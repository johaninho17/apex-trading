"""High-frequency local news event parsing and cache helpers.

This module is deliberately additive: it can ingest normalized feed items, classify
headlines with Ollama, persist parsed events, and build per-symbol event state that
other systems can consume without blocking the trading loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from functools import partial
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from core.ollama import ollama_url
from . import current_crypto_config, market_data, store
from .universe import select_trade_universe

logger = logging.getLogger(__name__)

_SOURCE_RELIABILITY = {
    "binance": 1.00,
    "binance_exchangeinfo": 1.00,
    "cryptopanic": 0.80,
    "coinmarketcal": 0.88,
    "rss": 0.65,
    "manual": 0.90,
}

_SOURCE_TIERS = {
    "binance": "official_exchange",
    "binance_exchangeinfo": "official_exchange",
    "cryptopanic": "structured_aggregator",
    "coinmarketcal": "structured_event",
    "rss": "editorial_rss",
    "manual": "manual",
}

_SENTIMENT_SIGN = {
    "extreme_positive": 1.30,
    "positive": 1.00,
    "neutral": 0.0,
    "negative": -1.00,
    "extreme_negative": -1.30,
}

_BINANCE_CMS_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20"
_BINANCE_EXCHANGEINFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"
_COINGECKO_COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"
_COINMARKETCAL_API_URL = os.getenv("COINMARKETCAL_API_URL", "https://developers.coinmarketcal.com/v1/events")
_DEFAULT_RSS_FEEDS = [
    {
        "name": "coindesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "source_category": "general",
        "scope_hint": "market",
        "parse_mode": "rss",
    },
    {
        "name": "cointelegraph",
        "url": "https://cointelegraph.com/rss",
        "source_category": "general",
        "scope_hint": "market",
        "parse_mode": "rss",
    },
    {
        "name": "cointelegraph_altcoin",
        "url": "https://cointelegraph.com/rss/tag/altcoin",
        "source_category": "altcoin",
        "scope_hint": "sector",
        "parse_mode": "rss",
    },
    {
        "name": "cointelegraph_ethereum",
        "url": "https://cointelegraph.com/rss/tag/ethereum",
        "source_category": "ethereum",
        "scope_hint": "sector",
        "parse_mode": "rss",
    },
    {
        "name": "cointelegraph_regulation",
        "url": "https://cointelegraph.com/rss/tag/regulation",
        "source_category": "regulation",
        "scope_hint": "market",
        "parse_mode": "rss",
    },
    {
        "name": "cointelegraph_market_analysis",
        "url": "https://cointelegraph.com/rss/tag/market-analysis",
        "source_category": "market_analysis",
        "scope_hint": "market",
        "parse_mode": "rss",
    },
    {
        "name": "decrypt",
        "url": "https://decrypt.co/feed",
        "source_category": "general",
        "scope_hint": "market",
        "parse_mode": "rss",
    },
]

_ASSET_NAME_ALIASES = {
    "AAVE": ("AAVE",),
    "ADA": ("CARDANO",),
    "ARB": ("ARBITRUM",),
    "AVAX": ("AVALANCHE",),
    "BAT": ("BASIC ATTENTION TOKEN",),
    "BCH": ("BITCOIN CASH",),
    "BONK": ("BONK",),
    "BTC": ("BITCOIN",),
    "CRV": ("CURVE",),
    "DOGE": ("DOGECOIN",),
    "DOT": ("POLKADOT",),
    "ETH": ("ETHEREUM",),
    "FIL": ("FILECOIN",),
    "GRT": ("THE GRAPH",),
    "HYPE": ("HYPERLIQUID",),
    "LDO": ("LIDO",),
    "LINK": ("CHAINLINK",),
    "LTC": ("LITECOIN",),
    "ONDO": ("ONDO",),
    "PAXG": ("PAX GOLD",),
    "PEPE": ("PEPE",),
    "POL": ("POLYGON", "MATIC"),
    "RENDER": ("RENDER", "RNDR"),
    "SHIB": ("SHIBA INU",),
    "SKY": ("SKY",),
    "SOL": ("SOLANA",),
    "SUSHI": ("SUSHISWAP",),
    "TRUMP": ("TRUMP",),
    "UNI": ("UNISWAP",),
    "USDC": ("USD COIN",),
}

_TICKER_HINT_RE = re.compile(r"\(([A-Z0-9]{2,15})\)|\$([A-Z0-9]{2,15})")
_BINANCE_ANNOUNCEMENT_RE = re.compile(
    r'href="(?P<path>/en/support/announcement/[^"#?]+)"[^>]*>(?P<title>[^<]+)</a>',
    re.IGNORECASE,
)
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="(?:__APP_DATA|__NEXT_DATA__)"[^>]*>(?P<json>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TOKEN_HINT_RE = re.compile(r"\b(token|coin|memecoin|meme coin|project|protocol)\b", re.IGNORECASE)
_POLITICAL_TRUMP_RE = re.compile(r"\b(donald trump|president trump|white house|campaign|election|tariff)\b", re.IGNORECASE)
_MARKET_WIDE_RE = re.compile(r"\b(crypto market|crypto markets|digital assets|altcoin market|market-wide|broad market|risk-on|risk off)\b", re.IGNORECASE)
_ASSET_ALIAS_CACHE: Dict[str, Any] = {"ts_sec": 0.0, "items": []}


def _pair_symbol(base_asset: str) -> str:
    base = str(base_asset or "").strip().upper()
    if not base:
        return ""
    if "/" in base:
        return base
    return f"{base}/USD"


def _extract_base_asset_hint(*values: Any) -> str:
    for value in values:
        text = str(value or "").upper()
        if not text:
            continue
        match = _TICKER_HINT_RE.search(text)
        if match:
            return str(match.group(1) or match.group(2) or "").upper()
    return ""


def _extract_matching_asset_hint(candidates: Iterable[Any], *values: Any) -> str:
    texts = [str(value or "").upper() for value in values if str(value or "").strip()]
    if not texts:
        return ""
    blob = " ".join(texts)
    matches: List[str] = []
    for candidate in candidates:
        asset = str(candidate or "").strip().upper()
        if not asset or asset in matches:
            continue
        patterns = [asset, *list(_ASSET_NAME_ALIASES.get(asset, ()))]
        for pattern in patterns:
            normalized = str(pattern or "").strip().upper()
            if not normalized:
                continue
            if re.search(rf"(?<![A-Z0-9]){re.escape(normalized)}(?![A-Z0-9])", blob):
                matches.append(asset)
                break
    return matches[0] if len(matches) == 1 else ""


def _normalized_alias_text(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def _news_cfg() -> Dict[str, Any]:
    try:
        cfg = current_crypto_config(resolve_symbols=False)
    except Exception:
        cfg = current_crypto_config()
    return dict((cfg.get("news") or {}))


def _symbol_confidence_high_min(news_cfg: Optional[Dict[str, Any]] = None) -> float:
    cfg = dict(news_cfg or _news_cfg())
    return max(0.0, min(1.0, float(cfg.get("symbol_confidence_high_min", 0.80) or 0.80)))


def _symbol_confidence_medium_min(news_cfg: Optional[Dict[str, Any]] = None) -> float:
    cfg = dict(news_cfg or _news_cfg())
    return max(0.0, min(1.0, float(cfg.get("symbol_confidence_medium_min", 0.50) or 0.50)))


def _rss_feed_registry(news_cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cfg = dict(news_cfg or _news_cfg())
    raw_feeds = cfg.get("rss_feeds")
    feeds = raw_feeds if isinstance(raw_feeds, list) and raw_feeds else _DEFAULT_RSS_FEEDS
    normalized: List[Dict[str, Any]] = []
    for index, feed in enumerate(feeds):
        if not isinstance(feed, dict):
            continue
        url = str(feed.get("url", "") or "").strip()
        if not url:
            continue
        normalized.append(
            {
                "name": str(feed.get("name", f"feed_{index}") or f"feed_{index}").strip().lower(),
                "url": url,
                "source_category": str(feed.get("source_category", "general") or "general").strip().lower(),
                "scope_hint": str(feed.get("scope_hint", "market") or "market").strip().lower(),
                "parse_mode": str(feed.get("parse_mode", "rss") or "rss").strip().lower(),
                "enabled": bool(feed.get("enabled", True)),
            }
        )
    return [feed for feed in normalized if feed.get("enabled", True)]


def _asset_alias_items(max_age_sec: float = 900.0) -> List[Dict[str, Any]]:
    now_sec = time.time()
    cached = list(_ASSET_ALIAS_CACHE.get("items") or [])
    if cached and (now_sec - float(_ASSET_ALIAS_CACHE.get("ts_sec", 0.0) or 0.0)) <= float(max_age_sec):
        return cached
    items = store.list_asset_aliases_sync(limit=2000)
    _ASSET_ALIAS_CACHE["ts_sec"] = now_sec
    _ASSET_ALIAS_CACHE["items"] = list(items)
    return list(items)


def _asset_alias_candidates() -> List[Dict[str, Any]]:
    items = _asset_alias_items(max_age_sec=900.0)
    if items:
        return items
    sync_asset_aliases_from_assets_sync()
    return _asset_alias_items(max_age_sec=900.0)


def sync_asset_aliases_from_assets_sync(account_mode: str = "paper") -> int:
    try:
        assets = market_data.list_crypto_assets(limit=300, mode=account_mode)
    except Exception as exc:
        logger.debug("[news_pipeline] asset alias sync skipped: %s", exc)
        return 0
    now_ms = int(time.time() * 1000)
    count = 0
    for asset in assets:
        symbol = _pair_symbol(asset.get("symbol", ""))
        if not symbol:
            continue
        base = symbol.split("/")[0]
        name = str(asset.get("name", "") or symbol).strip()
        aliases = {
            base,
            _normalized_alias_text(base),
            _normalized_alias_text(name),
            *[_normalized_alias_text(alias) for alias in _ASSET_NAME_ALIASES.get(base, ())],
        }
        aliases.discard("")
        store.upsert_asset_alias_sync(
            base.lower(),
            symbol=symbol,
            name=name,
            aliases=sorted(aliases),
            websites=[],
            platforms={},
            updated_at=now_ms,
        )
        count += 1
    _ASSET_ALIAS_CACHE["ts_sec"] = 0.0
    return count


def sync_coingecko_metadata_sync(account_mode: str = "paper", *, force: bool = False) -> int:
    news_cfg = _news_cfg()
    if not bool(((news_cfg.get("sources") or {}).get("coingecko_metadata", False))):
        return 0
    now_ms = int(time.time() * 1000)
    existing = store.get_kv_values_sync("coingecko_metadata:")
    last_sync_ms = int(existing.get("coingecko_metadata:last_sync_ms") or 0)
    if not force and last_sync_ms > 0 and (now_ms - last_sync_ms) < 24 * 60 * 60 * 1000:
        return 0
    try:
        assets = market_data.list_crypto_assets(limit=300, mode=account_mode)
        response = httpx.get(
            _COINGECKO_COINS_LIST_URL,
            params={"include_platform": "false"},
            timeout=20.0,
            follow_redirects=True,
            headers={"accept": "application/json", "user-agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        payload = response.json() or []
    except Exception as exc:
        logger.warning("[news_pipeline] CoinGecko metadata sync failed: %s", exc)
        return 0
    candidates_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for item in payload if isinstance(payload, list) else []:
        symbol = str(item.get("symbol", "") or "").strip().upper()
        if not symbol:
            continue
        candidates_by_symbol.setdefault(symbol, []).append(dict(item))
    updated = 0
    for asset in assets:
        symbol = _pair_symbol(asset.get("symbol", ""))
        if not symbol:
            continue
        base = symbol.split("/")[0]
        name = str(asset.get("name", "") or symbol).strip()
        choices = candidates_by_symbol.get(base, [])
        match = next(
            (
                choice
                for choice in choices
                if _normalized_alias_text(choice.get("name")) == _normalized_alias_text(name)
            ),
            choices[0] if choices else None,
        )
        if not match:
            continue
        aliases = {
            base,
            _normalized_alias_text(base),
            _normalized_alias_text(name),
            _normalized_alias_text(match.get("name")),
            *[_normalized_alias_text(alias) for alias in _ASSET_NAME_ALIASES.get(base, ())],
        }
        aliases.discard("")
        store.upsert_asset_alias_sync(
            str(match.get("id") or base.lower()),
            symbol=symbol,
            name=name,
            aliases=sorted(aliases),
            websites=[],
            platforms={},
            updated_at=now_ms,
        )
        updated += 1
    if updated > 0:
        store.save_kv_value_sync("coingecko_metadata:last_sync_ms", str(now_ms))
        _ASSET_ALIAS_CACHE["ts_sec"] = 0.0
    return updated


def _choose_asset_candidate(
    requested_assets: List[str],
    title: str,
    body: str,
    *,
    parsed_ticker: str = "",
    source: str = "",
) -> Dict[str, Any]:
    title_upper = str(title or "").upper()
    body_upper = str(body or "").upper()
    blob = f"{title_upper} {body_upper}".strip()
    candidates = _asset_alias_candidates()
    match_scores: Dict[str, Dict[str, Any]] = {}

    def _note(symbol: str, canonical_asset_id: str, method: str, confidence: float) -> None:
        normalized_symbol = _pair_symbol(symbol)
        if not normalized_symbol:
            return
        existing = match_scores.get(normalized_symbol, {})
        if confidence >= float(existing.get("confidence", 0.0) or 0.0):
            match_scores[normalized_symbol] = {
                "symbol": normalized_symbol,
                "base_asset": normalized_symbol.split("/")[0],
                "canonical_asset_id": canonical_asset_id,
                "method": method,
                "confidence": confidence,
            }

    contextual_requested_match = _extract_matching_asset_hint(requested_assets, title, body)
    if contextual_requested_match:
        return {
            "symbol": _pair_symbol(contextual_requested_match),
            "base_asset": contextual_requested_match,
            "canonical_asset_id": contextual_requested_match.lower(),
            "event_scope": "symbol",
            "confidence": 0.91,
            "method": "requested_context_alias",
        }

    for asset in requested_assets:
        base = str(asset or "").strip().upper()
        if not base:
            continue
        _note(base, base.lower(), "requested_asset", 0.93)
    if parsed_ticker:
        _note(parsed_ticker, str(parsed_ticker).strip().lower(), "parsed_ticker", 0.90)

    for item in candidates:
        symbol = _pair_symbol(item.get("symbol", ""))
        canonical_asset_id = str(item.get("canonical_asset_id") or symbol.split("/")[0].lower())
        aliases = [
            _normalized_alias_text(symbol.split("/")[0]),
            _normalized_alias_text(item.get("name")),
            *[_normalized_alias_text(alias) for alias in (item.get("aliases") or [])],
        ]
        seen_aliases: set[str] = set()
        for alias in aliases:
            if not alias or alias in seen_aliases:
                continue
            seen_aliases.add(alias)
            if re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", blob):
                confidence = 0.88 if len(alias) > 3 else 0.78
                _note(symbol, canonical_asset_id, "exact_alias", confidence)

    if "TRUMP" in blob and _POLITICAL_TRUMP_RE.search(blob) and not _TOKEN_HINT_RE.search(blob):
        return {
            "symbol": "",
            "base_asset": "",
            "canonical_asset_id": "",
            "event_scope": "market",
            "confidence": 0.30,
            "method": "ambiguous_macro_trump",
        }

    if len(match_scores) == 1:
        selected = next(iter(match_scores.values()))
        return {
            **selected,
            "event_scope": "symbol",
        }

    if len(match_scores) > 1:
        if requested_assets:
            exact_requested = [row for row in match_scores.values() if row.get("base_asset") in requested_assets]
            if len(exact_requested) == 1:
                return {
                    **exact_requested[0],
                    "event_scope": "symbol",
                }
        return {
            "symbol": "",
            "base_asset": "",
            "canonical_asset_id": "",
            "event_scope": "sector",
            "confidence": 0.40,
            "method": "ambiguous_multi_asset",
        }

    if _MARKET_WIDE_RE.search(blob):
        return {
            "symbol": "",
            "base_asset": "",
            "canonical_asset_id": "",
            "event_scope": "market",
            "confidence": 0.25,
            "method": "market_wide_text",
        }

    return {
        "symbol": "",
        "base_asset": "",
        "canonical_asset_id": "",
        "event_scope": "unknown",
        "confidence": 0.20,
        "method": "no_match",
    }


def _event_id(source: str, external_id: str, headline: str, url: str) -> str:
    if external_id:
        return f"{source}:{external_id}"
    digest = hashlib.sha1(f"{source}|{headline}|{url}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{source}:{digest}"



def _deterministic_parse(item: Dict[str, Any]) -> Dict[str, Any]:
    title = str(item.get("title", "") or "")
    body = str(item.get("body", "") or "")
    blob = f"{title} {body}".upper()
    base_asset = str(item.get("base_asset", "") or "").strip().upper()
    requested_assets = [
        str(asset or "").strip().upper()
        for asset in (item.get("requested_base_assets") or [])
        if str(asset or "").strip()
    ]

    if not base_asset:
        currencies = item.get("currencies") or []
        if currencies:
            base_asset = str(currencies[0]).strip().upper()
    if not base_asset and requested_assets:
        base_asset = _extract_matching_asset_hint(requested_assets, title, body)
    if not base_asset:
        base_asset = _extract_base_asset_hint(title, body)

    parsed = {
        "ticker": base_asset,
        "asset_scope": [base_asset] if base_asset else [],
        "event_type": "headline",
        "sentiment": "neutral",
        "impact_score": 0,
        "confidence": 0,
        "urgency": "medium",
        "ttl_minutes": 180,
        "trade_bias": "watch_only",
        "reason": "No deterministic event classification matched.",
    }

    if "BINANCE WILL LIST" in blob or "WILL LIST" in blob:
        parsed.update({
            "event_type": "listing",
            "sentiment": "extreme_positive",
            "impact_score": 92,
            "confidence": 97,
            "urgency": "critical",
            "ttl_minutes": 240,
            "trade_bias": "buy_bias",
            "reason": "Exchange listing detected from a structured Binance announcement.",
        })
    elif "DELIST" in blob:
        parsed.update({
            "event_type": "delisting",
            "sentiment": "extreme_negative",
            "impact_score": 96,
            "confidence": 96,
            "urgency": "critical",
            "ttl_minutes": 360,
            "trade_bias": "hard_veto",
            "reason": "Exchange delisting detected from a structured announcement.",
        })
    elif "HACK" in blob or "EXPLOIT" in blob or "INSOLVENC" in blob:
        parsed.update({
            "event_type": "exploit",
            "sentiment": "extreme_negative",
            "impact_score": 90,
            "confidence": 82,
            "urgency": "critical",
            "ttl_minutes": 720,
            "trade_bias": "hard_veto",
            "reason": "Security or solvency crisis keywords detected.",
        })
    elif "ETF" in blob or "APPROVAL" in blob:
        parsed.update({
            "event_type": "etf",
            "sentiment": "positive",
            "impact_score": 72,
            "confidence": 74,
            "urgency": "high",
            "ttl_minutes": 360,
            "trade_bias": "buy_bias",
            "reason": "ETF-related catalyst detected.",
        })
    elif "UPGRADE" in blob or "MAINNET" in blob or "VALIDATOR" in blob:
        parsed.update({
            "event_type": "network_upgrade",
            "sentiment": "positive",
            "impact_score": 62,
            "confidence": 68,
            "urgency": "high",
            "ttl_minutes": 240,
            "trade_bias": "buy_bias",
            "reason": "Protocol or network improvement detected.",
        })

    return parsed



def classify_news_item_with_ollama(item: Dict[str, Any], model_name: str = "qwen3:8b") -> Dict[str, Any]:
    deterministic = _deterministic_parse(item)
    # If a structured source already gave us a strong high-confidence answer, keep it deterministic.
    if deterministic.get("confidence", 0) >= 95:
        return deterministic

    title = str(item.get("title", "") or "")
    body = str(item.get("body", "") or "")
    base_asset = str(item.get("base_asset", "") or "")
    source = str(item.get("source", "news") or "news")

    prompt = f"""Classify this crypto news item for trading. Return strict JSON only.

Source: {source}
Base asset hint: {base_asset or 'unknown'}
Headline: {title}
Body: {body[:1500]}

Return JSON with this exact shape:
{{
  "ticker": "BASE_ASSET_OR_EMPTY",
  "asset_scope": ["OPTIONAL_ASSET_LIST"],
  "event_type": "listing|delisting|exploit|etf|regulation|network_upgrade|partnership|unlock|headline",
  "sentiment": "extreme_positive|positive|neutral|negative|extreme_negative",
  "impact_score": 0,
  "confidence": 0,
  "urgency": "low|medium|high|critical",
  "ttl_minutes": 180,
  "trade_bias": "buy_bias|sell_bias|hard_veto|watch_only",
  "reason": "one short sentence"
}}
"""

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
        },
    }

    try:
        response = httpx.post(ollama_url('/api/generate'), json=payload, timeout=20.0)
        response.raise_for_status()
        raw_text = str(response.json().get("response", "") or "").strip()
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1 and end >= start:
            raw_text = raw_text[start:end + 1]
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            return deterministic
        merged = dict(deterministic)
        merged.update(parsed)
        return merged
    except Exception as exc:
        logger.warning("[news_pipeline] Ollama classification failed: %s", exc)
        return deterministic



def _news_provider_status() -> Dict[str, Any]:
    return {
        "binance": dict(_BINANCE_POLL_META),
        "cryptopanic": dict(_CRYPTOPANIC_POLL_META),
        "rss": dict(_RSS_POLL_META),
        "coinmarketcal": dict(_COINMARKETCAL_POLL_META),
    }


def _compute_symbol_event_state(
    symbol: str,
    events: Iterable[Dict[str, Any]],
    now_ms: Optional[int] = None,
    *,
    existing_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now_ms = int(now_ms or (time.time() * 1000))
    news_cfg = _news_cfg()
    symbol_confidence_high_min = _symbol_confidence_high_min(news_cfg)
    pair = _pair_symbol(symbol)
    net = 0.0
    top_event_id = ""
    top_event_type = ""
    top_event_score = 0.0
    event_count = 0
    hard_veto = False
    last_fresh_event_at = 0

    for event in events:
        event_scope = str(event.get("event_scope", "") or "").strip().lower()
        attribution_confidence = float(event.get("attribution_confidence", 0.0) or 0.0)
        if event_scope and event_scope != "symbol":
            continue
        if attribution_confidence and attribution_confidence < symbol_confidence_high_min:
            continue
        expires_at = int(event.get("expires_at", 0) or 0)
        published_at = int(event.get("published_at", now_ms) or now_ms)
        if expires_at and expires_at < now_ms:
            continue
        event_count += 1
        last_fresh_event_at = max(last_fresh_event_at, published_at)
        sentiment = str(event.get("sentiment", "neutral") or "neutral").lower()
        sign = _SENTIMENT_SIGN.get(sentiment, 0.0)
        impact = float(event.get("impact_score", 0.0) or 0.0)
        ttl_min = max(1, int(event.get("ttl_minutes", 180) or 180))
        age_min = max(0.0, (now_ms - published_at) / 60000.0)
        decay = math.exp(-age_min / ttl_min)
        reliability = float(_SOURCE_RELIABILITY.get(str(event.get("source", "news") or "news").lower(), 0.65))
        contribution = sign * impact * decay * reliability
        net += contribution
        if impact >= top_event_score:
            top_event_score = impact
            top_event_id = str(event.get("id", ""))
            top_event_type = str(event.get("event_type", ""))
        if str(event.get("trade_bias", "")).lower() == "hard_veto" or sentiment == "extreme_negative":
            hard_veto = True

    net = round(net / 4.0, 2)
    event_bias = "bullish" if net > 5 else "bearish" if net < -5 else "neutral"
    golden_trade_flag = top_event_score >= 85.0 and not hard_veto and net > 10
    provider_status = _news_provider_status()
    provider_health = {
        str(name): str((meta or {}).get("status", "idle") or "idle").strip().lower()
        for name, meta in provider_status.items()
    }
    if event_count > 0:
        news_context_state = "fresh_active_events"
        news_context_complete = True
    elif any(status == "ok" for status in provider_health.values()):
        news_context_state = "fresh_no_active_events"
        news_context_complete = True
    elif existing_state and int(existing_state.get("updated_at", 0) or 0) > 0:
        news_context_state = "stale"
        news_context_complete = False
    else:
        news_context_state = "unavailable"
        news_context_complete = False
    return {
        "symbol": pair,
        "net_event_score": net,
        "event_bias": event_bias,
        "hard_veto": hard_veto,
        "golden_trade_flag": golden_trade_flag,
        "top_event_id": top_event_id,
        "top_event_type": top_event_type,
        "top_event_score": round(top_event_score, 2),
        "event_count_active": event_count,
        "updated_at": now_ms,
        "news_context_state": news_context_state,
        "news_context_complete": news_context_complete,
        "news_last_fresh_at": int(last_fresh_event_at or 0),
        "news_provider_status": provider_health,
        "news_sources": [name for name, status in provider_health.items() if status],
    }



def refresh_symbol_event_state_sync(symbol: str, since_ms: int = 0) -> Dict[str, Any]:
    pair = _pair_symbol(symbol)
    events = store.get_recent_news_events_sync(limit=100, symbol=pair, since_ms=since_ms, active_only=True)
    existing_state = store.get_symbol_event_state_sync(pair)
    state = _compute_symbol_event_state(pair, events, existing_state=existing_state)
    store.upsert_symbol_event_state_sync(pair, state)
    return state


def refresh_symbol_event_states_sync(symbols: Iterable[str], since_ms: int = 0, limit: int = 120) -> int:
    count = 0
    for raw_symbol in list(symbols or [])[: max(1, min(int(limit or 120), 300))]:
        symbol = _pair_symbol(raw_symbol)
        if not symbol:
            continue
        try:
            refresh_symbol_event_state_sync(symbol, since_ms=since_ms)
            count += 1
        except Exception:
            continue
    return count



def ingest_normalized_news_items_sync(
    items: Iterable[Dict[str, Any]],
    source: str,
    model_name: str = "qwen3:8b",
    tradable_symbols: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    news_cfg = _news_cfg()
    high_confidence_min = _symbol_confidence_high_min(news_cfg)
    tradable = {str(s).upper() for s in (tradable_symbols or [])}
    account_mode = str(current_crypto_config(resolve_symbols=False).get("account_mode", "paper") or "paper")
    if not _asset_alias_items(max_age_sec=6 * 60 * 60):
        sync_asset_aliases_from_assets_sync(account_mode)
    if bool(((news_cfg.get("sources") or {}).get("coingecko_metadata", False))):
        sync_coingecko_metadata_sync(account_mode)
    persisted: List[Dict[str, Any]] = []

    for raw in items:
        title = str(raw.get("title", "") or "").strip()
        if not title:
            continue
        published_at = int(raw.get("published_at", now_ms) or now_ms)
        normalized = {
            "source": source,
            "external_id": str(raw.get("external_id", "") or ""),
            "published_at": published_at,
            "title": title,
            "body": str(raw.get("body", "") or ""),
            "url": str(raw.get("url", "") or ""),
            "currencies": list(raw.get("currencies") or []),
            "base_asset": str(raw.get("base_asset", "") or ""),
            "requested_base_assets": list(raw.get("requested_base_assets") or []),
            "category": str(raw.get("category", source) or source),
            "scope_hint": str(raw.get("scope_hint", "market") or "market"),
        }
        parsed = classify_news_item_with_ollama(normalized, model_name=model_name)
        requested_assets = [
            str(asset or "").strip().upper()
            for asset in [
                *(normalized.get("requested_base_assets") or []),
                *(normalized.get("currencies") or []),
                normalized.get("base_asset") or "",
            ]
            if str(asset or "").strip()
        ]
        attribution = _choose_asset_candidate(
            requested_assets,
            title,
            normalized.get("body", ""),
            parsed_ticker=str(parsed.get("ticker", "") or normalized.get("base_asset", "") or "").strip().upper(),
            source=source,
        )
        base_asset = str(attribution.get("base_asset", "") or "").strip().upper()
        symbol = _pair_symbol(attribution.get("symbol", "") or base_asset)
        if tradable and symbol and symbol.upper() not in tradable and base_asset not in tradable:
            continue
        event_scope = str(attribution.get("event_scope", normalized.get("scope_hint", "market")) or "market").strip().lower()
        attribution_confidence = float(attribution.get("confidence", 0.0) or 0.0)
        attribution_method = str(attribution.get("method", "none") or "none").strip().lower()
        if event_scope != "symbol" or attribution_confidence < high_confidence_min:
            symbol = symbol if event_scope == "symbol" and attribution_confidence >= _symbol_confidence_medium_min(news_cfg) else ""
        ttl_minutes = int(parsed.get("ttl_minutes", 180) or 180)
        source_category = str(normalized.get("category", source) or source).strip().lower()
        source_tier = _SOURCE_TIERS.get(str(source or "rss").strip().lower(), "editorial_rss")
        event = {
            "id": _event_id(source, normalized["external_id"], title, normalized["url"]),
            "source": source,
            "external_id": normalized["external_id"],
            "symbol": symbol,
            "base_asset": base_asset,
            "canonical_asset_id": str(attribution.get("canonical_asset_id", "") or base_asset.lower()),
            "event_type": str(parsed.get("event_type", "headline")),
            "headline": title,
            "url": normalized["url"],
            "published_at": published_at,
            "ingested_at": now_ms,
            "sentiment": str(parsed.get("sentiment", "neutral")),
            "impact_score": float(parsed.get("impact_score", 0.0) or 0.0),
            "confidence": float(parsed.get("confidence", 0.0) or 0.0),
            "urgency": str(parsed.get("urgency", "medium")),
            "ttl_minutes": ttl_minutes,
            "expires_at": published_at + (ttl_minutes * 60000),
            "trade_bias": str(parsed.get("trade_bias", "watch_only")),
            "parsed_by": "ollama" if float(parsed.get("confidence", 0.0) or 0.0) < 95 else "deterministic",
            "parser_model": model_name,
            "reason": str(parsed.get("reason", "")),
            "event_scope": event_scope,
            "attribution_confidence": attribution_confidence,
            "attribution_method": attribution_method,
            "source_tier": source_tier,
            "source_category": source_category,
            "payload": {
                "normalized": normalized,
                "parsed": parsed,
                "attribution": attribution,
            },
        }
        store.record_news_event_sync(event)
        if symbol:
            state = refresh_symbol_event_state_sync(symbol)
            event["symbol_event_state"] = state
        persisted.append(event)

    return persisted



def get_symbol_event_state(symbol: str) -> Dict[str, Any]:
    return store.get_symbol_event_state_sync(_pair_symbol(symbol))



def get_recent_news_summary(symbol: str = "", limit: int = 5, since_ms: int = 0) -> List[Dict[str, Any]]:
    return store.get_recent_news_events_sync(limit=limit, symbol=_pair_symbol(symbol) if symbol else "", since_ms=since_ms, active_only=False)


_last_binance_poll_sec = 0.0
_last_crypto_poll_sec = 0.0
_cryptopanic_backoff_until_sec = 0.0
_cryptopanic_dynamic_asset_limit = 0
_news_poll_primed = False
_BINANCE_POLL_META: Dict[str, Any] = {"status": "idle", "info": ""}
_CRYPTOPANIC_POLL_META: Dict[str, Any] = {"status": "idle", "info": ""}
_RSS_POLL_META: Dict[str, Any] = {"status": "idle", "info": ""}
_COINMARKETCAL_POLL_META: Dict[str, Any] = {"status": "idle", "info": ""}


class _CryptoPanicRateLimited(RuntimeError):
    def __init__(self, message: str = "", *, info: str = "", retry_after_sec: int = 0):
        super().__init__(message or info or "CryptoPanic rate-limited")
        self.info = str(info or message or "").strip()
        self.retry_after_sec = max(0, int(retry_after_sec or 0))


class _CryptoPanicQuotaExceeded(_CryptoPanicRateLimited):
    pass


def _set_poll_meta(provider: str, *, status: str, info: str = "", **extra: Any) -> None:
    normalized = str(provider or "").strip().lower()
    if normalized == "binance":
        target = _BINANCE_POLL_META
    elif normalized == "cryptopanic":
        target = _CRYPTOPANIC_POLL_META
    elif normalized == "rss":
        target = _RSS_POLL_META
    else:
        target = _COINMARKETCAL_POLL_META
    target.clear()
    target.update({
        "status": str(status or "idle"),
        "info": str(info or ""),
        **dict(extra or {}),
    })


def _to_epoch_ms(value: Any) -> int:
    if value is None or value == "":
        return int(time.time() * 1000)
    if isinstance(value, (int, float)):
        raw = float(value)
        return int(raw if raw > 10_000_000_000 else raw * 1000)
    text = str(value).strip()
    if not text:
        return int(time.time() * 1000)
    try:
        return int(text) if text.isdigit() else int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return int(time.time() * 1000)


def _walk_article_nodes(node: Any, out: List[Dict[str, Any]]) -> None:
    if isinstance(node, dict):
        title = node.get("title") or node.get("name")
        if title and any(key in node for key in ("url", "code", "id", "articleId", "external_id")):
            out.append(node)
        for value in node.values():
            _walk_article_nodes(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_article_nodes(item, out)


def _normalize_binance_url(url_or_path: str) -> str:
    value = str(url_or_path or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/"):
        return f"https://www.binance.com{value}"
    return f"https://www.binance.com/en/support/announcement/{value}"


def _extract_binance_articles_from_html(html: str) -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for match in _BINANCE_ANNOUNCEMENT_RE.finditer(html or ""):
        path = str(match.group("path") or "").strip()
        title = re.sub(r"\s+", " ", str(match.group("title") or "")).strip()
        if not path or not title:
            continue
        url = _normalize_binance_url(path)
        if url in seen:
            continue
        seen.add(url)
        articles.append({
            "url": url,
            "title": title,
        })

    for match in _NEXT_DATA_RE.finditer(html or ""):
        raw_json = str(match.group("json") or "").strip()
        if not raw_json:
            continue
        try:
            payload = json.loads(raw_json)
        except Exception:
            continue
        raw_articles: List[Dict[str, Any]] = []
        _walk_article_nodes(payload, raw_articles)
        for article in raw_articles:
            title = str(article.get("title") or article.get("name") or "").strip()
            url = _normalize_binance_url(
                article.get("url")
                or article.get("webLink")
                or article.get("web_link")
                or article.get("code")
                or ""
            )
            if not title or not url or url in seen:
                continue
            seen.add(url)
            articles.append({
                "url": url,
                "title": title,
                "published_at": _to_epoch_ms(
                    article.get("releaseDate")
                    or article.get("publishDate")
                    or article.get("published_at")
                    or article.get("date")
                ),
                "body": str(article.get("body") or article.get("content") or ""),
                "external_id": str(article.get("code") or article.get("articleId") or article.get("id") or ""),
            })

    return articles


def _is_binance_waf_challenge(response: httpx.Response) -> bool:
    try:
        waf_action = str(response.headers.get("x-amzn-waf-action", "") or "").strip().lower()
    except Exception:
        waf_action = ""
    if waf_action == "challenge":
        return True
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code == 202:
        return True
    text = str(getattr(response, "text", "") or "")
    lowered = text.lower()
    return "/cdn-cgi/challenge" in lowered or "just a moment" in lowered or "cloudfront" in lowered and "challenge" in lowered


def _fetch_binance_cms_articles_sync(limit: int = 10) -> List[Dict[str, Any]]:
    response = httpx.get(_BINANCE_CMS_URL, timeout=20.0, follow_redirects=True, headers={"user-agent": "Mozilla/5.0", "accept": "application/json"})
    response.raise_for_status()
    payload = response.json() or {}
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for catalog in list((payload.get("data") or {}).get("catalogs") or []):
        for article in list(catalog.get("articles") or []):
            title = str(article.get("title") or "").strip()
            code = str(article.get("code") or article.get("id") or "").strip()
            if not title or not code:
                continue
            url = _normalize_binance_url(code)
            dedupe_key = f"{code}|{title}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append({
                "external_id": code,
                "published_at": _to_epoch_ms(article.get("releaseDate") or article.get("publishDate")),
                "title": title,
                "url": url,
                "body": "",
                "base_asset": _extract_base_asset_hint(title),
                "currencies": [],
                "category": "cms",
            })
            if len(normalized) >= limit:
                return normalized
    return normalized


def _fetch_binance_exchangeinfo_updates_sync(limit: int = 10) -> List[Dict[str, Any]]:
    response = httpx.get(_BINANCE_EXCHANGEINFO_URL, timeout=20.0, follow_redirects=True, headers={"user-agent": "Mozilla/5.0", "accept": "application/json"})
    response.raise_for_status()
    payload = response.json() or {}
    current_symbols = sorted({
        str(item.get("baseAsset") or "").strip().upper()
        for item in list(payload.get("symbols") or [])
        if str(item.get("status") or "").strip().upper() == "TRADING"
        and str(item.get("quoteAsset") or "").strip().upper() in {"USDT", "USDC", "FDUSD", "BTC", "ETH"}
        and str(item.get("baseAsset") or "").strip()
    })
    existing = store.get_kv_values_sync("binance_exchangeinfo:")
    previous = set()
    raw_previous = str(existing.get("binance_exchangeinfo:symbols") or "").strip()
    if raw_previous:
        try:
            previous = set(json.loads(raw_previous))
        except Exception:
            previous = set()
    store.save_kv_value_sync("binance_exchangeinfo:symbols", json.dumps(current_symbols, separators=(",", ":"), ensure_ascii=True))
    if not previous:
        return []
    new_symbols = [symbol for symbol in current_symbols if symbol not in previous][: max(1, min(int(limit), 50))]
    now_ms = int(time.time() * 1000)
    return [
        {
            "external_id": f"exchangeinfo:{symbol}:{now_ms}",
            "published_at": now_ms,
            "title": f"Binance exchangeInfo now includes {symbol}",
            "url": _BINANCE_EXCHANGEINFO_URL,
            "body": f"exchangeInfo reported {symbol} as a trading base asset on Binance.",
            "base_asset": symbol,
            "currencies": [symbol],
            "category": "exchangeinfo",
        }
        for symbol in new_symbols
    ]


def fetch_binance_announcements_sync(limit: int = 10) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    errors: List[str] = []
    for label, fetcher in (
        ("cms", _fetch_binance_cms_articles_sync),
        ("exchangeinfo", _fetch_binance_exchangeinfo_updates_sync),
    ):
        try:
            items = fetcher(limit=max(limit, 10))
        except Exception as exc:
            logger.warning("[news_pipeline] Binance %s fetch failed: %s", label, exc)
            errors.append(f"{label}: {exc}")
            continue
        normalized.extend(items)
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in normalized:
        dedupe_key = f"{item.get('external_id')}|{item.get('title')}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    if deduped:
        _set_poll_meta("binance", status="ok", info="")
    elif errors:
        _set_poll_meta("binance", status="error", info="; ".join(errors))
    else:
        _set_poll_meta("binance", status="ok", info="")
    return deduped


def _parse_retry_after_seconds(exc: Exception) -> int:
    try:
        response = getattr(exc, "response", None)
        if response is None:
            return 0
        header = str((response.headers or {}).get("Retry-After") or "").strip()
        if not header:
            return 0
        return max(0, int(float(header)))
    except Exception:
        return 0


def _parse_cryptopanic_info(response: Optional[httpx.Response]) -> str:
    if response is None:
        return ""
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("info") or payload.get("error") or "").strip()


def _prime_news_poll_timers(news_cfg: Dict[str, Any]) -> None:
    global _last_binance_poll_sec, _last_crypto_poll_sec, _news_poll_primed
    if _news_poll_primed:
        return
    now = time.time()
    _last_binance_poll_sec = now
    _last_crypto_poll_sec = now
    _news_poll_primed = True


def _adjust_cryptopanic_dynamic_limit(configured_limit: int, *, rate_limited: bool) -> int:
    global _cryptopanic_dynamic_asset_limit
    configured_limit = max(1, int(configured_limit or 1))
    if _cryptopanic_dynamic_asset_limit <= 0:
        _cryptopanic_dynamic_asset_limit = configured_limit
    if rate_limited:
        _cryptopanic_dynamic_asset_limit = max(3, min(_cryptopanic_dynamic_asset_limit, configured_limit) - 2)
    else:
        _cryptopanic_dynamic_asset_limit = min(configured_limit, max(_cryptopanic_dynamic_asset_limit, 3) + 1)
    return int(_cryptopanic_dynamic_asset_limit)


def _select_cryptopanic_assets(
    symbols: List[str],
    universe_state: Optional[Dict[str, Any]],
    news_cfg: Dict[str, Any],
) -> List[str]:
    configured_limit = max(3, min(20, int(news_cfg.get("cryptopanic_asset_limit", 12) or 12)))
    dynamic_limit = _cryptopanic_dynamic_asset_limit if _cryptopanic_dynamic_asset_limit > 0 else configured_limit
    asset_limit = max(3, min(configured_limit, dynamic_limit))
    selected: List[str] = []

    def _push_symbol(raw: Any) -> None:
        symbol = str(raw or "").strip().upper()
        if not symbol:
            return
        base = symbol.split("/")[0]
        if not base or base in selected:
            return
        selected.append(base)

    for symbol in (universe_state or {}).get("forced_symbols", []) or []:
        _push_symbol(symbol)
    for row in (universe_state or {}).get("rankings", []) or []:
        if not bool(row.get("selected", False)):
            continue
        if str(row.get("bucket", "discovery")) != "discovery":
            continue
        _push_symbol(row.get("symbol", ""))
        if len(selected) >= asset_limit:
            break
    for symbol in symbols:
        _push_symbol(symbol)
        if len(selected) >= asset_limit:
            break
    return selected[:asset_limit]


def fetch_cryptopanic_posts_sync(base_assets: Optional[Iterable[str]] = None, limit: int = 10) -> List[Dict[str, Any]]:
    token = str(os.environ.get("CRYPTOPANIC_API_TOKEN", "") or "").strip()
    if not token:
        _set_poll_meta("cryptopanic", status="disabled", info="Missing CRYPTOPANIC_API_TOKEN")
        return []
    params = {
        "auth_token": token,
        "kind": str(os.environ.get("CRYPTOPANIC_KIND", "news") or "news").strip() or "news",
    }
    if str(os.environ.get("CRYPTOPANIC_PUBLIC", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}:
        params["public"] = "true"
    assets = [str(asset).strip().upper() for asset in (base_assets or []) if str(asset).strip()]
    if assets:
        params["currencies"] = ",".join(sorted(set(assets)))
    try:
        response = httpx.get("https://cryptopanic.com/api/developer/v2/posts/", params=params, timeout=20.0)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 429:
            info = _parse_cryptopanic_info(exc.response)
            retry_after_sec = _parse_retry_after_seconds(exc)
            normalized_info = info.lower()
            if "monthly quota exceeded" in normalized_info or "quota exceeded" in normalized_info or "upgrade your api plan" in normalized_info:
                raise _CryptoPanicQuotaExceeded(str(exc), info=info, retry_after_sec=retry_after_sec) from exc
            raise _CryptoPanicRateLimited(str(exc), info=info, retry_after_sec=retry_after_sec) from exc
        logger.warning("[news_pipeline] CryptoPanic fetch failed: %s", exc)
        _set_poll_meta("cryptopanic", status="error", info=str(exc))
        return []
    except Exception as exc:
        logger.warning("[news_pipeline] CryptoPanic fetch failed: %s", exc)
        _set_poll_meta("cryptopanic", status="error", info=str(exc))
        return []

    normalized: List[Dict[str, Any]] = []
    for item in list(payload.get("results") or [])[:limit]:
        currencies = []
        for currency in item.get("currencies") or []:
            if isinstance(currency, dict):
                code = currency.get("code") or currency.get("title") or currency.get("slug")
            else:
                code = currency
            if code:
                currencies.append(str(code).upper())
        base_asset = currencies[0] if currencies else ""
        normalized.append({
            "external_id": str(item.get("id") or item.get("slug") or item.get("title") or ""),
            "published_at": _to_epoch_ms(item.get("published_at") or item.get("created_at")),
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or item.get("domain") or ""),
            "body": str((item.get("metadata") or {}).get("description") or item.get("body") or ""),
            "currencies": currencies,
            "base_asset": base_asset or _extract_matching_asset_hint(assets, item.get("title") or "", (item.get("metadata") or {}).get("description") or item.get("body") or ""),
            "requested_base_assets": list(assets),
        })
    _set_poll_meta("cryptopanic", status="ok", info="", requested_assets=list(assets), result_count=len(normalized))
    return normalized


def fetch_rss_posts_sync(limit: int = 12) -> List[Dict[str, Any]]:
    news_cfg = _news_cfg()
    feeds = _rss_feed_registry(news_cfg)
    if not feeds:
        _set_poll_meta("rss", status="disabled", info="No RSS feeds configured")
        return []
    per_feed_limit = max(2, min(8, int(limit or 12)))
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    errors: List[str] = []
    for feed in feeds:
        source_name = str(feed.get("name", "rss") or "rss")
        url = str(feed.get("url", "") or "")
        try:
            response = httpx.get(url, timeout=20.0, follow_redirects=True, headers={"user-agent": "Mozilla/5.0", "accept": "application/rss+xml,application/xml,text/xml"})
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception as exc:
            logger.warning("[news_pipeline] RSS fetch failed for %s: %s", source_name, exc)
            errors.append(f"{source_name}: {exc}")
            continue
        items = root.findall(".//item")
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for item in items[:per_feed_limit]:
            title = str(item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link = str(item.findtext("link") or item.findtext("{http://www.w3.org/2005/Atom}link") or "").strip()
            if not link:
                link_node = item.find("{http://www.w3.org/2005/Atom}link")
                if link_node is not None:
                    link = str(link_node.attrib.get("href") or "").strip()
            description = str(item.findtext("description") or item.findtext("summary") or item.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()
            published = item.findtext("pubDate") or item.findtext("{http://www.w3.org/2005/Atom}updated") or item.findtext("{http://www.w3.org/2005/Atom}published")
            if not title:
                continue
            dedupe_key = f"{source_name}|{link}|{title}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(
                {
                    "external_id": dedupe_key,
                    "published_at": _to_epoch_ms(published),
                    "title": title,
                    "url": link,
                    "body": re.sub(r"<[^>]+>", " ", description),
                    "base_asset": _extract_base_asset_hint(title, description),
                    "currencies": [],
                    "category": str(feed.get("source_category", source_name) or source_name),
                    "scope_hint": str(feed.get("scope_hint", "market") or "market"),
                }
            )
            if len(normalized) >= max(1, int(limit or 12)):
                _set_poll_meta("rss", status="ok", info="", result_count=len(normalized), feed_count=len(feeds))
                return normalized
    if normalized:
        _set_poll_meta("rss", status="ok", info="", result_count=len(normalized), feed_count=len(feeds))
    elif errors:
        _set_poll_meta("rss", status="error", info="; ".join(errors), feed_count=len(feeds))
    else:
        _set_poll_meta("rss", status="ok", info="", result_count=0, feed_count=len(feeds))
    return normalized


def fetch_coinmarketcal_events_sync(base_assets: Optional[Iterable[str]] = None, limit: int = 10) -> List[Dict[str, Any]]:
    token = str(os.environ.get("COINMARKETCAL_API_TOKEN", "") or "").strip()
    if not token:
        _set_poll_meta("coinmarketcal", status="disabled", info="Missing COINMARKETCAL_API_TOKEN")
        return []
    params: Dict[str, Any] = {"max": max(1, min(int(limit or 10), 50))}
    assets = [str(asset or "").strip().upper() for asset in (base_assets or []) if str(asset or "").strip()]
    if assets:
        params["symbols"] = ",".join(sorted(set(assets)))
    headers = {
        "accept": "application/json",
        "user-agent": "Mozilla/5.0",
        "x-api-key": token,
        "Authorization": f"Bearer {token}",
    }
    try:
        response = httpx.get(_COINMARKETCAL_API_URL, params=params, headers=headers, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
        payload = response.json() or {}
    except Exception as exc:
        logger.warning("[news_pipeline] CoinMarketCal fetch failed: %s", exc)
        _set_poll_meta("coinmarketcal", status="error", info=str(exc))
        return []

    rows = []
    if isinstance(payload, dict):
        for key in ("body", "results", "events", "data"):
            if isinstance(payload.get(key), list):
                rows = list(payload.get(key) or [])
                break
    elif isinstance(payload, list):
        rows = list(payload)
    normalized: List[Dict[str, Any]] = []
    for item in rows[: max(1, min(int(limit or 10), 50))]:
        currencies = []
        for currency in item.get("coins") or item.get("currencies") or []:
            if isinstance(currency, dict):
                code = currency.get("symbol") or currency.get("code") or currency.get("name")
            else:
                code = currency
            if code:
                currencies.append(str(code).strip().upper())
        title = str(item.get("title") or item.get("event") or item.get("name") or "").strip()
        if not title:
            continue
        normalized.append(
            {
                "external_id": str(item.get("id") or item.get("slug") or title),
                "published_at": _to_epoch_ms(item.get("date_event") or item.get("published_at") or item.get("created_at")),
                "title": title,
                "url": str(item.get("url") or item.get("source") or ""),
                "body": str(item.get("description") or item.get("body") or ""),
                "currencies": currencies,
                "base_asset": currencies[0] if currencies else "",
                "requested_base_assets": list(assets),
                "category": str(item.get("category") or "event"),
                "scope_hint": "symbol" if currencies else "market",
            }
        )
    _set_poll_meta("coinmarketcal", status="ok", info="", requested_assets=list(assets), result_count=len(normalized))
    return normalized


def run_news_poll_cycle_sync() -> Dict[str, Any]:
    global _last_binance_poll_sec, _last_crypto_poll_sec, _cryptopanic_backoff_until_sec

    cfg = current_crypto_config()
    news_cfg = cfg.get("news", {}) or {}
    sources = news_cfg.get("sources", {}) or {}
    if not bool(news_cfg.get("enabled", True)):
        return {"enabled": False}

    credential_mode = str(cfg.get("account_mode", "paper") or "paper")
    try:
        universe_state = select_trade_universe(cfg, mode=credential_mode)
        symbols = [
            str(row.get("symbol") or "").strip().upper()
            for row in (universe_state.get("rankings", []) or [])
            if str(row.get("symbol") or "").strip()
        ]
    except Exception:
        universe_state = {}
        symbols = [str(s).strip().upper() for s in cfg.get("symbols", []) if str(s).strip()]
    base_assets = _select_cryptopanic_assets(symbols, universe_state, news_cfg)
    tradable = symbols if bool(news_cfg.get("watchlist_only", False)) else None
    model_name = str(cfg.get("ollama_model", "qwen3:8b") or "qwen3:8b")
    now = time.time()
    _prime_news_poll_timers(news_cfg)
    if bool(sources.get("coingecko_metadata")):
        sync_coingecko_metadata_sync(credential_mode)
    else:
        sync_asset_aliases_from_assets_sync(credential_mode)
    configured_asset_limit = max(1, int(news_cfg.get("cryptopanic_asset_limit", 12) or 12))
    results = {"enabled": True, "binance": 0, "cryptopanic": 0, "rss": 0, "coinmarketcal": 0}

    binance_interval = max(5, int(news_cfg.get("binance_poll_sec", 10) or 10))
    if sources.get("binance") and (now - _last_binance_poll_sec) >= binance_interval:
        items = fetch_binance_announcements_sync(limit=10)
        if items:
            persisted = ingest_normalized_news_items_sync(items, source="binance", model_name=model_name, tradable_symbols=None)
            results["binance"] = len(persisted)
        _last_binance_poll_sec = now
    if _BINANCE_POLL_META:
        results["binance_status"] = str(_BINANCE_POLL_META.get("status", "idle") or "idle")
        if _BINANCE_POLL_META.get("info"):
            results["binance_info"] = str(_BINANCE_POLL_META.get("info") or "")

    if sources.get("rss"):
        try:
            rss_items = fetch_rss_posts_sync(limit=12)
        except Exception as exc:
            logger.warning("[news_pipeline] RSS poll failed: %s", exc)
            rss_items = []
        if rss_items:
            persisted = ingest_normalized_news_items_sync(rss_items, source="rss", model_name=model_name, tradable_symbols=tradable)
            results["rss"] = len(persisted)
    if _RSS_POLL_META:
        results["rss_status"] = str(_RSS_POLL_META.get("status", "idle") or "idle")
        if _RSS_POLL_META.get("info"):
            results["rss_info"] = str(_RSS_POLL_META.get("info") or "")

    if sources.get("coinmarketcal"):
        try:
            cmc_items = fetch_coinmarketcal_events_sync(base_assets=base_assets, limit=10)
        except Exception as exc:
            logger.warning("[news_pipeline] CoinMarketCal poll failed: %s", exc)
            cmc_items = []
        if cmc_items:
            persisted = ingest_normalized_news_items_sync(cmc_items, source="coinmarketcal", model_name=model_name, tradable_symbols=tradable)
            results["coinmarketcal"] = len(persisted)
    if _COINMARKETCAL_POLL_META:
        results["coinmarketcal_status"] = str(_COINMARKETCAL_POLL_META.get("status", "idle") or "idle")
        if _COINMARKETCAL_POLL_META.get("info"):
            results["coinmarketcal_info"] = str(_COINMARKETCAL_POLL_META.get("info") or "")

    crypto_interval = max(60, int(news_cfg.get("crypto_news_poll_sec", 300) or 300))
    if sources.get("cryptopanic") and now < float(_cryptopanic_backoff_until_sec or 0.0):
        results["cryptopanic_backoff_sec"] = max(0, int(_cryptopanic_backoff_until_sec - now))
        if _CRYPTOPANIC_POLL_META:
            results["cryptopanic_status"] = str(_CRYPTOPANIC_POLL_META.get("status", "backoff") or "backoff")
            if _CRYPTOPANIC_POLL_META.get("info"):
                results["cryptopanic_info"] = str(_CRYPTOPANIC_POLL_META.get("info") or "")
    elif sources.get("cryptopanic") and (now - _last_crypto_poll_sec) >= crypto_interval:
        requested_assets = len(base_assets)
        try:
            items = fetch_cryptopanic_posts_sync(base_assets=base_assets, limit=10)
            _adjust_cryptopanic_dynamic_limit(configured_asset_limit, rate_limited=False)
            results["cryptopanic_status"] = str(_CRYPTOPANIC_POLL_META.get("status", "ok") or "ok")
        except _CryptoPanicQuotaExceeded as exc:
            backoff_sec = max(86400, int(news_cfg.get("cryptopanic_backoff_sec", 900) or 900))
            _cryptopanic_backoff_until_sec = now + backoff_sec
            _set_poll_meta("cryptopanic", status="quota_exceeded", info=exc.info or str(exc), requested_assets=list(base_assets))
            logger.warning("[news_pipeline] CryptoPanic quota exhausted; backing off for %ss until plan quota resets or config changes.", backoff_sec)
            results["cryptopanic_backoff_sec"] = backoff_sec
            results["cryptopanic_asset_limit"] = _cryptopanic_dynamic_asset_limit or configured_asset_limit
            results["cryptopanic_status"] = "quota_exceeded"
            if exc.info:
                results["cryptopanic_info"] = exc.info
            items = []
        except _CryptoPanicRateLimited as exc:
            retry_after_sec = int(getattr(exc, "retry_after_sec", 0) or 0)
            configured_backoff = max(300, int(news_cfg.get("cryptopanic_backoff_sec", 900) or 900))
            backoff_sec = max(configured_backoff, retry_after_sec)
            _cryptopanic_backoff_until_sec = now + backoff_sec
            reduced_limit = _adjust_cryptopanic_dynamic_limit(configured_asset_limit, rate_limited=True)
            _set_poll_meta("cryptopanic", status="rate_limited", info=getattr(exc, "info", "") or str(exc), requested_assets=list(base_assets))
            logger.warning("[news_pipeline] CryptoPanic rate-limited; backing off for %ss (assets=%s -> next_limit=%s).", backoff_sec, requested_assets, reduced_limit)
            results["cryptopanic_backoff_sec"] = backoff_sec
            results["cryptopanic_asset_limit"] = reduced_limit
            results["cryptopanic_status"] = "rate_limited"
            if getattr(exc, "info", ""):
                results["cryptopanic_info"] = str(exc.info)
            items = []
        if items:
            persisted = ingest_normalized_news_items_sync(items, source="cryptopanic", model_name=model_name, tradable_symbols=tradable)
            results["cryptopanic"] = len(persisted)
            results["cryptopanic_asset_limit"] = _cryptopanic_dynamic_asset_limit or configured_asset_limit
            results["cryptopanic_status"] = str(_CRYPTOPANIC_POLL_META.get("status", "ok") or "ok")
            if _CRYPTOPANIC_POLL_META.get("info"):
                results["cryptopanic_info"] = str(_CRYPTOPANIC_POLL_META.get("info") or "")
        _last_crypto_poll_sec = now

    refresh_symbol_event_states_sync(symbols, limit=120)

    return results


async def news_cron_loop() -> None:
    logger.info("[news_pipeline] News cron loop started.")
    while True:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, run_news_poll_cycle_sync)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[news_pipeline] Poll cycle failed: %s", exc)
        await asyncio.sleep(5)
