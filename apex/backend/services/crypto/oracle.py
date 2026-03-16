"""
oracle.py — AI News Sentiment Oracle
Calls Gemini Flash API once per hour to evaluate crypto market sentiment.
Returns a `gemini_macro` multiplier (0.2 - 1.8) that is:
  - Injected into the bot's position sizing formula in real time
  - Written to the `oracle_history` SQLite table for future ML retraining

Design goals:
  - Zero API cost: Free Gemini Flash tier, max 1 call per hour
  - Hardware-safe: All AI compute is on Google cloud, not local
  - Persistent memory: Scores are logged so the neural network can
    retroactively learn how news sentiment correlated to price action
"""

import json
import logging
import os
import re
import time
import threading
import urllib.request
import urllib.parse
import ssl

logger = logging.getLogger(__name__)

# ─── In-memory Cache ────────────────────────────────────────────────────────
# Updated at most once per CACHE_TTL_SEC seconds to stay within API limits
_CACHE_TTL_SEC = 3600  # 60 min — 24 calls/day (well within 80 RPD dual-key budget)
_cache_lock = threading.Lock()
_cached_score: float = 1.0         # Neutral default
_cached_regime: str = "normal"     # Persists the last Gemini-provided market regime
_cached_summary: str = ""          # Persists the last Gemini-provided summary
_cached_breadth_state: str = "mixed"
_cached_drivers: list[str] = []
_cached_mentioned_symbols: list[str] = []
_cached_summary_source: str = "default"
_cached_at_ts: float = 0.0         # Unix timestamp of last update
_last_error: str = ""
_refresh_in_progress: bool = False  # Prevents duplicate concurrent API calls
_restored_from_db: bool = False     # True after first-boot DB restore has run


def _restore_oracle_from_db() -> None:
    """
    Restore in-memory oracle cache from SQLite on first boot.
    Reads the latest oracle_history row (score + summary) so the dashboard
    and bot don't start with a neutral blank state after a restart.
    Also restores the narrative leaders from signal_state.
    """
    global _cached_score, _cached_regime, _cached_summary, _cached_breadth_state
    global _cached_drivers, _cached_mentioned_symbols, _cached_summary_source
    global _cached_at_ts, _restored_from_db
    global _narrative_leaders, _narrative_cached_at

    with _cache_lock:
        if _restored_from_db:
            return
        _restored_from_db = True

    try:
        from . import store
        macro_row = store.get_latest_macro_consensus_sync() or {}
        payload = dict(macro_row.get("payload") or {})
        if macro_row:
            with _cache_lock:
                _cached_breadth_state = _normalize_breadth_state(payload.get("breadth_state"))
                _cached_drivers = _normalize_driver_list(payload.get("drivers"))
                _cached_mentioned_symbols = _normalize_symbol_list(payload.get("mentioned_symbols"))
                _cached_summary_source = str(payload.get("summary_source", "db_restore") or "db_restore")
        # ── Restore oracle score + summary ───────────────────────────────
        row = store.get_latest_oracle_score_sync()  # {score, summary, ts}
        if row:
            score = float(row.get("score", 1.0))
            summary = str(row.get("summary", ""))
            ts = float(row.get("ts", 0)) / 1000  # ms → seconds
            regime = str(macro_row.get("market_regime", "") or "") if macro_row else ""
            regime = _normalize_market_regime(regime, score)
            with _cache_lock:
                _cached_score = score
                _cached_regime = regime
                _cached_summary = summary
                _cached_at_ts = ts
            logger.info(f"🔄 Oracle restored from DB: score={score:.2f} regime={regime} (age={(time.time()-ts)/3600:.1f}h)")

        # ── Restore narrative leaders ─────────────────────────────────────
        with _narrative_lock:
            state = store._get_signal_state_sync("narrative_leaders")
            raw = state.get("narrative_leaders")
            if raw:
                import json as _json
                try:
                    loaded = _json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(loaded, dict):
                        _narrative_leaders = loaded
                        _narrative_cached_at = float(state.get("narrative_cached_at", 0))
                        logger.info(f"🔄 Narrative restored from DB: {list(loaded.keys())}")
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Oracle DB restore failed (non-fatal, will use defaults): {e}")


# ─── Prompt Engineering ─────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are a savant crypto quantitative analyst monitoring global news flow.
Your job: given the latest news context, rate overall market sentiment from 0.2 to 1.8 and provide highly specific intelligence about broad crypto market consensus.

Rating scale:
  0.2 - 0.5  = Extreme Fear (crashes, hacks, regulatory bans, macro recession)
  0.6 - 0.9  = Fear / Caution (negative sentiment, bearish signals)
  1.0         = Neutral (no major market-moving news)
  1.1 - 1.4  = Greed / Optimism (positive progress, ETF news, adoption)
  1.5 - 1.8  = Extreme Greed (massive bull run, halving, institutional FOMO)

Respond ONLY with a single JSON object (no markdown formatting).
Use this exact structure:
{"score": <float_between_0.2_and_1.8>, "market_regime": "<panic_crash|bear|normal|bull|extreme_greed>", "summary": "Detailed 2-3 sentence reason", "breadth_state": "<broad|majors_led|btc_led|mixed|risk_off>", "drivers": ["driver 1", "driver 2"], "mentioned_symbols": ["BTC", "ETH"]}

CRITICAL: Do NOT use generic phrases like 'cautiously optimistic', 'regulatory uncertainty', or 'macro headwinds'. State exactly WHAT happened in the news today. Cite the specific catalyst driving your score, referencing names like Powell, ETF flows, exchange listings, hacks, or on-chain events.
CRITICAL: Assess whether the move is broad across crypto, concentrated in BTC, led by majors like ETH/SOL, mixed, or clearly risk-off. Do not use BTC alone as a proxy for the whole market unless the evidence is genuinely BTC-led.
"""

_USER_PROMPT = """What is the current macro sentiment for crypto markets right now?
Identify the top 1-2 actionable news events from the last 6 hours. Name specific coins, people, or macroeconomic data points.
Also classify whether the move is broad, majors-led, BTC-led, mixed, or risk-off. Mention non-BTC leaders or laggards when they materially matter.
Keep the summary to 40-50 words maximum. Pick the market_regime that best fits your score."""

def _derive_market_regime(score: float) -> str:
    if score <= 0.4:
        return "panic_crash"
    if score <= 0.7:
        return "bear"
    if score >= 1.5:
        return "extreme_greed"
    if score >= 1.2:
        return "bull"
    return "normal"


def _normalize_market_regime(raw_regime: str, score: float) -> str:
    normalized = str(raw_regime or "").strip().lower()
    aliases = {
        "panic": "panic_crash",
        "panic crash": "panic_crash",
        "panic_crash": "panic_crash",
        "extreme_fear": "panic_crash",
        "fear": "bear",
        "bearish": "bear",
        "risk_off": "bear",
        "caution": "bear",
        "cautious": "bear",
        "negative": "bear",
        "normal": "normal",
        "neutral": "normal",
        "mixed": "normal",
        "sideways": "normal",
        "bull": "bull",
        "bullish": "bull",
        "positive": "bull",
        "greed": "bull",
        "optimistic": "bull",
        "extreme_greed": "extreme_greed",
        "euphoria": "extreme_greed",
    }
    return aliases.get(normalized, _derive_market_regime(score))


def _normalize_breadth_state(raw_value: str) -> str:
    normalized = str(raw_value or "").strip().lower()
    aliases = {
        "broad": "broad",
        "broad_based": "broad",
        "broad-based": "broad",
        "majors": "majors_led",
        "majors_led": "majors_led",
        "majors-led": "majors_led",
        "btc": "btc_led",
        "btc_led": "btc_led",
        "btc-led": "btc_led",
        "bitcoin_led": "btc_led",
        "bitcoin-led": "btc_led",
        "mixed": "mixed",
        "neutral": "mixed",
        "risk_off": "risk_off",
        "risk-off": "risk_off",
        "defensive": "risk_off",
    }
    return aliases.get(normalized, "mixed")


def _normalize_driver_list(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    normalized: list[str] = []
    for item in items:
        text = " ".join(str(item or "").split()).strip()
        if text and text not in normalized:
            normalized.append(text[:120])
    return normalized[:3]


def _normalize_symbol_list(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    normalized: list[str] = []
    for item in items:
        token = str(item or "").upper().replace("/USD", "").strip()
        token = re.sub(r"[^A-Z0-9]", "", token)
        if 2 <= len(token) <= 12 and token not in normalized:
            normalized.append(token)
    return normalized[:8]


def _fallback_oracle_summary(
    score: float,
    market_regime: str,
    breadth_state: str,
    drivers: list[str],
    mentioned_symbols: list[str],
    previous_summary: str,
) -> tuple[str, str]:
    if previous_summary:
        return previous_summary, "fallback_cached"
    if drivers:
        breadth_text = {
            "broad": "broad across majors and alts",
            "majors_led": "led by larger-cap majors",
            "btc_led": "narrow and mostly Bitcoin-led",
            "mixed": "mixed across the market",
            "risk_off": "risk-off across crypto",
        }.get(breadth_state, "mixed across crypto")
        symbols_text = f" Key symbols: {', '.join(mentioned_symbols[:4])}." if mentioned_symbols else ""
        return f"Crypto macro is {market_regime} with {breadth_text}. Drivers: {'; '.join(drivers[:2])}.{symbols_text}", "fallback_structured"
    return f"Crypto macro is {market_regime} with a Gemini oracle score of {score:.2f}. Breadth currently reads {breadth_state.replace('_', ' ')}.", "fallback_default"


def _normalize_oracle_payload(result: dict) -> dict:
    raw_score = float(result.get("score", 1.0))
    score = max(0.2, min(1.8, raw_score))
    market_regime = _normalize_market_regime(result.get("market_regime", ""), score)
    breadth_state = _normalize_breadth_state(result.get("breadth_state", ""))
    drivers = _normalize_driver_list(result.get("drivers"))
    mentioned_symbols = _normalize_symbol_list(result.get("mentioned_symbols"))
    summary = " ".join(str(result.get("summary", "")).split())
    with _cache_lock:
        previous_summary = _cached_summary
    if not summary:
        summary, summary_source = _fallback_oracle_summary(
            score,
            market_regime,
            breadth_state,
            drivers,
            mentioned_symbols,
            previous_summary,
        )
    else:
        summary_source = "fresh_gemini"
    return {
        "score": score,
        "market_regime": market_regime,
        "summary": summary,
        "breadth_state": breadth_state,
        "drivers": drivers,
        "mentioned_symbols": mentioned_symbols,
        "summary_source": summary_source,
    }


def _extract_first_json_object(raw_text: str) -> dict:
    text = str(raw_text or "").strip()
    if not text:
        return {}

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)

    normalized = text.replace("\r", " ").replace("\n", " ")
    decoder = json.JSONDecoder()
    for candidate_text in (text, normalized):
        for start, char in enumerate(candidate_text):
            if char != "{":
                continue
            try:
                candidate, _end = decoder.raw_decode(candidate_text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                return candidate
    return {}


def _extract_oracle_fields(raw_text: str) -> dict:
    text = " ".join(str(raw_text or "").replace("\r", " ").replace("\n", " ").split())
    if not text:
        return {}

    score_match = re.search(r'"score"\s*:\s*([-+]?[0-9]*\.?[0-9]+)', text)
    regime_match = re.search(r'"market_regime"\s*:\s*"([^"]+)"', text)
    summary_match = re.search(r'"summary"\s*:\s*"(.*?)"', text)
    if not score_match:
        return {}

    parsed = {"score": float(score_match.group(1))}
    if regime_match:
        parsed["market_regime"] = regime_match.group(1)
    if summary_match:
        parsed["summary"] = summary_match.group(1)
    return parsed



def _call_gemini_api(api_key: str) -> dict:
    """Make a Gemini API call through the centralized rate-limited client."""
    from .gemini_client import call_gemini

    raw_text = call_gemini(
        prompt=_USER_PROMPT,
        system=_SYSTEM_PROMPT,
        max_tokens=800,
        temperature=0.8,
        model="gemini-2.5-flash",
        use_search=True,
    )

    logger.info(f"[Oracle] Raw Gemini response (first 400 chars): {raw_text[:400]}")

    parsed = _extract_first_json_object(raw_text)
    if "score" not in parsed:
        parsed = _extract_oracle_fields(raw_text)
    if "score" in parsed:
        logger.info(f"[Oracle] Parsed JSON: {parsed}")
        return parsed

    logger.warning(f"Oracle: no valid score JSON found in Gemini response: {raw_text[:200]}")
    return {}


def refresh_oracle_score() -> float:
    """
    Fetch a fresh sentiment score from Gemini API and persist it.
    Returns the float score (0.2 - 1.8), silently falls back to 1.0 on error.
    Guards against concurrent calls — only one HTTP request fires at a time.
    """
    global _cached_score, _cached_regime, _cached_summary, _cached_breadth_state
    global _cached_drivers, _cached_mentioned_symbols, _cached_summary_source
    global _cached_at_ts, _last_error, _refresh_in_progress

    # Deduplicate: if another thread is already refreshing, skip this call
    with _cache_lock:
        if _refresh_in_progress:
            logger.debug("Oracle refresh already in progress — skipping duplicate call.")
            return _cached_score
        _refresh_in_progress = True

    try:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set — Oracle disabled, using neutral score 1.0")
            return 1.0

        result = _normalize_oracle_payload(_call_gemini_api(api_key))
        score = float(result.get("score", 1.0))
        summary = str(result.get("summary", "") or "")
        market_regime = str(result.get("market_regime", "normal") or "normal")
        breadth_state = str(result.get("breadth_state", "mixed") or "mixed")
        drivers = _normalize_driver_list(result.get("drivers"))
        mentioned_symbols = _normalize_symbol_list(result.get("mentioned_symbols"))
        summary_source = str(result.get("summary_source", "fresh_gemini") or "fresh_gemini")

        logger.info(
            "🧭 Oracle Update: score=%.2f regime=%s breadth=%s source=%s | %s",
            score,
            market_regime,
            breadth_state,
            summary_source,
            summary,
        )

        # Persist to database for ML retraining
        try:
            from . import store
            store.record_oracle_score_sync(score=score, summary=summary)
            store.record_macro_consensus_sync(
                risk_multiplier=score,
                market_regime=market_regime,
                macro_sentiment_score=round(score * 50.0, 2),
                summary=summary,
                payload={
                    "score": score,
                    "market_regime": market_regime,
                    "summary": summary,
                    "source": "gemini_oracle",
                    "breadth_state": breadth_state,
                    "drivers": drivers,
                    "mentioned_symbols": mentioned_symbols,
                    "summary_source": summary_source,
                },
            )
            # Log to action feed so user can see oracle refreshes in the UI
            store._record_action_sync(
                action_type="gemini_oracle",
                symbol="",
                side="",
                status="info",
                reason=f"Score: {score:.2f} | Regime: {market_regime} | {summary[:100]}",
                payload={
                    "score": score,
                    "market_regime": market_regime,
                    "summary": summary,
                    "breadth_state": breadth_state,
                    "drivers": drivers,
                    "mentioned_symbols": mentioned_symbols,
                    "summary_source": summary_source,
                },
            )
        except Exception as db_err:
            logger.warning(f"Oracle DB write failed (non-fatal): {db_err}")

        with _cache_lock:
            _cached_score = score
            _cached_regime = market_regime
            _cached_summary = summary
            _cached_breadth_state = breadth_state
            _cached_drivers = drivers
            _cached_mentioned_symbols = mentioned_symbols
            _cached_summary_source = summary_source
            _cached_at_ts = time.time()
            _last_error = ""

        return score


    except Exception as e:
        logger.error(f"Oracle API error (using cached score {_cached_score:.2f}): {e}")
        import traceback
        try:
            with open("debug_oracle_error.txt", "w") as dbg_f:
                dbg_f.write(traceback.format_exc())
        except Exception:
            pass
        with _cache_lock:
            _last_error = str(e)
        return _cached_score
    finally:
        with _cache_lock:
            _refresh_in_progress = False


def get_macro_score() -> float:
    """
    Returns current macro sentiment score (0.2 - 1.8).
    Uses the in-memory cache and only calls Gemini API once per hour.
    Thread-safe. Falls back to 1.0 on first call if no API key configured.
    """
    # On first call after a restart, warm the cache from the last DB row
    if not _restored_from_db:
        _restore_oracle_from_db()

    with _cache_lock:
        age_sec = time.time() - _cached_at_ts
        if age_sec < _CACHE_TTL_SEC:
            return _cached_score  # Cache is fresh

    # Cache expired — refresh asynchronously so the bot loop doesn't block
    # Use a daemon thread to prevent hanging the main trading cycle
    t = threading.Thread(target=refresh_oracle_score, daemon=True)
    t.start()

    # Re-read cached value immediately (won't block even if refresh is slow)
    with _cache_lock:
        return _cached_score


def get_oracle_status() -> dict:
    """Public status endpoint for the UI dashboard."""
    with _cache_lock:
        return {
            "score": _cached_score,
            "market_regime": _cached_regime,
            "summary": _cached_summary,
            "breadth_state": _cached_breadth_state,
            "drivers": list(_cached_drivers),
            "mentioned_symbols": list(_cached_mentioned_symbols),
            "summary_source": _cached_summary_source,
            "cached_at_ts": _cached_at_ts,
            "age_sec": int(time.time() - _cached_at_ts),
            "ttl_sec": _CACHE_TTL_SEC,
            "last_error": _last_error,
            "api_enabled": bool(os.environ.get("GEMINI_API_KEY", "")),
        }


def get_current_oracle_state() -> dict:
    """
    Backward-compatible shim used by bot.py and main.py.
    Returns the same dict structure as the legacy oracle but now powered
    by the Gemini news sentiment score rather than a static default.
    """
    score = get_macro_score()  # Uses cache, never blocks

    # Prefer the Gemini-provided regime (cached from last successful call).
    # Fall back to score-derived regime only if regime is unset.
    with _cache_lock:
        regime = _cached_regime if _cached_regime else None
    if not regime:
        regime = _derive_market_regime(score)

    with _cache_lock:
        summary = _cached_summary
        breadth_state = _cached_breadth_state
        drivers = list(_cached_drivers)
        mentioned_symbols = list(_cached_mentioned_symbols)
        summary_source = _cached_summary_source

    return {
        "risk_multiplier": score,
        "market_regime": regime,
        "rationale_summary": summary or f"Gemini News Oracle score: {score:.2f} ({regime})",
        "oracle_source": "gemini_news",
        "breadth_state": breadth_state,
        "drivers": drivers,
        "mentioned_symbols": mentioned_symbols,
        "summary_source": summary_source,
    }




# ─── Narrative Scanner Cache ────────────────────────────────────────────────
# Updated at most once per 3 hours. Separate from the macro score cache.
_NARRATIVE_TTL_SEC = 21600  # 6 hours — 4 calls/day
_narrative_lock = threading.Lock()
_narrative_leaders: dict = {}      # e.g. {"SOL": {"label": "positive", "reason": "..."}}
_narrative_cached_at: float = 0.0
_narrative_in_progress: bool = False

_NARRATIVE_SYSTEM_PROMPT = """You are a professional crypto market intelligence analyst.
Your job: scan recent news and identify which coins from a given watchlist have
meaningful catalysts in the last 12 hours.

Classify each coin you find as:
  "extreme_positive" — Major binary event: ETF approval, exchange listing, mainnet launch, government adoption
  "positive"         — General bullish news: partnerships, development milestones, influential endorsements
  "extreme_negative" — Major crisis: hack, regulatory ban, rug-pull, exchange insolvency
  "negative"         — General bearish news: project delays, sell pressure, bearish technicals

For coins with NO notable news, omit them entirely.

Respond ONLY with a strict JSON object (no markdown, no explanation):
{"SYMBOL": {"label": "positive", "reason": "one sentence max"}, ...}

If there is truly no notable news for any coin, respond with: {}
"""


def _build_narrative_prompt(symbols: list[str]) -> str:
    tickers = ", ".join(symbols)
    return (
        f"I am running an automated crypto trading bot. My active watchlist is: [{tickers}]. "
        f"Search the web for crypto news from the last 12 hours. "
        f"Which of these coins have notable bullish or bearish catalysts right now? "
        f"Be selective — only flag coins with clearly meaningful events. "
        f"Output strict JSON only (no markdown). Omit coins with no notable news."
    )


def refresh_narrative_leaders(symbols: list[str]) -> dict:
    """
    Call Gemini once to scan news for all active symbols.
    Returns a dict of {symbol: {label, reason}} for coins with catalysts.
    Thread-safe. Silently falls back to empty dict on error.
    """
    global _narrative_leaders, _narrative_cached_at, _narrative_in_progress

    with _narrative_lock:
        if _narrative_in_progress:
            return _narrative_leaders
        _narrative_in_progress = True

    try:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return {}

        from .gemini_client import call_gemini_json
        result = call_gemini_json(
            prompt=_build_narrative_prompt(symbols),
            system=_NARRATIVE_SYSTEM_PROMPT,
            max_tokens=1500,
            temperature=0.2,
            model="gemini-2.5-flash-lite",  # Lite has its own 20 RPD budget — keeps Flash free for oracle
            use_search=True,
        )

        # call_gemini_json may return a nested dict under a wrapper key
        # Try to find the actual coin keyed result
        if isinstance(result, dict):
            # If every key is a coin ticker it's the right shape already
            # Validate: all values must have a "label" key
            narratives = {}
            for k, v in result.items():
                if isinstance(v, dict) and "label" in v:
                    label = str(v.get("label", "")).lower()
                    reason = str(v.get("reason", ""))[:120]
                    narratives[k.upper()] = {"label": label, "reason": reason}
            
            with _narrative_lock:
                _narrative_leaders = narratives
                _narrative_cached_at = time.time()

            # ── Persist narrative to SQLite so dashboard survives restart ──
            try:
                from . import store
                import json as _json
                store._save_signal_state_sync("narrative_leaders", _json.dumps(narratives))
                store._save_signal_state_sync("narrative_cached_at", str(time.time()))
            except Exception as _pe:
                logger.debug(f"Narrative persist failed (non-fatal): {_pe}")

            if narratives:
                logger.info(f"📰 Narrative Update: {list(narratives.keys())}")
            else:
                logger.info("📰 Narrative Update: No notable catalysts found for any coin.")

            # Log to action feed
            try:
                from . import store
                store._record_action_sync(
                    action_type="gemini_narrative",
                    symbol="",
                    side="",
                    status="info",
                    reason=f"Narrative scan: {len(narratives)} coins flagged — {list(narratives.keys())}",
                    payload=narratives,
                )
            except Exception:
                pass

            return narratives

    except Exception as e:
        logger.error(f"Narrative scanner error (non-fatal): {e}")
        with _narrative_lock:
            _narrative_in_progress = False
        return _narrative_leaders
    finally:
        with _narrative_lock:
            _narrative_in_progress = False

    return {}


def get_narrative_state() -> dict:
    """
    Returns the current narrative leaders cache.
    {symbol: {label: str, reason: str}}  — e.g. {"SOL": {"label": "positive", "reason": "..."}}
    Never blocks. Returns stale cache if refresh is in progress.
    """
    with _narrative_lock:
        return dict(_narrative_leaders)


def get_narrative_for_symbol(symbol: str) -> dict | None:
    """
    Returns narrative data for a specific symbol, or None if no catalyst.
    Normalizes symbol format (e.g. 'SOL/USD' → 'SOL').
    """
    # Normalize: strip /USD, /USDT, etc.
    base = symbol.upper().split("/")[0]
    with _narrative_lock:
        return _narrative_leaders.get(base, None)


def get_narrative_status() -> dict:
    """Public status endpoint for the UI dashboard."""
    with _narrative_lock:
        return {
            "leaders": dict(_narrative_leaders),
            "count": len(_narrative_leaders),
            "cached_at_ts": _narrative_cached_at,
            "age_sec": int(time.time() - _narrative_cached_at),
            "ttl_sec": _NARRATIVE_TTL_SEC,
            "in_progress": _narrative_in_progress,
        }


async def oracle_cron_loop() -> None:
    """
    Async background cron scheduled by main.py.
    Refreshes the Gemini oracle score every 30 minutes.
    Generates an hourly bot report every other refresh (~1 hour).
    Runs the 3-hour Narrative Scanner in a separate sub-loop.
    
    Smart boot: checks DB for last oracle call timestamp.
      - If >30 min ago (real restart): refreshes immediately
      - If <30 min ago (dev --reload): waits for remaining interval
    """
    import asyncio
    import time as _time

    # Check DB for last oracle call to decide boot delay
    initial_delay = _CACHE_TTL_SEC  # Default: wait the full 30 min
    try:
        from . import store
        rows = store.get_oracle_history_sync(
            since_ms=int((_time.time() - _CACHE_TTL_SEC) * 1000)
        )
        if rows["count"] > 0:
            logger.info("🧭 Oracle: recent call found in DB — waiting full %ds before next refresh.", _CACHE_TTL_SEC)
        else:
            initial_delay = 10
            logger.info("🧭 Oracle: no recent calls — refreshing in %ds.", initial_delay)
    except Exception:
        logger.info("🧭 Oracle: could not check DB — waiting full %ds.", _CACHE_TTL_SEC)

    await asyncio.sleep(initial_delay)
    
    _narrative_last_run = 0.0  # Track when we last ran the narrative scan

    while True:
        try:
            loop = asyncio.get_event_loop()

            # ── Macro Score (every 30 min) ──────────────────────────────────
            await loop.run_in_executor(None, refresh_oracle_score)

            # ── 6-Hour Report (4 calls/day — stays within 40 RPD budget) ─────
            try:
                from . import store
                now_ms = int(_time.time() * 1000)
                _REPORT_INTERVAL_MS = 2 * 60 * 60 * 1000  # Every 2 hours = 12 calls/day

                # Fetch persistent state
                last_report_ms = store.get_signal_state_sync("last_hourly_report_ms").get("last_hourly_report_ms", 0)
                bot_state = store._get_runtime_state_sync()
                started_at_ms = bot_state.get("started_at")

                # Only execute if the bot has actually started
                if started_at_ms:
                    # Initialize the anchor to bot's start time if never run
                    if last_report_ms == 0:
                        store.save_signal_state_sync("last_hourly_report_ms", started_at_ms)
                        last_report_ms = started_at_ms

                    # If 6+ hours have passed since the last report
                    if (now_ms - last_report_ms) >= _REPORT_INTERVAL_MS:
                        from .report_generator import generate_report
                        await loop.run_in_executor(None, generate_report, "hourly")
                        store.save_signal_state_sync("last_hourly_report_ms", now_ms)
            except Exception as report_err:
                logger.warning(f"Report generation failed (non-fatal): {report_err}")

            # ── Daily Report + Judge (1 call/day) ────────────────────────────
            # Generates a 24-hour summary and runs LLM-as-judge to grade decisions.
            try:
                from . import store
                now_ms = int(_time.time() * 1000)
                _DAILY_INTERVAL_MS = 24 * 60 * 60 * 1000  # Once per day

                last_daily_ms = store.get_signal_state_sync("last_daily_report_ms").get("last_daily_report_ms", 0)
                bot_state = store._get_runtime_state_sync()
                started_at_ms = bot_state.get("started_at")

                if started_at_ms and (now_ms - last_daily_ms) >= _DAILY_INTERVAL_MS:
                    from .report_generator import generate_report
                    await loop.run_in_executor(None, generate_report, "daily")
                    store.save_signal_state_sync("last_daily_report_ms", now_ms)
            except Exception as daily_err:
                logger.warning(f"Daily report/judge failed (non-fatal): {daily_err}")

            # ── Monthly Live Retrain (zero Gemini budget, runs offline) ──────
            # Retrains PPO on real live_experience data: actual oracle scores +
            # session memory context + verified trade outcomes.
            try:
                from . import store
                from .bot import _get_crypto_cfg as _bot_cfg
                now_ms = int(_time.time() * 1000)
                # Configurable via config — defaults to 7 days (weekly)
                _retrain_days = int((_bot_cfg() or {}).get("live_retrain_interval_days", 7))
                _RETRAIN_INTERVAL_MS = _retrain_days * 24 * 60 * 60 * 1000

                last_retrain_ms = store.get_signal_state_sync("last_live_retrain_ms").get("last_live_retrain_ms", 0)
                if (now_ms - last_retrain_ms) >= _RETRAIN_INTERVAL_MS:
                    logger.info(f"[Oracle] Live retrain triggered (interval: {_retrain_days}d).")
                    from .ml.train_from_live_experience import run_live_retrain
                    result = await loop.run_in_executor(None, run_live_retrain)
                    store.save_signal_state_sync("last_live_retrain_ms", now_ms)
                    logger.info(f"[Oracle] Live retrain complete: {result}")
            except Exception as retrain_err:
                logger.warning(f"Monthly live retrain failed (non-fatal): {retrain_err}")

            # Optional: stagger the narrative scanner by 15s so it doesn't fight the
            # macro oracle or report generator for the exact same Gemini API rate limit slot.
            await asyncio.sleep(15)

            # ── Narrative Scanner (every 3 hours) ──────────────────────────
            if (_time.time() - _narrative_last_run) >= _NARRATIVE_TTL_SEC:
                try:
                    from .bot import _get_crypto_cfg
                    from .market_data import get_crypto_positions
                    cfg = _get_crypto_cfg()
                    symbols_raw: list = cfg.get("symbols", [])
                    # Base tickers from config watchlist ("BTC/USD" → "BTC")
                    base_symbols = [s.split("/")[0] for s in symbols_raw if isinstance(s, str)]
                    # Also include any coins currently held (may not be in watchlist)
                    try:
                        held = get_crypto_positions()
                        for p in held:
                            sym = str(p.get("symbol", "")).split("/")[0].split("USD")[0]
                            if sym and sym not in base_symbols:
                                base_symbols.append(sym)
                    except Exception:
                        pass
                    if base_symbols:
                        await loop.run_in_executor(None, refresh_narrative_leaders, base_symbols)
                        _narrative_last_run = _time.time()
                except Exception as narr_err:
                    logger.warning(f"Narrative scan failed (non-fatal): {narr_err}")

        except Exception as e:
            logger.error(f"Oracle cron iteration failed: {e}")
        await asyncio.sleep(_CACHE_TTL_SEC)
