"""Telegram retrieval service over local bot history and current state."""

from __future__ import annotations

from collections import Counter
import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.config_manager import get_config
from core.ollama import ollama_url
from integrations.telegram import bot_knowledge, entity_resolver, query_presenter
from services.crypto import store

logger = logging.getLogger("apex.telegram.query_service")

_SYMBOL_RE = re.compile(r"\b([A-Z]{2,10})(?:/USD)?\b")
_STOPWORDS = {
    "WHY", "WHAT", "WHEN", "SHOW", "LAST", "NEWS", "SCORE", "SCORES", "REPORT",
    "TRADE", "TRADES", "BOT", "AND", "THE", "FOR", "WITH", "DID", "OUR", "MY",
    "TODAY", "YESTERDAY", "THIS", "THAT", "HOW", "WERE", "ARE", "FROM", "PAST",
    "WE", "BUY", "SELL", "HOLD", "BOUGHT", "SOLD", "STATUS", "POSITION", "POSITIONS",
    "WHICH", "TYPE", "TYPES", "LOSE", "LOSING", "MOST", "MONEY", "EVENT", "EVENTS",
    "AFFECTED", "LATEST", "CURRENT", "JUST", "RSI", "EMA", "MACD", "ATR", "VWAP",
    "BB", "BOLLINGER", "BANDS", "GOVERNOR", "PRESSURE", "MODE", "PNL", "DCA", "IS", "CURRENT", "ACTIVE", "BRAIN",
    "ORACLE", "TRACKED", "COINS", "COIN", "CARD", "LEARNING", "LEARN", "BLOCKED", "DETAILS",
}
_ALLOWED_INTENTS = {
    "portfolio_status",
    "position_status",
    "trade_analysis",
    "trade_history",
    "decision_review",
    "news_analysis",
    "report_summary",
    "event_edge_analysis",
    "indicator_status",
    "config_status",
    "coin_card",
    "learning_card",
    "brain_symbol",
    "tracked_status",
    "blocked_review",
    "news_breadth",
    "oracle_detail",
}
_ALLOWED_DATASETS = {
    "account_summary",
    "current_positions",
    "actions",
    "live_experience",
    "news_events",
    "decision_traces",
    "event_state",
    "macro_consensus",
    "reports",
    "live_news",
    "current_indicators",
    "runtime_status",
    "tracked_symbols",
    "decision_outcomes",
    "symbol_policy_overlay",
    "brain_self_score",
    "oracle_state",
}
_ALLOWED_CALCULATIONS = {
    "position_snapshot",
    "trade_stats",
    "decision_stats",
    "news_stats",
    "macro_snapshot",
    "event_edge_stats",
    "indicator_snapshot",
    "runtime_snapshot",
    "learning_stats",
    "tracked_snapshot",
    "breadth_snapshot",
    "oracle_snapshot",
}
_DEBUG_TERMS = ("debug", "raw", "evidence", "query plan", "queryplan", "show plan")
_LIVE_NEWS_QUERY_COOLDOWN_SEC = 900
_LIVE_NEWS_CACHE_LOCK = threading.Lock()
_LIVE_NEWS_CACHE: Dict[str, Dict[str, Any]] = {}


def _llama_retrieval_module():
    from integrations.telegram import llama_retrieval

    return llama_retrieval


def _scratchpad_helpers():
    from services.crypto.research_scratchpad import build_query_scratchpad, critique_query_answer

    return build_query_scratchpad, critique_query_answer


def _normalize_symbol(raw: str) -> str:
    token = str(raw or "").strip().upper()
    if not token:
        return ""
    if "/" in token:
        return token
    return f"{token}/USD"


def _extract_symbol(question: str) -> str:
    symbol = entity_resolver.primary_symbol(question)
    if symbol:
        return _normalize_symbol(symbol)

    prompt = str(question or "").upper()
    for match in _SYMBOL_RE.findall(prompt):
        if match not in _STOPWORDS and 2 <= len(match) <= 10:
            return _normalize_symbol(match)
    return ""


def _extract_limit(question: str, default: int = 5) -> int:
    match = re.search(r"\b(?:last|recent)\s+(\d{1,3})\b", str(question or ""), re.IGNORECASE)
    if not match:
        return default
    return max(1, min(int(match.group(1)), 20))


def _extract_lookback_days(question: str, default: int = 7) -> int:
    prompt = str(question or "").lower()
    if "today" in prompt or "current" in prompt or "now" in prompt:
        return 1
    if "yesterday" in prompt:
        return 2
    if "this week" in prompt or "weekly" in prompt:
        return 7
    if "this month" in prompt or "monthly" in prompt:
        return 30
    match = re.search(r"\b(?:last|past)\s+(\d{1,3})\s*(day|days|week|weeks|month|months)\b", prompt)
    if not match:
        return default
    value = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("week"):
        value *= 7
    elif unit.startswith("month"):
        value *= 30
    return max(1, min(value, 365))


def _match_symbol(value: Any, symbol: str) -> bool:
    if not symbol:
        return True
    current = str(value or "").upper()
    target = symbol.upper()
    return current == target or current.replace("/", "") == target.replace("/", "")


def _question_focus(question: str) -> str:
    lower = str(question or "").lower()
    entities = entity_resolver.resolve_entities(question)
    concepts = set(entities.get("concepts") or [])
    indicators = set(entities.get("indicators") or [])
    symbols = entities.get("symbols") or []

    if any(term in lower for term in ("tracked coins", "tracked symbols", "show tracked", "active coins", "saved coins", "recently active")):
        return "tracked_status"
    if any(term in lower for term in ("news breadth", "market breadth in news", "btc bias in news", "btc dominating the news")):
        return "news_breadth"
    if "oracle" in lower and any(term in lower for term in ("detail", "details", "show oracle", "oracle update", "oracle seeing", "oracle view")):
        return "oracle_detail"
    if symbols and "blocked" in lower:
        return "blocked_review"
    if symbols and any(term in lower for term in ("learning", "learn ", "learn?", "outcomes", "overlay")):
        return "learning_card"
    if symbols and "brain" in lower and any(term in lower for term in ("score", "scoring", "think", "view", "detail", "details", "reason")):
        return "brain_symbol"
    if symbols and any(term in lower for term in ("card", "tell me about", "update on", "coin card")):
        return "coin_card"
    if "BRAIN" in concepts or any(term in lower for term in ("current brain", "active brain", "brain mode", "what brain", "which brain")):
        return "config_status"
    if indicators or any(term in lower for term in ("rsi", "ema", "macd", "atr", "vwap", "bollinger", "bb ", "bands", "governor pressure")):
        return "indicator_status"
    if any(term in lower for term in ("event types", "event type", "which event", "lose the most money", "losing the most money")):
        return "event_edge_analysis"
    if any(term in lower for term in ("why", "because", "reason", "decision", "trace")):
        return "decision_review"
    if any(term in lower for term in ("news", "event", "headline", "catalyst")):
        return "news_analysis"
    if any(term in lower for term in ("report", "summary", "macro")):
        return "report_summary"
    if re.search(r"\b(last|recent)\s+\d+\b", lower) and "trade" in lower:
        return "trade_history"
    if any(term in lower for term in ("trade", "win rate", "losing", "performance history", "history")):
        return "trade_analysis"
    if symbols and any(term in lower for term in ("status", "holding", "position", "portfolio", "my ")):
        return "position_status"
    if any(term in lower for term in ("status", "portfolio")):
        return "portfolio_status"
    return "portfolio_status"


def _deterministic_plan(question: str) -> Dict[str, Any]:
    prompt = str(question or "").strip()
    intent = _question_focus(prompt)
    symbol = "" if intent in {"event_edge_analysis", "config_status", "tracked_status", "news_breadth", "oracle_detail"} else _extract_symbol(prompt)
    limit = _extract_limit(prompt)
    lookback_days = _extract_lookback_days(prompt)

    datasets = ["macro_consensus", "reports"]
    calculations = ["macro_snapshot"]

    if intent in {"portfolio_status", "position_status"}:
        datasets = ["account_summary", "current_positions", "live_experience", "event_state", "macro_consensus", "reports"]
        calculations = ["position_snapshot", "trade_stats", "macro_snapshot"]
    elif intent == "trade_history":
        datasets = ["live_experience", "decision_traces", "news_events", "macro_consensus", "reports"]
        calculations = ["trade_stats", "decision_stats", "news_stats", "macro_snapshot"]
    elif intent == "trade_analysis":
        datasets = ["live_experience", "actions", "decision_traces", "event_state", "macro_consensus", "reports"]
        calculations = ["trade_stats", "decision_stats", "macro_snapshot"]
    elif intent == "decision_review":
        datasets = ["account_summary", "current_positions", "actions", "decision_traces", "live_experience", "news_events", "event_state", "macro_consensus", "reports"]
        calculations = ["position_snapshot", "trade_stats", "decision_stats", "news_stats", "macro_snapshot"]
    elif intent == "news_analysis":
        datasets = ["news_events", "event_state", "macro_consensus", "reports"]
        calculations = ["news_stats", "macro_snapshot"]
    elif intent == "indicator_status":
        datasets = ["current_indicators", "current_positions", "event_state", "macro_consensus", "reports"]
        calculations = ["indicator_snapshot", "position_snapshot", "macro_snapshot"]
    elif intent == "config_status":
        datasets = ["runtime_status", "macro_consensus", "reports"]
        calculations = ["runtime_snapshot", "macro_snapshot"]
    elif intent == "report_summary":
        datasets = ["reports", "macro_consensus", "decision_traces"]
        calculations = ["macro_snapshot", "decision_stats"]
    elif intent == "event_edge_analysis":
        datasets = ["live_experience", "reports"]
        calculations = ["event_edge_stats"]
    elif intent == "coin_card":
        datasets = ["current_positions", "live_experience", "decision_traces", "decision_outcomes", "event_state", "macro_consensus", "reports", "tracked_symbols", "symbol_policy_overlay", "oracle_state"]
        calculations = ["position_snapshot", "trade_stats", "decision_stats", "learning_stats", "tracked_snapshot", "macro_snapshot", "oracle_snapshot"]
    elif intent == "learning_card":
        datasets = ["live_experience", "decision_outcomes", "symbol_policy_overlay", "brain_self_score", "tracked_symbols", "macro_consensus", "reports"]
        calculations = ["trade_stats", "learning_stats", "tracked_snapshot", "macro_snapshot"]
    elif intent == "brain_symbol":
        datasets = ["decision_traces", "decision_outcomes", "symbol_policy_overlay", "brain_self_score", "tracked_symbols", "macro_consensus", "reports"]
        calculations = ["decision_stats", "learning_stats", "tracked_snapshot", "macro_snapshot"]
    elif intent == "tracked_status":
        datasets = ["tracked_symbols", "macro_consensus", "reports"]
        calculations = ["tracked_snapshot", "macro_snapshot"]
    elif intent == "blocked_review":
        datasets = ["actions", "decision_traces", "decision_outcomes", "event_state", "tracked_symbols", "macro_consensus", "reports"]
        calculations = ["decision_stats", "learning_stats", "tracked_snapshot", "macro_snapshot"]
    elif intent == "news_breadth":
        datasets = ["news_events", "macro_consensus", "reports", "oracle_state"]
        calculations = ["news_stats", "breadth_snapshot", "macro_snapshot", "oracle_snapshot"]
    elif intent == "oracle_detail":
        datasets = ["oracle_state", "macro_consensus", "reports"]
        calculations = ["oracle_snapshot", "macro_snapshot"]

    lower = prompt.lower()
    include_live_news = (
        intent == "news_analysis"
        and bool(symbol)
        and any(term in lower for term in ("latest", "current", "today", "now", "just"))
    )
    if include_live_news:
        datasets.append("live_news")

    return {
        "question": prompt,
        "intent": intent,
        "symbol": symbol,
        "lookback_days": lookback_days,
        "limit": limit,
        "datasets": datasets,
        "calculations": calculations,
        "include_live_news": include_live_news,
        "brain_mode": str(get_config().get("stocks", {}).get("crypto", {}).get("active_brain", "") or ""),
    }


def _validate_plan(plan: Dict[str, Any], question: str) -> Dict[str, Any]:
    fallback = _deterministic_plan(question)
    if not isinstance(plan, dict):
        return fallback

    intent = str(plan.get("intent") or fallback["intent"]).strip().lower()
    if intent not in _ALLOWED_INTENTS:
        intent = fallback["intent"]

    symbol = _normalize_symbol(plan.get("symbol") or fallback["symbol"])
    if intent in {"event_edge_analysis", "config_status", "tracked_status", "news_breadth", "oracle_detail"}:
        symbol = ""
    limit = max(1, min(int(plan.get("limit", fallback["limit"]) or fallback["limit"]), 20))
    lookback_days = max(1, min(int(plan.get("lookback_days", fallback["lookback_days"]) or fallback["lookback_days"]), 365))
    include_live_news = bool(plan.get("include_live_news", fallback["include_live_news"]))

    datasets = [item for item in plan.get("datasets", fallback["datasets"]) if item in _ALLOWED_DATASETS]
    calculations = [item for item in plan.get("calculations", fallback["calculations"]) if item in _ALLOWED_CALCULATIONS]
    if not datasets:
        datasets = fallback["datasets"]
    if not calculations:
        calculations = fallback["calculations"]
    if include_live_news and "live_news" not in datasets:
        datasets.append("live_news")

    return {
        "question": question,
        "intent": intent,
        "symbol": symbol,
        "lookback_days": lookback_days,
        "limit": limit,
        "datasets": datasets,
        "calculations": calculations,
        "include_live_news": include_live_news,
        "brain_mode": fallback.get("brain_mode", ""),
    }


def _plan_with_ollama(question: str) -> Optional[Dict[str, Any]]:
    cfg = get_config().get("stocks", {}).get("crypto", {})
    model_name = str(cfg.get("telegram_ollama_model") or "qwen3:8b")
    schema_prompt = (
        bot_knowledge.planner_context(question)
        + "\n\nConvert the user's bot-data question into a strict JSON query plan. "
        + "Do not write SQL. Use only these intent values: "
        + "portfolio_status, position_status, trade_analysis, trade_history, decision_review, news_analysis, report_summary, event_edge_analysis, indicator_status, config_status, coin_card, learning_card, brain_symbol, tracked_status, blocked_review, news_breadth, oracle_detail. "
        + "Use only these datasets: account_summary, current_positions, actions, live_experience, news_events, "
        + "decision_traces, event_state, macro_consensus, reports, live_news, current_indicators, runtime_status, tracked_symbols, decision_outcomes, symbol_policy_overlay, brain_self_score, oracle_state. "
        + "Use only these calculations: position_snapshot, trade_stats, decision_stats, news_stats, macro_snapshot, event_edge_stats, indicator_snapshot, runtime_snapshot, learning_stats, tracked_snapshot, breadth_snapshot, oracle_snapshot. "
        + "Return JSON with keys: intent, symbol, lookback_days, limit, datasets, calculations, include_live_news."
    )
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": schema_prompt},
            {"role": "user", "content": question},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_predict": 220},
    }
    try:
        response = httpx.post(ollama_url("/api/chat"), json=payload, timeout=12.0)
        response.raise_for_status()
        raw = str(((response.json().get("message") or {}).get("content")) or "").strip()
        # Strip <think> tokens before parsing
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end >= start:
            raw = raw[start:end + 1]
        return _validate_plan(json.loads(raw), question)
    except Exception as exc:
        logger.debug("[query_service] Ollama planner failed: %s", exc)
        return None


def build_query_plan(question: str) -> Dict[str, Any]:
    prompt = str(question or "").strip()
    if not prompt:
        return _deterministic_plan("")
    return _plan_with_ollama(prompt) or _deterministic_plan(prompt)


def _account_summary() -> Dict[str, Any]:
    try:
        from services.crypto.market_data import get_account_summary
        return get_account_summary() or {}
    except Exception as exc:
        logger.debug("[query_service] account summary failed: %s", exc)
        return {}

def _runtime_status() -> Dict[str, Any]:
    try:
        from services.crypto.bot import _RUNTIME
        return _RUNTIME.status() or {}
    except Exception as exc:
        logger.debug("[query_service] runtime status failed: %s", exc)
        return {}


def _account_mode() -> str:
    return str(get_config().get("stocks", {}).get("crypto", {}).get("account_mode", "paper") or "paper")

def _current_positions(symbol: str) -> List[Dict[str, Any]]:
    try:
        from services.crypto.market_data import get_crypto_positions
        rows = get_crypto_positions() or []
    except Exception as exc:
        logger.debug("[query_service] current positions failed: %s", exc)
        return []
    return [row for row in rows if _match_symbol(row.get("symbol", ""), symbol)]


def _recent_actions(symbol: str, limit: int) -> List[Dict[str, Any]]:
    rows = store._list_actions_sync(limit=max(limit * 3, 20))
    return [row for row in rows if _match_symbol(row.get("symbol", ""), symbol)][:limit]


def _recent_experience(symbol: str, limit: int) -> List[Dict[str, Any]]:
    rows = store.get_live_experience_sync(limit=max(limit * 8, 50), closed_only=False)
    return [row for row in rows if _match_symbol(row.get("symbol", ""), symbol)][:limit]


def _recent_reports(limit: int) -> List[Dict[str, Any]]:
    return store.get_latest_reports_sync(limit=limit)


def _tracked_symbols(symbol: str, limit: int) -> List[Dict[str, Any]]:
    mode = _account_mode()
    if symbol:
        row = store.get_tracked_symbol_sync(symbol, mode)
        return [row] if row else []
    return store.list_tracked_symbols_sync(mode, include_idle=False, limit=max(limit * 5, 40))


def _decision_outcomes(symbol: str, limit: int) -> List[Dict[str, Any]]:
    return store.list_decision_outcomes_sync(limit=max(limit * 8, 40), symbol=symbol)


def _symbol_policy_overlay(symbol: str) -> Dict[str, Any]:
    if not symbol:
        return {}
    return store.get_symbol_policy_overlay_sync(symbol)


def _brain_self_score() -> Dict[str, Any]:
    return {
        "latest": store.get_latest_brain_self_score_sync() or {},
        "history": store.list_brain_self_score_history_sync(limit=12),
    }


def _oracle_state() -> Dict[str, Any]:
    try:
        from services.crypto.news_pipeline import _news_provider_status
        from services.crypto.oracle import get_current_oracle_state, get_narrative_status, get_oracle_status

        return {
            "current": get_current_oracle_state() or {},
            "status": get_oracle_status() or {},
            "narrative": get_narrative_status() or {},
            "news_provider_status": _news_provider_status() or {},
        }
    except Exception as exc:
        logger.debug("[query_service] oracle state failed: %s", exc)
        return {}


def _provider_headroom(provider_status: Dict[str, Any]) -> Dict[str, bool]:
    headroom: Dict[str, bool] = {}
    for name, meta in (provider_status or {}).items():
        status = str((meta or {}).get("status", "idle") or "idle").strip().lower()
        headroom[str(name)] = status in {"idle", "ok", "fresh"}
    return headroom


def _live_news_cache_key(symbol: str, limit: int) -> str:
    return f"{str(symbol or '').upper()}:{int(limit or 0)}"



def _indicator_snapshot(symbol: str) -> Dict[str, Any]:
    if not symbol:
        return {}
    try:
        from services.crypto import market_data
        from services.crypto.indicators import enrich_indicators, snapshot
    except Exception as exc:
        logger.debug("[query_service] indicator imports failed: %s", exc)
        return {}
    result: Dict[str, Any] = {"symbol": symbol}
    try:
        bars_15m = market_data.fetch_bars(symbol, timeframe="15Min", limit=120)
        if bars_15m is not None and not bars_15m.empty:
            snap_15m = snapshot(enrich_indicators(bars_15m))
            result.update({
                "close_15m": float(snap_15m.get("close", 0.0) or 0.0),
                "rsi_15m": float(snap_15m.get("rsi14", 50.0) or 50.0),
                "ema_fast_15m": float(snap_15m.get("ema_fast", 0.0) or 0.0),
                "ema_slow_15m": float(snap_15m.get("ema_slow", 0.0) or 0.0),
                "bb_upper_15m": float(snap_15m.get("bb_upper", 0.0) or 0.0),
                "bb_lower_15m": float(snap_15m.get("bb_lower", 0.0) or 0.0),
                "trend_15m": "bullish" if float(snap_15m.get("ema_fast", 0.0) or 0.0) >= float(snap_15m.get("ema_slow", 0.0) or 0.0) else "bearish",
            })
    except Exception as exc:
        logger.debug("[query_service] 15m indicators failed: %s", exc)
    try:
        bars_1m = market_data.fetch_bars(symbol, timeframe="1Min", limit=60)
        if bars_1m is not None and not bars_1m.empty:
            snap_1m = snapshot(enrich_indicators(bars_1m))
            result.update({
                "close_1m": float(snap_1m.get("close", 0.0) or 0.0),
                "rsi_1m": float(snap_1m.get("rsi14", 50.0) or 50.0),
            })
    except Exception as exc:
        logger.debug("[query_service] 1m indicators failed: %s", exc)
    return result

def _live_news(symbol: str, limit: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not symbol:
        return [], {"status": "symbol_required", "from_cache": False, "reason": "symbol_required"}
    try:
        from services.crypto.news_pipeline import _news_provider_status, fetch_binance_announcements_sync, fetch_cryptopanic_posts_sync
    except Exception as exc:
        logger.debug("[query_service] live news imports failed: %s", exc)
        return [], {"status": "unavailable", "from_cache": False, "reason": str(exc)}
    base = symbol.split("/")[0]
    provider_status = _news_provider_status() or {}
    headroom = _provider_headroom(provider_status)
    cache_key = _live_news_cache_key(symbol, limit)
    now_sec = time.time()
    with _LIVE_NEWS_CACHE_LOCK:
        cached = dict(_LIVE_NEWS_CACHE.get(cache_key) or {})
    cached_items = list(cached.get("items") or [])
    cached_age_sec = now_sec - float(cached.get("ts_sec", 0.0) or 0.0)
    if cached_items and cached_age_sec < _LIVE_NEWS_QUERY_COOLDOWN_SEC:
        return cached_items[:limit], {
            "status": "cooldown_cached",
            "from_cache": True,
            "cooldown_sec": _LIVE_NEWS_QUERY_COOLDOWN_SEC,
            "cache_age_sec": round(cached_age_sec, 1),
            "provider_status": provider_status,
        }
    if not any(headroom.values()):
        return cached_items[:limit], {
            "status": "provider_limited",
            "from_cache": bool(cached_items),
            "cooldown_sec": _LIVE_NEWS_QUERY_COOLDOWN_SEC,
            "cache_age_sec": round(cached_age_sec, 1) if cached_items else None,
            "provider_status": provider_status,
        }

    results: List[Dict[str, Any]] = []
    providers_used: List[str] = []
    if headroom.get("cryptopanic", False):
        try:
            for item in fetch_cryptopanic_posts_sync(base_assets=[base], limit=limit):
                item = dict(item)
                item["source"] = "cryptopanic_live"
                results.append(item)
            providers_used.append("cryptopanic")
        except Exception as exc:
            logger.debug("[query_service] live cryptopanic fetch failed: %s", exc)
    if headroom.get("binance", False):
        try:
            for item in fetch_binance_announcements_sync(limit=limit):
                if _match_symbol(item.get("base_asset", ""), symbol) or _match_symbol(item.get("title", ""), symbol):
                    item = dict(item)
                    item["source"] = "binance_live"
                    results.append(item)
            providers_used.append("binance")
        except Exception as exc:
            logger.debug("[query_service] live binance fetch failed: %s", exc)

    trimmed = results[:limit]
    with _LIVE_NEWS_CACHE_LOCK:
        _LIVE_NEWS_CACHE[cache_key] = {"ts_sec": now_sec, "items": list(trimmed)}
    return trimmed, {
        "status": "fresh_fetch",
        "from_cache": False,
        "cooldown_sec": _LIVE_NEWS_QUERY_COOLDOWN_SEC,
        "provider_status": provider_status,
        "providers_used": providers_used,
    }


def _execute_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    symbol = plan.get("symbol", "")
    limit = int(plan.get("limit", 5) or 5)
    datasets = set(plan.get("datasets", []))
    result: Dict[str, Any] = {"plan": plan, "symbol": symbol}
    if "account_summary" in datasets:
        result["account_summary"] = _account_summary()
    if "current_positions" in datasets:
        result["current_positions"] = _current_positions(symbol)
    if "actions" in datasets:
        result["actions"] = _recent_actions(symbol, limit)
    if "live_experience" in datasets:
        result["live_experience"] = _recent_experience(symbol, limit)
    if "news_events" in datasets:
        result["news_events"] = store.get_recent_news_events_sync(limit=limit, symbol=symbol, active_only=False)
    if "decision_traces" in datasets:
        result["decision_traces"] = store.list_brain_decision_traces_sync(limit=limit, symbol=symbol)
    if "event_state" in datasets:
        result["event_state"] = store.get_symbol_event_state_sync(symbol) if symbol else {}
    if "macro_consensus" in datasets:
        result["macro_consensus"] = store.get_latest_macro_consensus_sync() or {}
    if "reports" in datasets:
        result["reports"] = _recent_reports(min(limit, 3))
    if "runtime_status" in datasets:
        result["runtime_status"] = _runtime_status()
    if "tracked_symbols" in datasets:
        result["tracked_symbols"] = _tracked_symbols(symbol, limit)
    if "decision_outcomes" in datasets:
        result["decision_outcomes"] = _decision_outcomes(symbol, limit)
    if "symbol_policy_overlay" in datasets:
        result["symbol_policy_overlay"] = _symbol_policy_overlay(symbol)
    if "brain_self_score" in datasets:
        result["brain_self_score"] = _brain_self_score()
    if "oracle_state" in datasets:
        result["oracle_state"] = _oracle_state()
    if "current_indicators" in datasets:
        result["current_indicators"] = _indicator_snapshot(symbol)
    if "live_news" in datasets:
        result["live_news"], result["live_news_meta"] = _live_news(symbol, limit)
    return result


def _horizon_label(horizon_min: Any) -> str:
    try:
        minutes = int(horizon_min or 0)
    except Exception:
        minutes = 0
    if minutes <= 0:
        return "unknown"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours}h" if hours != 1 else "1h"
    return f"{minutes}m"


def _headline_symbol_counts(rows: List[Dict[str, Any]]) -> List[Tuple[str, int]]:
    configured_symbols = list(get_config().get("stocks", {}).get("crypto", {}).get("symbols", []) or [])
    bases = []
    for raw in configured_symbols:
        token = str(raw or "").upper().split("/")[0].strip()
        if token and token not in bases:
            bases.append(token)
    counts: Counter[str] = Counter()
    for row in rows:
        symbol = str(row.get("symbol", "") or "").upper().split("/")[0].strip()
        base_asset = str(row.get("base_asset", "") or "").upper().strip()
        if symbol:
            counts[symbol] += 1
        if base_asset:
            counts[base_asset] += 1
        payload = dict(row.get("payload") or {})
        parsed = dict(payload.get("parsed") or {}) if isinstance(payload.get("parsed"), dict) else {}
        for asset in parsed.get("asset_scope") or []:
            token = str(asset or "").upper().strip()
            if token:
                counts[token] += 1
        headline = f"{row.get('headline', '')} {row.get('title', '')}"
        for base in bases:
            if re.search(rf"(?<![A-Z0-9]){re.escape(base)}(?![A-Z0-9])", headline, flags=re.IGNORECASE):
                counts[base] += 1
    return counts.most_common(8)


def _calculate_metrics(plan: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    calculations = set(plan.get("calculations", []))

    if "position_snapshot" in calculations:
        positions = evidence.get("current_positions") or []
        if positions:
            row = positions[0]
            metrics["position_open"] = True
            metrics["position_qty"] = round(float(row.get("qty", 0.0) or 0.0), 6)
            metrics["avg_entry_price"] = float(row.get("avg_entry_price", 0.0) or 0.0)
            metrics["current_price"] = float(row.get("current_price", 0.0) or 0.0)
            metrics["unrealized_pl"] = float(row.get("unrealized_pl", 0.0) or 0.0)
            metrics["unrealized_plpc"] = round(float(row.get("unrealized_plpc", 0.0) or 0.0) * 100.0, 2)
        else:
            metrics["position_open"] = False

    if "trade_stats" in calculations:
        rows = evidence.get("live_experience") or []
        closed = [row for row in rows if row.get("outcome_pnl_pct") is not None]
        metrics["trade_count"] = len(rows)
        metrics["closed_trade_count"] = len(closed)
        metrics["open_trade_count"] = len(rows) - len(closed)
        if closed:
            pnls = [float(row.get("outcome_pnl_pct", 0.0) or 0.0) for row in closed]
            wins = [value for value in pnls if value > 0]
            metrics["avg_pnl_pct"] = round(sum(pnls) / len(pnls), 2)
            metrics["win_rate_pct"] = round((len(wins) / len(pnls)) * 100.0, 2)
            metrics["total_realized_pnl_pct"] = round(sum(pnls), 2)

    if "decision_stats" in calculations:
        rows = evidence.get("decision_traces") or []
        if rows:
            scores = [float(row.get("final_score", 0.0) or 0.0) for row in rows]
            submitted = [1 for row in rows if bool(row.get("submitted", 0))]
            block_counts: Dict[str, int] = {}
            for row in rows:
                reason = str(row.get("block_reason", "") or "")
                if reason:
                    block_counts[reason] = block_counts.get(reason, 0) + 1
            metrics["decision_count"] = len(rows)
            metrics["avg_decision_score"] = round(sum(scores) / len(scores), 2)
            metrics["submitted_ratio_pct"] = round((len(submitted) / len(rows)) * 100.0, 2)
            metrics["last_decision"] = rows[0].get("decision", "")
            metrics["blocked_count"] = len(rows) - len(submitted)
            metrics["block_reason_counts"] = block_counts
            if block_counts:
                metrics["top_block_reason"] = sorted(block_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    if "news_stats" in calculations:
        rows = list(evidence.get("news_events") or []) + list(evidence.get("live_news") or [])
        impacts = [float(row.get("impact_score", 0.0) or 0.0) for row in rows if row.get("impact_score") is not None]
        positives = [row for row in rows if "positive" in str(row.get("sentiment", "")).lower()]
        negatives = [row for row in rows if "negative" in str(row.get("sentiment", "")).lower()]
        tagged = 0
        for row in rows:
            payload = dict(row.get("payload") or {})
            parsed = dict(payload.get("parsed") or {}) if isinstance(payload.get("parsed"), dict) else {}
            if str(row.get("symbol", "") or "").strip() or str(row.get("base_asset", "") or "").strip() or list(parsed.get("asset_scope") or []):
                tagged += 1
        metrics["news_event_count"] = len(rows)
        metrics["avg_news_impact"] = round(sum(impacts) / len(impacts), 2) if impacts else 0.0
        metrics["positive_news_count"] = len(positives)
        metrics["negative_news_count"] = len(negatives)
        metrics["symbol_tagged_news_count"] = tagged
        metrics["unattributed_news_count"] = max(0, len(rows) - tagged)
        if rows:
            metrics["top_news_headline"] = str(rows[0].get("headline") or rows[0].get("title") or "")[:160]
        live_news_meta = evidence.get("live_news_meta") or {}
        if live_news_meta:
            metrics["live_news_status"] = str(live_news_meta.get("status", "") or "")
            metrics["live_news_from_cache"] = bool(live_news_meta.get("from_cache"))

    if "macro_snapshot" in calculations:
        macro = evidence.get("macro_consensus") or {}
        if macro:
            metrics["macro_risk_multiplier"] = float(macro.get("risk_multiplier", 1.0) or 1.0)
            metrics["macro_regime"] = str(macro.get("market_regime", "normal") or "normal")
            payload = dict(macro.get("payload") or {})
            if payload.get("breadth_state"):
                metrics["macro_breadth_state"] = str(payload.get("breadth_state") or "")

    if "indicator_snapshot" in calculations:
        indicator_row = evidence.get("current_indicators") or {}
        if indicator_row:
            metrics["indicator_symbol"] = str(indicator_row.get("symbol") or plan.get("symbol") or "")
            for key in ("rsi_15m", "rsi_1m", "close_15m", "close_1m", "ema_fast_15m", "ema_slow_15m", "bb_upper_15m", "bb_lower_15m"):
                if key in indicator_row:
                    metrics[key] = indicator_row.get(key)
            metrics["trend_15m"] = str(indicator_row.get("trend_15m") or "")

    if "runtime_snapshot" in calculations:
        runtime = evidence.get("runtime_status") or {}
        last_status = runtime.get("last_status") or {}
        risk_state = runtime.get("risk_state") or {}
        governor = runtime.get("activity_governor") or {}
        metrics["active_brain"] = str(last_status.get("active_brain") or plan.get("brain_mode") or runtime.get("active_brain") or "")
        metrics["trading_mode"] = str(last_status.get("trading_mode") or "")
        metrics["account_mode"] = str(last_status.get("account_mode") or "")
        metrics["risk_state"] = str(risk_state.get("state") or "normal")
        metrics["risk_source"] = str(risk_state.get("source") or "")
        metrics["governor_mode"] = str(governor.get("mode") or "normal")
        metrics["pressure_score"] = float(governor.get("pressure_score", 0.0) or 0.0)
        effective_risk_profile = dict(runtime.get("effective_risk_profile") or {})
        if effective_risk_profile:
            metrics["effective_hard_cap_usd"] = float(effective_risk_profile.get("effective_hard_cap_usd", 0.0) or 0.0)
            metrics["effective_ai_ceiling_usd"] = float(effective_risk_profile.get("effective_ai_ceiling_usd", 0.0) or 0.0)
            metrics["max_exposure_usd"] = float(effective_risk_profile.get("max_exposure_usd", 0.0) or 0.0)

    if "learning_stats" in calculations:
        rows = evidence.get("decision_outcomes") or []
        by_horizon: Dict[str, Dict[str, Any]] = {}
        label_counts: Dict[str, int] = {}
        for row in rows:
            label = str(row.get("outcome_label", "") or "")
            if label:
                label_counts[label] = label_counts.get(label, 0) + 1
            horizon = _horizon_label(row.get("horizon_min", 0))
            bucket = by_horizon.setdefault(horizon, {"count": 0, "pnl_total": 0.0, "pnl_count": 0, "labels": {}})
            bucket["count"] += 1
            pnl = row.get("pnl_pct")
            if pnl is not None:
                bucket["pnl_total"] += float(pnl or 0.0)
                bucket["pnl_count"] += 1
            if label:
                labels = bucket["labels"]
                labels[label] = labels.get(label, 0) + 1
        summarized_horizons: Dict[str, Dict[str, Any]] = {}
        for horizon, bucket in by_horizon.items():
            pnl_count = int(bucket.get("pnl_count", 0) or 0)
            summarized_horizons[horizon] = {
                "count": int(bucket.get("count", 0) or 0),
                "avg_pnl_pct": round(float(bucket.get("pnl_total", 0.0) or 0.0) / pnl_count, 2) if pnl_count else None,
                "labels": dict(bucket.get("labels") or {}),
            }
        metrics["decision_outcome_count"] = len(rows)
        metrics["decision_outcomes_by_horizon"] = summarized_horizons
        metrics["decision_outcome_labels"] = label_counts
        if rows:
            latest = rows[0]
            metrics["latest_outcome_label"] = str(latest.get("outcome_label", "") or "")
            metrics["latest_outcome_horizon"] = _horizon_label(latest.get("horizon_min", 0))
            metrics["latest_outcome_pnl_pct"] = latest.get("pnl_pct")
        overlay = evidence.get("symbol_policy_overlay") or {}
        if overlay:
            metrics["overlay_score_delta"] = float(overlay.get("score_delta", 0.0) or 0.0)
            metrics["overlay_size_multiplier"] = float(overlay.get("size_multiplier", 1.0) or 1.0)
            metrics["overlay_confidence_boost"] = float(overlay.get("confidence_boost", 0.0) or 0.0)
            metrics["overlay_decision_samples"] = int(overlay.get("decision_samples", 0) or 0)
        brain = evidence.get("brain_self_score") or {}
        latest_brain = dict(brain.get("latest") or {})
        if latest_brain:
            metrics["brain_self_score"] = float(latest_brain.get("score", 0.0) or 0.0)
            metrics["brain_self_confidence"] = float(latest_brain.get("confidence", 0.0) or 0.0)
        metrics["learning_ready"] = bool(overlay) or len(rows) >= 4

    if "tracked_snapshot" in calculations:
        rows = evidence.get("tracked_symbols") or []
        counts = {"active": 0, "recently_active": 0, "saved": 0}
        for row in rows:
            state = str(row.get("active_state", "idle") or "idle")
            if state in counts:
                counts[state] += 1
            if bool(row.get("saved_manual")):
                counts["saved"] += 1
        metrics["tracked_state_counts"] = counts
        if rows and plan.get("symbol"):
            row = rows[0]
            metrics["tracked_active_state"] = str(row.get("active_state", "idle") or "idle")
            metrics["tracked_active_reason"] = str(row.get("active_reason", "") or "")
            metrics["tracked_saved_manual"] = bool(row.get("saved_manual"))
            metrics["tracked_monitor_tier"] = str(row.get("monitor_tier", "eligible") or "eligible")
            metrics["tracked_active_since_ts"] = int(row.get("active_since_ts", 0) or 0)
            metrics["tracked_recent_until_ts"] = int(row.get("recent_until_ts", 0) or 0)
        elif rows:
            metrics["tracked_preview"] = [str(row.get("symbol", "") or "") for row in rows[:8]]

    if "breadth_snapshot" in calculations:
        rows = list(evidence.get("news_events") or [])
        source_counts: Dict[str, int] = {}
        for row in rows:
            source = str(row.get("source", "news") or "news").lower()
            source_counts[source] = source_counts.get(source, 0) + 1
        top_symbols = _headline_symbol_counts(rows)
        metrics["news_source_counts"] = source_counts
        metrics["news_breadth_top_symbols"] = top_symbols
        if top_symbols:
            leader_symbol, leader_count = top_symbols[0]
            total_mentions = sum(count for _symbol, count in top_symbols)
            metrics["news_breadth_leader"] = leader_symbol
            metrics["news_breadth_leader_share_pct"] = round((leader_count / max(1, total_mentions)) * 100.0, 2)
        oracle_state = evidence.get("oracle_state") or {}
        provider_status = dict(oracle_state.get("news_provider_status") or {})
        if provider_status:
            metrics["news_provider_status"] = {
                str(name): str((meta or {}).get("status", "idle") or "idle")
                for name, meta in provider_status.items()
            }

    if "oracle_snapshot" in calculations:
        oracle_state = evidence.get("oracle_state") or {}
        current = dict(oracle_state.get("current") or {})
        status = dict(oracle_state.get("status") or {})
        narrative = dict(oracle_state.get("narrative") or {})
        if current or status:
            metrics["oracle_score"] = float(current.get("risk_multiplier", status.get("score", 1.0)) or 1.0)
            metrics["oracle_regime"] = str(current.get("market_regime", status.get("market_regime", "normal")) or "normal")
            metrics["oracle_summary"] = str(current.get("rationale_summary", status.get("summary", "")) or "")
            metrics["oracle_age_sec"] = int(status.get("age_sec", 0) or 0)
            metrics["oracle_breadth_state"] = str(current.get("breadth_state", status.get("breadth_state", "mixed")) or "mixed")
            metrics["oracle_drivers"] = list(current.get("drivers") or status.get("drivers") or [])
            metrics["oracle_mentioned_symbols"] = list(current.get("mentioned_symbols") or status.get("mentioned_symbols") or [])
            metrics["oracle_summary_source"] = str(current.get("summary_source", status.get("summary_source", "")) or "")
            metrics["oracle_last_error"] = str(status.get("last_error", "") or "")
        if narrative:
            metrics["narrative_leader_count"] = int(narrative.get("count", 0) or 0)
            metrics["narrative_leaders"] = list((narrative.get("leaders") or {}).keys())[:8]

    if "event_edge_stats" in calculations:
        rows = evidence.get("live_experience") or []
        closed = [row for row in rows if row.get("outcome_pnl_pct") is not None]
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in closed:
            event_type = str(row.get("top_event_type_at_entry") or "unknown")
            pnl = float(row.get("outcome_pnl_pct", 0.0) or 0.0)
            bucket = grouped.setdefault(event_type, {"count": 0, "total_pnl_pct": 0.0})
            bucket["count"] += 1
            bucket["total_pnl_pct"] += pnl
        ranking = []
        for event_type, bucket in grouped.items():
            count = int(bucket["count"])
            total_pnl = float(bucket["total_pnl_pct"])
            avg_pnl = total_pnl / count if count else 0.0
            ranking.append({
                "event_type": event_type,
                "count": count,
                "avg_pnl_pct": round(avg_pnl, 2),
                "total_pnl_pct": round(total_pnl, 2),
            })
        ranking.sort(key=lambda item: (item["avg_pnl_pct"], item["total_pnl_pct"]))
        metrics["event_edge_ranking"] = ranking[:5]

    return metrics


def _format_evidence(bundle: Dict[str, Any]) -> str:
    plan = bundle.get("plan") or {}
    symbol = plan.get("symbol") or "portfolio"
    lines: List[str] = [
        f"Query plan: intent={plan.get('intent', '')}, symbol={symbol}, lookback_days={plan.get('lookback_days', '')}, limit={plan.get('limit', '')}",
        f"Datasets: {', '.join(plan.get('datasets', []))}",
        f"Calculations: {', '.join(plan.get('calculations', []))}",
    ]

    metrics = bundle.get("metrics") or {}
    if metrics:
        lines.append("Computed metrics:")
        for key, value in metrics.items():
            lines.append(f"- {key}: {value}")

    account = bundle.get("account_summary") or {}
    if account:
        lines.append(
            f"Account snapshot: equity={float(account.get('equity', 0.0) or 0.0):,.2f}, cash={float(account.get('cash', 0.0) or 0.0):,.2f}"
        )

    positions = bundle.get("current_positions") or []
    if positions:
        lines.append("Current positions:")
        for row in positions[:5]:
            lines.append(
                f"- {row.get('symbol', '')} qty={float(row.get('qty', 0.0) or 0.0):.6f} entry={row.get('avg_entry_price', '')} current={row.get('current_price', '')} unrealized={row.get('unrealized_pl', '')}"
            )
    elif plan.get("intent") in {"portfolio_status", "position_status"} and plan.get("symbol"):
        lines.append(f"Current positions: no open position found for {plan.get('symbol')}.")

    event_state = bundle.get("event_state") or {}
    indicators = bundle.get("current_indicators") or {}
    if event_state:
        lines.append(
            f"Event state: score {float(event_state.get('net_event_score', 0.0) or 0.0):+.2f}, bias {event_state.get('event_bias', 'neutral')}, veto={bool(event_state.get('hard_veto', False))}, top={event_state.get('top_event_type', '')}"
        )

    if indicators:
        lines.append(
            f"Indicator snapshot: RSI 15m={float(indicators.get('rsi_15m', 50.0) or 50.0):.1f}, RSI 1m={float(indicators.get('rsi_1m', 50.0) or 50.0):.1f}, trend_15m={indicators.get('trend_15m', 'unknown')}"
        )

    macro = bundle.get("macro_consensus") or {}
    if macro:
        lines.append(
            f"Macro consensus: {float(macro.get('risk_multiplier', 1.0) or 1.0):.2f} ({macro.get('market_regime', 'normal')})"
        )
        if macro.get("summary"):
            lines.append(f"Macro summary: {macro.get('summary')}")

    tracked_rows = bundle.get("tracked_symbols") or []
    if tracked_rows:
        lines.append("Tracked symbols:")
        for row in tracked_rows[:8]:
            lines.append(
                f"- {row.get('symbol', '')} state={row.get('active_state', 'idle')} saved={bool(row.get('saved_manual'))} tier={row.get('monitor_tier', '')} reason={row.get('active_reason', '')}"
            )

    decision_outcomes = bundle.get("decision_outcomes") or []
    if decision_outcomes:
        lines.append("Recent decision outcomes:")
        for row in decision_outcomes[:6]:
            lines.append(
                f"- {row.get('symbol', '')} {row.get('outcome_label', '')} horizon={_horizon_label(row.get('horizon_min', 0))} pnl={row.get('pnl_pct', 'n/a')}"
            )

    overlay = bundle.get("symbol_policy_overlay") or {}
    if overlay:
        lines.append(
            f"Policy overlay: score_delta={float(overlay.get('score_delta', 0.0) or 0.0):+.2f}, size_multiplier={float(overlay.get('size_multiplier', 1.0) or 1.0):.2f}, samples={int(overlay.get('decision_samples', 0) or 0)}"
        )

    brain = bundle.get("brain_self_score") or {}
    latest_brain = brain.get("latest") or {}
    if latest_brain:
        lines.append(
            f"Brain self score: score={float(latest_brain.get('score', 0.0) or 0.0):.2f}, confidence={float(latest_brain.get('confidence', 0.0) or 0.0):.2f}"
        )

    oracle_state = bundle.get("oracle_state") or {}
    if oracle_state:
        current = oracle_state.get("current") or {}
        status = oracle_state.get("status") or {}
        lines.append(
            f"Oracle state: score={float(current.get('risk_multiplier', status.get('score', 1.0)) or 1.0):.2f}, regime={current.get('market_regime', status.get('market_regime', 'normal'))}, breadth={current.get('breadth_state', status.get('breadth_state', 'mixed'))}, age_sec={int(status.get('age_sec', 0) or 0)}"
        )
        provider_status = oracle_state.get("news_provider_status") or {}
        if provider_status:
            lines.append(
                "Oracle provider status: "
                + ", ".join(
                    f"{name}={str((meta or {}).get('status', 'idle') or 'idle')}"
                    for name, meta in provider_status.items()
                )
            )

    for label, rows, formatter in (
        ("Recent actions", bundle.get("actions") or [], lambda row: f"- {row.get('action_type', 'action')} {row.get('side', '')} {row.get('symbol', '')} @ {row.get('price', '')} | {str(row.get('reason', ''))[:120]}"),
        ("Recent decision traces", bundle.get("decision_traces") or [], lambda row: f"- {row.get('decision', '')} score={float(row.get('final_score', 0.0) or 0.0):.1f} submitted={bool(row.get('submitted', 0))} block={row.get('block_reason', '')}"),
        ("Recent live experience", bundle.get("live_experience") or [], lambda row: f"- {row.get('strategy_used', '')} {row.get('side', '')} {row.get('symbol', '')} entry={row.get('entry_price', '')} pnl={row.get('outcome_pnl_pct', 'open')} event={row.get('event_score_at_entry', 0.0)}"),
        ("Recent news", bundle.get("news_events") or [], lambda row: f"- [{row.get('source', 'news')}] {row.get('event_type', 'headline')} {row.get('sentiment', 'neutral')} ({float(row.get('impact_score', 0.0) or 0.0):.0f}) {str(row.get('headline') or row.get('title') or '')[:120]}"),
        ("Live news", bundle.get("live_news") or [], lambda row: f"- [{row.get('source', 'live')}] {str(row.get('headline') or row.get('title') or '')[:120]}"),
        ("Latest reports", bundle.get("reports") or [], lambda row: f"- {row.get('report_type', '')}: {str(row.get('content', ''))[:160]}"),
    ):
        if rows:
            lines.append(label + ":")
            for row in rows[:5]:
                lines.append(formatter(row))

    live_news_meta = bundle.get("live_news_meta") or {}
    if live_news_meta:
        lines.append(
            f"Live news meta: status={live_news_meta.get('status', '')}, from_cache={bool(live_news_meta.get('from_cache'))}, cooldown_sec={live_news_meta.get('cooldown_sec', '')}"
        )

    llama_evidence = _llama_retrieval_module().format_context(bundle.get("llama_context") or {})
    if llama_evidence:
        lines.append(llama_evidence)

    return "\n".join(lines)


def _summarize_with_ollama(question: str, evidence_text: str, knowledge_text: str, draft_answer: str) -> Optional[str]:
    cfg = get_config().get("stocks", {}).get("crypto", {})
    model_name = str(cfg.get("telegram_ollama_model") or "qwen3:8b")
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are answering questions about a crypto bot using only retrieved local evidence and the provided bot knowledge. "
                    "Do not invent facts. Preserve all numeric facts. Write in concise natural language. "
                    "Use the draft answer as the default structure and improve clarity only when the evidence supports it.\n\n"
                    f"Bot knowledge:\n{knowledge_text}"
                ),
            },
            {
                "role": "user",
                "content": f"Question: {question}\n\nDraft answer:\n{draft_answer}\n\nEvidence:\n{evidence_text}",
            },
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 280},
    }
    try:
        response = httpx.post(ollama_url('/api/chat'), json=payload, timeout=18.0)
        response.raise_for_status()
        summary = str(((response.json().get('message') or {}).get('content')) or '').strip()
        # Strip <think> chains that qwen3:8b emits before the actual response
        summary = re.sub(r'<think>.*?</think>', '', summary, flags=re.DOTALL | re.IGNORECASE).strip()
        return summary or None
    except Exception as exc:
        logger.debug("[query_service] Ollama summarize failed: %s", exc)
        return None


def _wants_debug(question: str) -> bool:
    lower = str(question or "").lower()
    return any(term in lower for term in _DEBUG_TERMS)


def answer_query(question: str, use_llm: bool = True, debug: bool = False) -> str:
    prompt = str(question or "").strip()
    if not prompt:
        return "I need a question about trades, scores, reports, news, or current positions."

    plan = build_query_plan(prompt)
    evidence = _execute_plan(plan)
    evidence["metrics"] = _calculate_metrics(plan, evidence)
    if use_llm:
        llama_context = _llama_retrieval_module().retrieve_context(prompt)
        if llama_context:
            evidence["llama_context"] = llama_context
    evidence_text = _format_evidence(evidence)
    if debug or _wants_debug(prompt):
        return evidence_text

    knowledge_text = bot_knowledge.answer_context(prompt, plan, evidence)
    llama_knowledge = _llama_retrieval_module().format_context(evidence.get("llama_context") or {})
    if llama_knowledge:
        knowledge_text = f"{knowledge_text}\n\nLlamaIndex retrieval context:\n{llama_knowledge}"

    draft_answer = query_presenter.render_natural_response(prompt, evidence)
    final_answer = draft_answer
    if use_llm:
        summary = _summarize_with_ollama(prompt, evidence_text, knowledge_text, draft_answer)
        if summary:
            final_answer = summary
    build_query_scratchpad, critique_query_answer = _scratchpad_helpers()
    critique = critique_query_answer(prompt, evidence.get("metrics") or {}, final_answer)
    try:
        store.record_research_scratchpad_sync(
            channel="telegram",
            question=prompt,
            intent=str(plan.get("intent", "") or ""),
            critique_verdict=str(critique.get("verdict", "") or ""),
            confidence=float(critique.get("confidence", 0.0) or 0.0),
            payload=build_query_scratchpad(
                question=prompt,
                intent=str(plan.get("intent", "") or ""),
                entity_resolution=entity_resolver.resolve_entities(prompt),
                datasets=list(plan.get("datasets") or []),
                calculations=list(plan.get("calculations") or []),
                evidence_summary={
                    "symbol": plan.get("symbol", ""),
                    "datasets": list(plan.get("datasets") or []),
                    "has_live_news": bool(plan.get("include_live_news", False)),
                },
                computed_metrics=evidence.get("metrics") or {},
                critique=critique,
                final_answer=final_answer,
            ),
        )
    except Exception as exc:
        logger.debug("[query_service] scratchpad persist failed: %s", exc)
    return final_answer


__all__ = ["answer_query", "build_query_plan", "_extract_symbol", "_format_evidence"]
