"""
Apex NL Router - Ollama (qwen3:8b) Command Parser
===================================================
Converts user's natural language Telegram messages into structured JSON
commands that the bot_process can execute safely.

Usage:
    from integrations.telegram.nl_router import parse_intent
    result = parse_intent("sell half my ETH")
    # -> {"action": "sell", "symbol": "ETH/USD", "amount": "half"}
"""

import json
import logging
import re
from typing import Any, Dict

import httpx

from core.config_manager import get_config
from core.ollama import ollama_url
from integrations.telegram import entity_resolver

logger = logging.getLogger("apex.telegram.nl_router")

_SYSTEM_PROMPT = """You are the command parser for an automated crypto trading bot called Apex.
The user will send you a natural language message.
Your ONLY job is to output a single valid JSON object classifying the intent.

VALID ACTIONS and their JSON schemas:
  1. {"action": "status"}
  2. {"action": "sell", "symbol": "<SYMBOL/USD>", "amount": "<all|half|<number>>"}
  3. {"action": "buy", "symbol": "<SYMBOL/USD>", "notional": <float>}
  4. {"action": "pause"}
  5. {"action": "resume"}
  6. {"action": "positions"}
  7. {"action": "close_all"}
  8. {"action": "query", "question": "<text>"}
  9. {"action": "chat", "reply": "<text>"}
  10. {"action": "unknown"}

RULES:
- Output ONLY the JSON object. No explanation, no markdown, no extra text.
- For symbols, always format as BASE/USD (e.g. ETH/USD, BTC/USD, SOL/USD).
- "amount" for sell can be "all", "half", or a numeric string like "0.5".
- If the user says "pause", "stop", "turn off", "disable" -> action: pause
- If the user says "resume", "start", "turn on", "enable" -> action: resume
- If the user says "how are we", "what's my portfolio", "status", "check" -> action: status
- If the user says "positions", "what am I holding", "my trades" -> action: positions
- If the user asks about history, reports, decision traces, scores, or news impact -> action: query with the full question in the "question" field
- If the user is just chatting or asking a question (e.g., "do sentences work now?", "hello") -> action: chat
- If intent is totally unreadable -> action: unknown
"""

_GREETING_RE = re.compile(r"^(hi|hello|hey|yo|sup|good morning|good afternoon|good evening)\b", re.IGNORECASE)
_RESERVED_TERMS = {
    "RSI", "EMA", "MACD", "ATR", "VWAP", "BB", "BOLLINGER", "BANDS", "PNL", "DCA",
    "GOVERNOR", "MODE", "PRESSURE", "ORACLE", "SCORE", "SCORES", "REPORT", "REPORTS",
    "TRACKED", "LEARNING", "BLOCKED", "CARD", "BREADTH",
}


def _has_configured_symbol(prompt: str) -> bool:
    entities = entity_resolver.resolve_entities(prompt)
    return bool(entities.get("symbols"))


def _extract_json_dict(raw_text: str) -> Dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        return {}

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    decoder = json.JSONDecoder()
    normalized = text.replace("\r", " ").replace("\n", " ")

    for candidate_text in (text, normalized):
        for start, char in enumerate(candidate_text):
            if char != "{":
                continue
            try:
                parsed, _end = decoder.raw_decode(candidate_text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _fallback_intent(user_message: str) -> Dict[str, Any]:
    prompt = user_message.strip()
    if not prompt:
        return {"action": "unknown"}
    if _GREETING_RE.match(prompt):
        return {"action": "chat", "reply": "Hi. How can I help with the bot?"}
    if "?" in prompt:
        return {"action": "chat", "reply": "I can help with bot status, positions, trades, reports, and past decisions."}
    return {"action": "unknown"}


def _normalize_parsed_intent(parsed: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    action = str(parsed.get("action") or "").strip().lower()
    if action == "chat":
        reply = str(parsed.get("reply") or "").strip()
        if not reply or reply.lower() == prompt.lower() or reply.lower() in {"hi", "hello", "hey"}:
            parsed = dict(parsed)
            parsed["reply"] = "Hi. How can I help with the bot?"
    return parsed


def _classify_fast_path(prompt: str) -> Dict[str, Any] | None:
    lower_prompt = prompt.lower().strip()
    compact_prompt = re.sub(r"[^a-z0-9 ]+", " ", lower_prompt)
    compact_prompt = re.sub(r"\s+", " ", compact_prompt).strip()

    status_phrases = (
        "status", "portfolio", "check", "whats my status", "what is my status",
        "whats the status", "what is the status", "how are we", "hows the bot",
        "what is my portfolio", "whats my portfolio"
    )
    positions_phrases = (
        "positions", "open positions", "what am i holding", "what are my positions",
        "my holdings", "holdings"
    )
    performance_phrases = (
        "performance", "pnl", "returns", "how much did i make", "chart", "equity",
        "how are we doing", "how did we do", "how am i doing"
    )
    pause_phrases = ("pause", "stop", "halt", "stop bot", "pause bot", "turn off", "disable")
    resume_phrases = ("resume", "start", "start bot", "resume bot", "turn on", "enable")
    close_all_phrases = ("close all", "liquidate", "sell all", "exit all")
    query_symbol_prefixes = ("what is my ", "whats my ", "how is my ", "tell me about ", "what about ", "update on ", "what is the ", "whats the ")

    if compact_prompt in status_phrases or any(phrase in compact_prompt for phrase in status_phrases):
        return {"action": "status"}
    if compact_prompt in positions_phrases or any(phrase in compact_prompt for phrase in positions_phrases):
        return {"action": "positions"}
    if compact_prompt in performance_phrases or any(phrase in compact_prompt for phrase in performance_phrases):
        return {"action": "performance"}
    if compact_prompt in pause_phrases or any(phrase == compact_prompt for phrase in pause_phrases):
        return {"action": "pause"}
    if compact_prompt in resume_phrases or any(phrase == compact_prompt for phrase in resume_phrases):
        return {"action": "resume"}
    if compact_prompt in close_all_phrases or any(phrase in compact_prompt for phrase in close_all_phrases):
        return {"action": "close_all"}
    indicator_terms = ("rsi", "ema", "macd", "atr", "vwap", "bollinger", "bb ", "governor", "pressure")
    config_query_terms = ("current brain", "active brain", "brain mode", "what brain", "which brain")
    if any(term in f" {compact_prompt} " for term in indicator_terms):
        return {"action": "query", "question": prompt}
    if any(term in compact_prompt for term in config_query_terms):
        return {"action": "query", "question": prompt}
    if _has_configured_symbol(prompt):
        if compact_prompt.startswith("my ") or any(compact_prompt.startswith(prefix) for prefix in query_symbol_prefixes):
            if not any(term in compact_prompt for term in ("buy ", "sell ", "close ", "pause", "resume")):
                return {"action": "query", "question": prompt}
    return None


def parse_intent(user_message: str) -> Dict[str, Any]:
    """
    Parse a natural language message into a structured command dict using qwen3:8b.
    Falls back to chat/unknown on malformed model output.
    """
    prompt = user_message.strip()
    if not prompt:
        return {"action": "unknown"}

    lower_prompt = prompt.lower()
    fast_path = _classify_fast_path(prompt)
    if fast_path is not None:
        return fast_path

    query_terms = (
        "history", "past", "yesterday", "why", "score", "scores", "news", "event", "events",
        "decision", "decisions", "trace", "traces", "report", "reports", "rsi", "ema", "macd",
        "atr", "vwap", "bollinger", "band", "bands", "governor", "pressure", "mode", "brain",
        "tracked", "learning", "blocked", "oracle", "breadth", "card"
    )
    if any(term in lower_prompt for term in query_terms):
        return {"action": "query", "question": prompt}

    try:
        cfg = get_config().get("stocks", {}).get("crypto", {})
        model_name = str(cfg.get("telegram_ollama_model") or "qwen3:8b")
    except Exception:
        model_name = "qwen3:8b"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.0,
            "num_predict": 160,
        },
    }

    raw = ""
    try:
        resp = httpx.post(
            ollama_url("/api/chat"),
            json=payload,
            timeout=12.0,
        )
        resp.raise_for_status()
        body = resp.json()
        raw = str(((body.get("message") or {}).get("content")) or "").strip()
        parsed = _extract_json_dict(raw)
        if not isinstance(parsed, dict) or "action" not in parsed:
            fallback = _fallback_intent(prompt)
            log_fn = logger.info if fallback.get("action") != "unknown" else logger.warning
            log_fn(f"[NLRouter] Unexpected JSON shape from model={model_name}: {raw!r}")
            return fallback

        parsed = _normalize_parsed_intent(parsed, prompt)
        logger.info(f"[NLRouter] model={model_name} prompt={prompt!r} -> {parsed}")
        return parsed
    except Exception as exc:
        logger.warning(f"[NLRouter] Ollama parser failed for model={model_name}: {exc}. Raw: {raw!r}")
        return _fallback_intent(prompt)
