"""Typed entity resolution for Telegram bot questions."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from core.config_manager import get_config

_TOKEN_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]{1,14})\b")

_INDICATORS = {
    "RSI", "EMA", "MACD", "ATR", "VWAP", "BB", "BOLLINGER", "BANDS",
}
_BOT_CONCEPTS = {
    "BRAIN", "GOVERNOR", "PRESSURE", "MODE", "RISK", "ORACLE", "REPORT",
    "REPORTS", "STATUS", "ACCOUNT", "PORTFOLIO", "EVENT_STATE", "LIVE_EXPERIENCE",
}
_STRATEGY_TERMS = {
    "MEAN_REVERSION", "SWING_MOMENTUM", "DYNAMIC_DCA", "BREAKOUT_MOMENTUM",
    "POSITION_GOLDEN_CROSS", "MICRO_SCALPER",
}
_TIME_TERMS = {
    "TODAY", "YESTERDAY", "NOW", "CURRENT", "LATEST", "WEEK", "MONTH",
}
_STOPWORDS = {
    "WHAT", "WHATS", "WHY", "WHEN", "HOW", "SHOW", "LAST", "MY", "THE", "FOR",
    "WITH", "DID", "OUR", "THIS", "THAT", "ARE", "FROM", "PAST", "WE", "BUY",
    "SELL", "HOLD", "BOUGHT", "SOLD", "IS", "ACTIVE", "CURRENT", "BRAIN", "YOUR",
}
_COMMON_ALIASES = {
    "BITCOIN": "BTC/USD",
    "BTC": "BTC/USD",
    "ETHER": "ETH/USD",
    "ETHEREUM": "ETH/USD",
    "ETH": "ETH/USD",
    "SOLANA": "SOL/USD",
    "SOL": "SOL/USD",
    "DOGECOIN": "DOGE/USD",
    "DOGE": "DOGE/USD",
    "CARDANO": "ADA/USD",
    "ADA": "ADA/USD",
    "CHAINLINK": "LINK/USD",
    "LINK": "LINK/USD",
}


def _configured_symbol_aliases() -> Dict[str, str]:
    cfg_symbols = get_config().get("stocks", {}).get("crypto", {}).get("symbols", []) or []
    aliases: Dict[str, str] = dict(_COMMON_ALIASES)
    for symbol in cfg_symbols:
        normalized = str(symbol or "").upper().strip()
        if not normalized:
            continue
        if "/" not in normalized:
            normalized = f"{normalized}/USD"
        base = normalized.split("/")[0]
        aliases.setdefault(base, normalized)
    return aliases


def resolve_entities(text: str) -> Dict[str, Any]:
    prompt = str(text or "")
    upper = prompt.upper()
    aliases = _configured_symbol_aliases()

    indicators: List[str] = []
    concepts: List[str] = []
    strategies: List[str] = []
    times: List[str] = []
    symbols: List[str] = []

    for raw_token in _TOKEN_RE.findall(prompt):
        token = raw_token.upper()
        if token in _INDICATORS and token not in indicators:
            indicators.append(token)
            continue
        if token in _BOT_CONCEPTS and token not in concepts:
            concepts.append(token)
            continue
        if token in _STRATEGY_TERMS and token not in strategies:
            strategies.append(token)
            continue
        if token in _TIME_TERMS and token not in times:
            times.append(token)
            continue
        if token in aliases and token not in _STOPWORDS:
            symbol = aliases[token]
            if symbol not in symbols:
                symbols.append(symbol)

    if not symbols:
        for alias, symbol in aliases.items():
            if alias in _STOPWORDS:
                continue
            if re.search(rf"\b{re.escape(alias)}\b", upper):
                if symbol not in symbols:
                    symbols.append(symbol)

    return {
        "symbols": symbols,
        "indicators": indicators,
        "concepts": concepts,
        "strategies": strategies,
        "time_terms": times,
    }


def primary_symbol(text: str) -> str:
    entities = resolve_entities(text)
    return entities["symbols"][0] if entities["symbols"] else ""
