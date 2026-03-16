"""
gemini_client.py — Centralized Gemini API client with per-model rate limiting
and dual API key rotation.

ALL Gemini API calls in the project MUST go through this module.

Dual-key strategy:
  - GEMINI_API_KEY  (Key 1): 40 RPD total (20 Flash + 20 Flash Lite)
  - GEMINI_API_KEY2 (Key 2): 40 RPD total (20 Flash + 20 Flash Lite)
  Combined: 80 requests per day with automatic key rotation.
  When Key 1 exhausts all its model budgets, rotates to Key 2 and back.

Model rotation strategy (per key):
  - gemini-2.5-flash:      5 RPM,  20 RPD  (used first for quality)
  - gemini-2.5-flash-lite: 10 RPM, 20 RPD  (fallback when Flash exhausted)

Rate limiting:
  - Flash:      12.5s minimum gap  (60s / 5 RPM + 0.5s buffer)
  - Flash Lite:  6.5s minimum gap  (60s / 10 RPM + 0.5s buffer)
  - 429 → stamps a 65-second penalty into that model+key slot.

Thread-safe: all counters use a single threading.Lock().
State persists to disk so uvicorn --reload doesn't reset the clock.
"""

import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Model definitions ────────────────────────────────────────────────────────
_MODELS: Dict[str, Dict[str, Any]] = {
    "gemini-2.5-flash": {
        "min_gap_sec": 12.5,
        "rpd_budget": 20,
        "priority": 0,
    },
    "gemini-2.5-flash-lite": {
        "min_gap_sec": 6.5,
        "rpd_budget": 20,
        "priority": 1,
    },
}

# ── API key slots ─────────────────────────────────────────────────────────────
# Key 1 = GEMINI_API_KEY, Key 2 = GEMINI_API_KEY2
# Each key gets its own independent per-model budget + timestamps.
_KEY_IDS = ["key1", "key2"]

# ── Process-wide state ────────────────────────────────────────────────────────
_lock = threading.Lock()
_daily_reset_ts: float = 0.0

# Per-key, per-model state: {"key1": {"gemini-2.5-flash": {...}, ...}, "key2": {...}}
_model_state: Dict[str, Dict[str, Dict[str, Any]]] = {
    key_id: {
        model: {"last_call_ts": 0.0, "daily_count": 0}
        for model in _MODELS
    }
    for key_id in _KEY_IDS
}

# ── Persistent state file ─────────────────────────────────────────────────────
_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
)
_STATE_FILE = os.path.join(_STATE_DIR, "gemini_state.json")


def _load_state() -> None:
    """Seed in-memory counters from disk on module load."""
    global _daily_reset_ts
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE) as f:
                s = json.load(f)
            _daily_reset_ts = float(s.get("daily_reset_ts", 0.0))
            if time.time() - _daily_reset_ts >= 86400:
                _daily_reset_ts = time.time()
                for kid in _KEY_IDS:
                    for m in _model_state[kid]:
                        _model_state[kid][m]["daily_count"] = 0
            else:
                per_key = s.get("per_key", {})
                for kid in _KEY_IDS:
                    per_model = per_key.get(kid, {})
                    for m, state in per_model.items():
                        if m in _model_state.get(kid, {}):
                            _model_state[kid][m]["last_call_ts"] = float(state.get("last_call_ts", 0.0))
                            _model_state[kid][m]["daily_count"] = int(state.get("daily_count", 0))
    except Exception:
        pass


def _save_state() -> None:
    """Persist current counters to disk (must be called inside _lock)."""
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump({
                "daily_reset_ts": _daily_reset_ts,
                "per_key": {
                    kid: {
                        m: {
                            "last_call_ts": _model_state[kid][m]["last_call_ts"],
                            "daily_count": _model_state[kid][m]["daily_count"],
                        }
                        for m in _model_state[kid]
                    }
                    for kid in _KEY_IDS
                },
            }, f)
    except Exception:
        pass


def _reset_daily_if_needed(now: float) -> None:
    """Reset all model daily counts if 24h have passed. Must be called inside _lock."""
    global _daily_reset_ts
    if now - _daily_reset_ts >= 86400:
        _daily_reset_ts = now
        for kid in _KEY_IDS:
            for m in _model_state[kid]:
                _model_state[kid][m]["daily_count"] = 0
        logger.info("[Gemini] Daily counters reset for all keys and models.")


def _get_available_slot(requested_model: str) -> Optional[tuple]:
    """
    Return (key_id, model) for the best available slot, respecting per-key RPD budgets.
    Priority: Key1 Flash → Key1 Flash Lite → Key2 Flash → Key2 Flash Lite.
    Returns None if all keys+models exhausted for the day.
    Must be called inside _lock.
    """
    env_keys = {
        "key1": os.environ.get("GEMINI_API_KEY", ""),
        "key2": os.environ.get("GEMINI_API_KEY2", ""),
    }
    for kid in _KEY_IDS:
        if not env_keys[kid]:  # Skip unconfigured keys
            continue
        # Try requested model first, then fall back by priority
        candidates = [requested_model] + [
            m for m in sorted(_MODELS, key=lambda x: _MODELS[x]["priority"])
            if m != requested_model
        ]
        for m in candidates:
            budget = _MODELS[m]["rpd_budget"]
            if _model_state[kid][m]["daily_count"] < budget:
                if m != requested_model or kid != "key1":
                    logger.info(
                        f"[Gemini] Rotated to {kid}/{m} "
                        f"(requested {requested_model} on key1 exhausted)."
                    )
                return (kid, m)
    return None


# ── Boot state load ───────────────────────────────────────────────────────────
_load_state()


def call_gemini(
    prompt: str,
    system: str = "",
    max_tokens: int = 512,
    temperature: float = 0.4,
    json_mode: bool = False,
    model: str = "gemini-2.5-flash",
    use_search: bool = False,
) -> str:
    """
    Make a Gemini API call with per-model rate limiting and daily budget enforcement.

    Automatically rotates to Flash Lite if Flash's 20 RPD budget is exhausted.
    Combined capacity: 40 requests per day (20 Flash + 20 Flash Lite).

    Returns raw text response. Raises on persistent failure.
    """
    # ── Select key+model slot & check budget ─────────────────────────────────
    with _lock:
        now = time.time()
        _reset_daily_if_needed(now)
        slot = _get_available_slot(model)
        if slot is None:
            total = sum(
                _MODELS[m]["rpd_budget"] for m in _MODELS
            ) * len(_KEY_IDS)
            logger.warning("[Gemini] All keys and models exhausted for today. Resets at midnight UTC.")
            return "[Gemini daily budget reached — resets at midnight UTC]"

        active_key_id, active_model = slot
        _model_state[active_key_id][active_model]["daily_count"] += 1
        budget = _MODELS[active_model]["rpd_budget"]
        count = _model_state[active_key_id][active_model]["daily_count"]

    api_key = os.environ.get("GEMINI_API_KEY" if active_key_id == "key1" else "GEMINI_API_KEY2", "")
    if not api_key:
        return "[Gemini API key not configured]"

    logger.debug(f"[Gemini] {active_key_id}/{active_model} — call {count}/{budget} RPD today.")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{active_model}:generateContent?key={api_key}"
    )
    gen_config: Dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
    }
    if json_mode and not use_search:
        gen_config["responseMimeType"] = "application/json"

    full_text = (system + "\n\n" + prompt) if system else prompt
    payload = {
        "contents": [{"role": "user", "parts": [{"text": full_text}]}],
        "generationConfig": gen_config,
    }
    if use_search:
        payload["tools"] = [{"googleSearch": {}}]

    data = json.dumps(payload).encode("utf-8")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # One retry: wait the 429 penalty, then try again
    _429_PENALTY_SEC = 65.0
    last_err: Optional[Exception] = None

    for attempt in range(2):
        # ── Per-model rate limiter ─────────────────────────────────────────
        min_gap = _MODELS[active_model]["min_gap_sec"]
        with _lock:
            now = time.time()
            elapsed = now - _model_state[active_key_id][active_model]["last_call_ts"]
            if elapsed < min_gap:
                wait = min_gap - elapsed
                logger.debug(f"[Gemini/{active_model}] Rate limiter: waiting {wait:.1f}s")
                time.sleep(wait)
            _model_state[active_key_id][active_model]["last_call_ts"] = time.time()
            _save_state()

        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            outer = json.loads(raw)
            # Gemini 2.5 Flash may include "thinking" parts without text parts.
            parts = outer["candidates"][0]["content"]["parts"]
            text = ""
            for part in reversed(parts):
                if "text" in part:
                    text = part["text"]
                    break
            if not text:
                raise ValueError(f"No text in Gemini response parts: {parts}")
            logger.debug(
                f"[Gemini/{active_model}] Call succeeded ({len(text)} chars, attempt {attempt + 1})"
            )
            return text

        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                logger.warning(
                    f"[Gemini] {active_key_id}/{active_model} 429 hit — stamping {_429_PENALTY_SEC}s penalty."
                )
                with _lock:
                    _model_state[active_key_id][active_model]["last_call_ts"] = time.time() + _429_PENALTY_SEC
                    _save_state()
                time.sleep(_429_PENALTY_SEC)
                last_err = e
            else:
                raise
        except Exception:
            raise

    raise last_err or RuntimeError(f"Gemini call failed after all retries ({active_model})")


def call_gemini_json(
    prompt: str,
    system: str = "",
    max_tokens: int = 256,
    temperature: float = 0.3,
    model: str = "gemini-2.5-flash",
    use_search: bool = False,
) -> dict:
    """Convenience wrapper: calls Gemini and parses JSON from the response."""
    text = call_gemini(
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=True,
        model=model,
        use_search=use_search,
    )

    text = text.strip()

    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    import re as _re
    fence_match = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    # Robustly extract JSON object, ignoring any remaining surrounding text
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
        try:
            return json.loads(text[start_idx : end_idx + 1])
        except json.JSONDecodeError:
            pass

    logger.warning(f"[Gemini] Failed to parse JSON: {text[:120]}")
    return {}


def get_gemini_usage() -> dict:
    """Return per-key, per-model usage stats for diagnostics."""
    with _lock:
        now = time.time()
        secs_since_reset = now - _daily_reset_ts
        hours_until_reset = max(0.0, (86400 - secs_since_reset) / 3600)
        env_keys = {
            "key1": bool(os.environ.get("GEMINI_API_KEY", "")),
            "key2": bool(os.environ.get("GEMINI_API_KEY2", "")),
        }
        per_key = {}
        total_calls = 0
        total_budget = 0
        total_remaining = 0
        for kid in _KEY_IDS:
            if not env_keys[kid]:
                continue
            key_calls = 0
            key_budget = 0
            key_remaining = 0
            models_out = {}
            for m in _MODELS:
                c = _model_state[kid][m]["daily_count"]
                b = _MODELS[m]["rpd_budget"]
                r = max(0, b - c)
                models_out[m.replace("gemini-2.5-", "")] = {"calls": c, "budget": b, "remaining": r}
                key_calls += c
                key_budget += b
                key_remaining += r
            per_key[kid] = {"models": models_out, "calls_today": key_calls, "budget": key_budget, "remaining": key_remaining}
            total_calls += key_calls
            total_budget += key_budget
            total_remaining += key_remaining
        return {
            "per_key": per_key,
            "total_calls_today": total_calls,
            "total_budget": total_budget,
            "total_remaining": total_remaining,
            "resets_in_hours": round(hours_until_reset, 1),
        }
