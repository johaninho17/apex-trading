"""
report_generator.py â€” AI Bot Report System
Generates natural-language trading desk briefings using Gemini Flash API.

Report hierarchy:
  - Hourly:  snapshot of current bot state, positions, oracle score, coin prices
  - Daily:   summarizes 24 hourly reports + raw aggregate stats
  - Weekly:  summarizes 7 daily reports + raw aggregate stats
  - Monthly: summarizes 4-5 weekly reports + raw aggregate stats

Each report includes:
  1. Natural language content (for the UI)
  2. Structured metrics JSON (for ML training)
  3. LLM-as-judge grade JSON (approach #3 â€” Gemini evaluates the bot's decisions)
"""

import json
import logging
import os
import ssl
import threading
import time
import traceback
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# â”€â”€â”€ In-memory Cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_cache_lock = threading.Lock()
_cached_reports: Dict[str, Dict[str, Any]] = {}  # type -> {content, ts, metrics, grade}

# â”€â”€ Time windows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_MS_HOUR = 3_600_000
_MS_DAY = 86_400_000
_MS_WEEK = 604_800_000
_MS_MONTH = 2_592_000_000  # 30 days


def _calendar_start_ms(report_type: str) -> int:
    """
    Return the epoch-ms for the CALENDAR start of the current period:
      hourly  â†’ top of the current hour (e.g. 15:00:00.000)
      daily   â†’ midnight today in local time
      weekly  â†’ midnight last Monday in local time
      monthly â†’ midnight the 1st of the current month in local time
    """
    from datetime import datetime, timezone
    now_local = datetime.now()  # local wall-clock time
    if report_type == "hourly":
        start = now_local.replace(minute=0, second=0, microsecond=0)
    elif report_type == "daily":
        start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif report_type == "weekly":
        # weekday(): Monday=0, Sunday=6
        days_since_monday = now_local.weekday()
        start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        start = start - timedelta(days=days_since_monday)
    elif report_type == "monthly":
        start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        # Fallback: last hour
        start = now_local.replace(minute=0, second=0, microsecond=0)
    # Convert to epoch ms (local â†’ UTC assumes mktime)
    import calendar
    return int(calendar.timegm(start.timetuple()) * 1000)

# â”€â”€ Prompt Templates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_HOURLY_SYSTEM = """You are APEX, an AI trading desk analyst for a crypto auto-trading bot.
Write a brief, conversational status report (3-5 sentences) covering:
- Account equity status: how much the account value has changed in the last hour (use the equity delta, NOT unrealized P&L)
- Bot operational status and any alerts
- Current market sentiment from the oracle, including whether the move is broad, majors-led, BTC-led, mixed, or risk-off
- Notable coin movements or recent signals, especially non-BTC leaders or laggards when they materially matter
- If news/provider coverage is limited or unavailable, say that clearly instead of implying neutral market news
IMPORTANT: The equity delta (actual money gained/lost) is MORE IMPORTANT than unrealized P&L. 
Unrealized P&L resets on every trade and is NOT a reliable performance indicator.
Keep it professional, concise, and actionable. Address the user as "you" / "your".
Do NOT use markdown formatting - plain text only."""

_DAILY_SYSTEM = """You are APEX, an AI trading desk analyst summarizing 24 hours of crypto bot activity.
Write a comprehensive daily briefing (6-10 sentences) focused on WHAT THE BOT DID:
- Start with actual trades executed today: how many, which coins, buy vs sell split
- For each coin traded, what was the reason (signal type, oracle score, behavior state)?
- Equity delta: actual dollar gained or lost today (equity history) — this is the #1 metric
- Governor behavior: was activity normal, warm, or throttled? Why?
- News relevance: only discuss news for coins the bot ACTUALLY TRADED today, not watched coins
- Best and worst trade outcomes (P&L per closed trade if available)
- What could improve tomorrow (note any systematic blocks or repeated rejections)
IMPORTANT: Do NOT discuss coins the bot only monitored or watched. Focus on executed actions.
Plain text only, no markdown. Address the user as "you" / "your"."""

_WEEKLY_SYSTEM = """You are APEX, an AI trading desk analyst writing a weekly performance summary.
Write a detailed weekly report (8-12 sentences) with this priority order:
1. What did the bot ACTUALLY DO this week? Total trades, buy/sell counts, coins traded (not watched)
2. Equity trajectory: real account value change week-over-week (primary P&L metric)
3. Which strategies fired most (breakout, DCA, mean_reversion, DRL)? Which were profitable?
4. Closed trade outcomes: win rate, average gain/loss, best and worst trades by P&L
5. Oracle regime trend: did the macro environment help or hinder trading?
6. News impact: only for coins the bot traded — did news events drive good or bad outcomes?
7. Governor behavior: was the bot throttled? Did signal floor block too much?
8. One specific recommendation for next week based on what worked or didn't
IMPORTANT: Do NOT discuss coins the bot was only watching. Focus entirely on executed actions.
Plain text only, no markdown. Address the user as "you" / "your"."""

_MONTHLY_SYSTEM = """You are APEX, an AI trading desk analyst producing a monthly performance review.
Write a comprehensive monthly report (10-15 sentences) covering:
1. Monthly P&L: actual equity change this month, compared to prior weeks
2. Total trades executed — coins traded most, strategies used most
3. Win rate and average trade P&L from closed trades (not unrealized)
4. Which strategies were net profitable vs net losing? What drove the difference?
5. Oracle accuracy: when macro score was high, did trades outperform? When low, did it correctly block?
6. Governor effectiveness: did throttling prevent large drawdowns?
7. News impact for traded coins only — did events cause any notable wins or losses?
8. DRL model contribution: were DRL-weighted trades better or worse than signal-only trades?
9. Top 3 lessons from this month's trading
10. Outlook and priorities for next month
IMPORTANT: Only analyze coins the bot EXECUTED trades on. Watched/monitored coins are irrelevant here.
Plain text only, no markdown. Address the user as "you" / "your"."""

_JUDGE_SYSTEM = """You are a senior quant reviewing an AI trading bot's decisions.
Given the bot's recent actions, equity performance, and market data, provide a brief grade and feedback.
The equity delta (actual account value change) is the most important signal â€” a bot that made many trades
but ended with negative equity should receive a low grade regardless of unrealized P&L on current positions.
Respond ONLY with a JSON object (no markdown):
{
  "grade": "A/B/C/D/F",
  "score": 0-100,
  "strengths": "one sentence on what the bot did well",
  "weaknesses": "one sentence on what could improve",
  "suggestion": "one actionable suggestion"
}"""

_SYSTEM_PROMPTS = {
    "hourly": _HOURLY_SYSTEM,
    "daily": _DAILY_SYSTEM,
    "weekly": _WEEKLY_SYSTEM,
    "monthly": _MONTHLY_SYSTEM,
}


def _call_gemini(prompt: str, system: str, max_tokens: int = 512, json_mode: bool = False) -> str:
    """Delegate to the centralized rate-limited Gemini client."""
    from .gemini_client import call_gemini
    return call_gemini(
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        json_mode=json_mode,
    )



def _gather_snapshot() -> Dict[str, Any]:
    """Gather current bot status, positions, oracle state, and account equity for reports."""
    from . import store
    from .oracle import get_current_oracle_state, get_narrative_status, get_oracle_status

    snapshot: Dict[str, Any] = {}

    # Runtime state
    try:
        runtime = store._get_runtime_state_sync()
        snapshot["bot"] = {
            "running": bool(runtime.get("running")),
            "halted": bool(runtime.get("halted")),
            "halted_reason": runtime.get("halted_reason", ""),
            "iterations": runtime.get("iterations", 0),
            "last_error": runtime.get("last_error", ""),
            "risk_state": str(runtime.get("risk_state", "normal") or "normal"),
            "risk_source": str(runtime.get("risk_source", "") or ""),
            "risk_reason": str(runtime.get("risk_reason", "") or ""),
            "effective_risk_profile": dict(runtime.get("effective_risk_profile") or {}),
            "behavior_state": dict(runtime.get("behavior_state") or {}),
            "component_calibration": dict(runtime.get("component_calibration") or {}),
        }
    except Exception:
        snapshot["bot"] = {"running": False, "error": "could not read runtime state"}

    # Oracle
    try:
        oracle_state = get_current_oracle_state()  # Always returns a valid regime
        oracle_status = get_oracle_status()
        snapshot["oracle"] = {
            "score": oracle_state.get("risk_multiplier", 1.0),
            "market_regime": oracle_state.get("market_regime", "normal"),
            "summary": oracle_state.get("rationale_summary", ""),
            "breadth_state": oracle_state.get("breadth_state", oracle_status.get("breadth_state", "mixed")),
            "drivers": list(oracle_state.get("drivers") or oracle_status.get("drivers") or []),
            "mentioned_symbols": list(oracle_state.get("mentioned_symbols") or oracle_status.get("mentioned_symbols") or []),
            "summary_source": str(oracle_state.get("summary_source", oracle_status.get("summary_source", "")) or ""),
            "age_min": round(oracle_status.get("age_sec", 0) / 60, 1),
            "last_error": oracle_status.get("last_error", ""),
        }
    except Exception:
        snapshot["oracle"] = {"score": 1.0, "market_regime": "normal", "summary": "Oracle unavailable"}

    try:
        narrative = get_narrative_status()
        snapshot["narrative_leaders"] = {
            str(symbol).upper(): dict(value)
            for symbol, value in (narrative.get("leaders") or {}).items()
            if isinstance(value, dict)
        }
        snapshot["narrative_cached_at_ts"] = int(narrative.get("cached_at_ts", 0) or 0)
    except Exception:
        snapshot["narrative_leaders"] = {}
        snapshot["narrative_cached_at_ts"] = 0

    # Equity history performance â€” primary P&L source
    try:
        eq_perf = store.get_equity_performance_sync()
        snapshot["equity_perf"] = eq_perf  # {current, 1h, 24h, 7d} each with {usd, pct}
    except Exception as e:
        logger.debug(f"Could not fetch equity performance for snapshot: {e}")
        snapshot["equity_perf"] = {}

    # Open positions â€” gives Gemini real current exposure data
    try:
        from .market_data import get_crypto_positions
        positions = get_crypto_positions()
        snapshot["open_positions"] = [
            {
                "symbol": p.get("symbol", ""),
                "qty": round(float(p.get("qty", 0)), 6),
                "avg_entry": round(float(p.get("avg_entry_price", 0)), 2),
                "market_value": round(float(p.get("market_value", 0)), 2),
                "unrealized_pl": round(float(p.get("unrealized_pl", 0)), 2),
                "unrealized_plpc": round(float(p.get("unrealized_plpc", 0)) * 100, 2),
            }
            for p in (positions or [])
        ]
        snapshot["total_unrealized_pl"] = round(
            sum(float(p.get("unrealized_pl", 0)) for p in (positions or [])), 2
        )
    except Exception as e:
        logger.debug(f"Could not fetch positions for report snapshot: {e}")
        snapshot["open_positions"] = []
        snapshot["total_unrealized_pl"] = 0.0

    # Recent actions (last 10)
    try:
        actions = store._list_actions_sync(limit=10)
        snapshot["recent_actions"] = [
            {
                "type": a["action_type"],
                "symbol": a["symbol"],
                "side": a["side"],
                "notional": a.get("notional"),
                "reason": a.get("reason", "")[:80],
            }
            for a in actions
        ]
    except Exception:
        snapshot["recent_actions"] = []

    try:
        snapshot["macro_consensus"] = store.get_latest_macro_consensus_sync() or {}
    except Exception:
        snapshot["macro_consensus"] = {}

    try:
        from core.config_manager import get_config

        account_mode = str(get_config().get("stocks", {}).get("crypto", {}).get("account_mode", "paper") or "paper")
    except Exception:
        account_mode = "paper"

    try:
        tracked_rows = store.list_tracked_symbols_sync(account_mode, include_idle=False, limit=80)
        snapshot["tracked_symbol_context"] = [
            {
                "symbol": str(row.get("symbol", "") or "").upper(),
                "active_state": str(row.get("active_state", "idle") or "idle"),
                "active_reason": str(row.get("active_reason", "") or ""),
                "saved_manual": bool(row.get("saved_manual")),
                "monitor_tier": str(row.get("monitor_tier", "eligible") or "eligible"),
            }
            for row in tracked_rows
            if str(row.get("symbol", "") or "").strip()
        ]
    except Exception:
        snapshot["tracked_symbol_context"] = []

    try:
        snapshot["top_symbol_event_states"] = [
            {
                "symbol": row.get("symbol", ""),
                "net_event_score": round(float(row.get("net_event_score", 0.0) or 0.0), 2),
                "event_bias": row.get("event_bias", "neutral"),
                "hard_veto": bool(row.get("hard_veto", 0)),
                "top_event_type": row.get("top_event_type", ""),
                "top_event_score": round(float(row.get("top_event_score", 0.0) or 0.0), 2),
            }
            for row in store.list_symbol_event_states_sync(limit=5)
        ]
    except Exception:
        snapshot["top_symbol_event_states"] = []

    try:
        news_rows = store.get_recent_news_events_sync(limit=40, active_only=False)
        snapshot["recent_news_events"] = [
            {
                "source": row.get("source", "news"),
                "symbol": row.get("symbol", ""),
                "base_asset": row.get("base_asset", ""),
                "event_type": row.get("event_type", "headline"),
                "sentiment": row.get("sentiment", "neutral"),
                "impact_score": round(float(row.get("impact_score", 0.0) or 0.0), 2),
                "headline": row.get("headline", "")[:140],
            }
            for row in news_rows[:8]
        ]
    except Exception:
        news_rows = []
        snapshot["recent_news_events"] = []

    try:
        from .news_pipeline import _news_provider_status

        snapshot["news_provider_health"] = {
            str(name): {
                "status": str((meta or {}).get("status", "idle") or "idle"),
                "info": str((meta or {}).get("info", "") or ""),
            }
            for name, meta in _news_provider_status().items()
        }
    except Exception:
        snapshot["news_provider_health"] = {}

    try:
        state_rows = store.list_symbol_event_states_sync(limit=80)
    except Exception:
        state_rows = []
    source_counts: Dict[str, int] = {}
    for row in news_rows:
        source = str(row.get("source", "news") or "news").lower()
        source_counts[source] = source_counts.get(source, 0) + 1
    context_counts: Dict[str, int] = {}
    for row in state_rows:
        key = str(row.get("news_context_state", "unavailable") or "unavailable")
        context_counts[key] = context_counts.get(key, 0) + 1
    symbol_tagged = sum(
        1
        for row in news_rows
        if str(row.get("symbol", "") or "").strip() or str(row.get("base_asset", "") or "").strip()
    )
    snapshot["news_symbol_coverage"] = {
        "recent_total": len(news_rows),
        "symbol_tagged": symbol_tagged,
        "unattributed": max(0, len(news_rows) - symbol_tagged),
        "source_counts": source_counts,
        "context_counts": context_counts,
    }

    try:
        activity_history = store.get_activity_history_sync(limit=48)
        snapshot["activity_history"] = activity_history
        snapshot["activity_governor"] = activity_history[-1] if activity_history else {}
    except Exception:
        snapshot["activity_history"] = []
        snapshot["activity_governor"] = {}

    try:
        snapshot["recent_decision_outcomes"] = [
            {
                "symbol": row.get("symbol", ""),
                "submitted": bool(row.get("submitted", False)),
                "outcome_label": row.get("outcome_label", ""),
                "block_reason": row.get("block_reason", ""),
                "pnl_pct": round(float(row.get("pnl_pct", 0.0) or 0.0), 2),
            }
            for row in store.list_decision_outcomes_sync(limit=10)
        ]
    except Exception:
        snapshot["recent_decision_outcomes"] = []

    try:
        snapshot["recent_candidate_traces"] = [
            {
                "symbol": row.get("symbol", ""),
                "strategy": row.get("strategy", ""),
                "side": row.get("side", ""),
                "score": round(float(row.get("score", 0.0) or 0.0), 2),
                "candidate_class": row.get("candidate_class", ""),
                "suppressed_reason": row.get("suppressed_reason", ""),
            }
            for row in store.list_candidate_traces_sync(limit=10)
        ]
    except Exception:
        snapshot["recent_candidate_traces"] = []

    try:
        snapshot["recent_risk_transitions"] = [
            {
                "event_type": row.get("event_type", ""),
                "symbol": row.get("symbol", ""),
                "state_value": row.get("state_value", ""),
                "reason": str(row.get("reason", "") or "")[:140],
            }
            for row in store.list_risk_state_history_sync(limit=10)
        ]
    except Exception:
        snapshot["recent_risk_transitions"] = []

    return snapshot


def _dedupe_symbols(values: List[str], limit: int = 8) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for raw in values:
        symbol = str(raw or "").upper().strip()
        if not symbol:
            continue
        if "/" not in symbol:
            symbol = f"{symbol}/USD"
        if symbol in seen:
            continue
        seen.add(symbol)
        ordered.append(symbol)
        if len(ordered) >= limit:
            break
    return ordered


def _prioritized_report_symbols(snapshot: Optional[Dict[str, Any]], limit: int = 8) -> List[str]:
    candidates: List[str] = []
    snapshot = snapshot or {}
    for row in snapshot.get("open_positions") or []:
        candidates.append(str(row.get("symbol", "") or ""))
    for row in snapshot.get("tracked_symbol_context") or []:
        if str(row.get("active_state", "idle") or "idle") in {"active", "recently_active"}:
            candidates.append(str(row.get("symbol", "") or ""))
    for row in snapshot.get("recent_decision_outcomes") or []:
        candidates.append(str(row.get("symbol", "") or ""))
    for row in snapshot.get("top_symbol_event_states") or []:
        candidates.append(str(row.get("symbol", "") or ""))
    for symbol in (snapshot.get("narrative_leaders") or {}).keys():
        candidates.append(str(symbol))
    for row in snapshot.get("recent_actions") or []:
        candidates.append(str(row.get("symbol", "") or ""))
    prioritized = _dedupe_symbols(candidates, limit=limit)
    if prioritized:
        return prioritized
    try:
        from core.config_manager import get_config

        cfg = get_config()
        return _dedupe_symbols(list(cfg.get("stocks", {}).get("crypto", {}).get("symbols", []) or []), limit=limit)
    except Exception:
        return []


def _gather_coin_prices(snapshot: Optional[Dict[str, Any]] = None) -> str:
    """Fetch latest prices for the most relevant market symbols for the report."""
    try:
        from .market_data import get_latest_quote
        symbols = _prioritized_report_symbols(snapshot, limit=8)
        if not symbols:
            return "No symbols configured."
        parts = []
        for sym in symbols:
            try:
                q = get_latest_quote(sym)
                mid = float((q or {}).get("mid_price", 0) or 0)
                if mid > 0:
                    parts.append(f"{sym.replace('/USD', '')}: ${mid:,.2f}")
            except Exception:
                continue
        return ", ".join(parts) if parts else "Price data unavailable (market may be closed)."
    except Exception:
        return "Price data unavailable."


def _fetch_equity_pnl(report_type: str, snapshot: Optional[Dict] = None) -> str:
    """Primary P&L source: read equity delta from equity_history table.
    Falls back to Alpaca portfolio history, then unrealized P&L."""
    # Primary: equity_history deltas (most accurate â€” captures realized + unrealized across all trades)
    equity_perf = (snapshot or {}).get("equity_perf", {})
    period_key = {"hourly": "1h", "daily": "24h", "weekly": "7d", "monthly": "7d"}.get(report_type, "24h")
    period_data = equity_perf.get(period_key)
    current_equity = equity_perf.get("current")

    if period_data and current_equity:
        usd = period_data.get("usd", 0)
        pct = period_data.get("pct", 0)
        sign = "+" if usd >= 0 else ""
        period_label = {"1h": "last hour", "24h": "last 24h", "7d": "last 7d"}.get(period_key, period_key)
        return (
            f"{sign}${usd:,.2f} ({sign}{pct:.2f}%) over {period_label} "
            f"[equity-based: ${current_equity:,.2f} current]"
        )

    # Secondary: Alpaca portfolio history API
    try:
        from integrations.alpaca.runtime_config import get_alpaca_credentials
        api_key, secret_key, is_paper = get_alpaca_credentials()
        if not api_key:
            raise ValueError("No API keys")

        period_map = {"hourly": "1D", "daily": "1D", "weekly": "1W", "monthly": "1M"}
        period = period_map.get(report_type, "1D")
        timeframe = "1H" if report_type == "hourly" else "1D"

        base_url = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
        url = f"{base_url}/v2/account/portfolio/history?period={period}&timeframe={timeframe}"

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        })
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        pl_array = data.get("profit_loss", [])
        pl_valid = [v for v in (pl_array or []) if v is not None]
        if pl_valid:
            pl_dollar = pl_valid[-1] if report_type != "hourly" else (
                pl_valid[-1] - pl_valid[-2] if len(pl_valid) >= 2 else pl_valid[-1]
            )
            sign = "+" if pl_dollar >= 0 else "-"
            return f"{sign}${abs(pl_dollar):,.2f} (Alpaca portfolio history â€” realized+unrealized)"
    except Exception as e:
        logger.debug(f"Portfolio history API failed: {e}")

    # Fallback: unrealized P&L from open positions (least accurate)
    if snapshot and snapshot.get("total_unrealized_pl") is not None:
        upl = float(snapshot["total_unrealized_pl"])
        if upl != 0.0:
            sign = "+" if upl >= 0 else "-"
            return f"{sign}${abs(upl):,.2f} (unrealized only â€” equity history not yet available)"

    return "Unavailable (equity history accumulating â€” bot must run for 5+ min)"


def _build_metrics(report_type: str, snapshot: Optional[Dict] = None) -> Dict[str, Any]:
    """Build structured metrics from SQLite data for ML consumption."""
    from . import store

    now_ms = int(time.time() * 1000)
    since_ms = _calendar_start_ms(report_type)

    actions = store.get_actions_since_sync(since_ms)
    oracle = store.get_oracle_history_sync(since_ms)
    closed_trades = store.get_recent_closed_trades_sync(since_ms)
    news_events = store.get_recent_news_events_sync(limit=200, since_ms=since_ms, active_only=False)
    activity_history = store.get_activity_history_sync(limit=500, since_ms=since_ms)
    decision_outcomes = [row for row in store.list_decision_outcomes_sync(limit=1000) if int(row.get("decision_ts", 0) or 0) >= since_ms]
    candidate_traces = [row for row in store.list_candidate_traces_sync(limit=1000) if int(row.get("ts", 0) or 0) >= since_ms]
    risk_transitions = [row for row in store.list_risk_state_history_sync(limit=500) if int(row.get("ts", 0) or 0) >= since_ms]

    window_hours = max(1, (now_ms - since_ms) // _MS_HOUR)

    # Pull equity metrics from snapshot
    equity_perf = (snapshot or {}).get("equity_perf", {})
    period_key = {"hourly": "1h", "daily": "24h", "weekly": "7d", "monthly": "7d"}.get(report_type, "24h")
    period_eq = equity_perf.get(period_key, {})

    pressure_values = [float(row.get("pressure_score", 0.0) or 0.0) for row in activity_history]
    effectiveness_values = [float(row.get("effectiveness_score", 0.0) or 0.0) for row in activity_history]
    confidence_values = [float(row.get("confidence_score", 0.0) or 0.0) for row in activity_history]
    mode_counts: Dict[str, int] = {}
    verdict_counts: Dict[str, int] = {}
    for row in activity_history:
        mode = str(row.get("mode", "normal") or "normal")
        verdict = str(row.get("verdict", "") or "")
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        if verdict:
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    latest_governor = (snapshot or {}).get("activity_governor") or (activity_history[-1] if activity_history else {})

    outcome_label_counts: Dict[str, int] = {}
    block_reason_counts: Dict[str, int] = {}
    for row in decision_outcomes:
        label = str(row.get("outcome_label", "") or "")
        reason = str(row.get("block_reason", "") or "")
        if label:
            outcome_label_counts[label] = outcome_label_counts.get(label, 0) + 1
        if reason:
            block_reason_counts[reason] = block_reason_counts.get(reason, 0) + 1

    candidate_class_counts: Dict[str, int] = {}
    suppressed_reason_counts: Dict[str, int] = {}
    for row in candidate_traces:
        candidate_class = str(row.get("candidate_class", "") or "")
        suppressed_reason = str(row.get("suppressed_reason", "") or "")
        if candidate_class:
            candidate_class_counts[candidate_class] = candidate_class_counts.get(candidate_class, 0) + 1
        if suppressed_reason:
            suppressed_reason_counts[suppressed_reason] = suppressed_reason_counts.get(suppressed_reason, 0) + 1

    risk_event_counts: Dict[str, int] = {}
    for row in risk_transitions:
        event_type = str(row.get("event_type", "") or "")
        if event_type:
            risk_event_counts[event_type] = risk_event_counts.get(event_type, 0) + 1

    return {
        "report_type": report_type,
        "window_hours": window_hours,
        "period_start_ms": since_ms,
        "total_actions": actions["total_actions"],
        "action_breakdown": actions["breakdown"],
        "oracle_readings": oracle["count"],
        "oracle_avg": oracle["avg"],
        "oracle_min": oracle["min"],
        "oracle_max": oracle["max"],
        "closed_trades": closed_trades,
        "news_event_count": len(news_events),
        "positive_news_events": sum(1 for e in news_events if str(e.get("sentiment", "")).lower() in ("positive", "extreme_positive")),
        "negative_news_events": sum(1 for e in news_events if str(e.get("sentiment", "")).lower() in ("negative", "extreme_negative")),
        "hard_veto_events": sum(1 for e in news_events if str(e.get("trade_bias", "")).lower() == "hard_veto"),
        "narrative_leaders": dict((snapshot or {}).get("narrative_leaders") or {}),
        "news_provider_health": dict((snapshot or {}).get("news_provider_health") or {}),
        "news_symbol_coverage": dict((snapshot or {}).get("news_symbol_coverage") or {}),
        "tracked_symbol_context": list((snapshot or {}).get("tracked_symbol_context") or []),
        "activity_samples": len(activity_history),
        "activity_mode_counts": mode_counts,
        "activity_verdict_counts": verdict_counts,
        "activity_pressure_avg": round(sum(pressure_values) / len(pressure_values), 2) if pressure_values else 0.0,
        "activity_pressure_min": round(min(pressure_values), 2) if pressure_values else 0.0,
        "activity_pressure_max": round(max(pressure_values), 2) if pressure_values else 0.0,
        "activity_effectiveness_avg": round(sum(effectiveness_values) / len(effectiveness_values), 2) if effectiveness_values else 0.0,
        "activity_confidence_avg": round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else 0.0,
        "activity_current_mode": str(latest_governor.get("mode", "normal") or "normal"),
        "activity_current_verdict": str(latest_governor.get("verdict", "") or ""),
        "decision_outcomes_count": len(decision_outcomes),
        "decision_outcome_labels": outcome_label_counts,
        "block_reason_counts": block_reason_counts,
        "candidate_trace_count": len(candidate_traces),
        "candidate_class_counts": candidate_class_counts,
        "candidate_suppressed_reason_counts": suppressed_reason_counts,
        "risk_transition_count": len(risk_transitions),
        "risk_transition_counts": risk_event_counts,
        "pnl_dollar": _fetch_equity_pnl(report_type, snapshot=snapshot),
        # Equity-based metrics for ML training
        "equity_current": equity_perf.get("current"),
        "equity_delta_usd": period_eq.get("usd"),
        "equity_delta_pct": period_eq.get("pct"),
        "generated_at_ms": now_ms,
    }


def _build_prompt(report_type: str, snapshot: Dict, metrics: Dict, coin_prices: str) -> str:
    """Build the user prompt with all gathered data."""
    from . import store

    lines = [f"=== {report_type.upper()} BOT REPORT DATA ==="]
    lines.append(f"\nBot Status: {'RUNNING' if snapshot.get('bot', {}).get('running') else 'STOPPED'}")
    if snapshot.get("bot", {}).get("halted"):
        lines.append(f"âš ï¸ HALTED: {snapshot['bot'].get('halted_reason', 'unknown')}")
    lines.append(f"Iterations: {snapshot.get('bot', {}).get('iterations', 0)}")

    oracle = snapshot.get("oracle", {})
    oracle_score = oracle.get('score', 1.0)
    oracle_regime = oracle.get('market_regime', 'normal')
    oracle_summary = oracle.get('summary', '')
    oracle_age = oracle.get('age_min', 0)
    oracle_stale = oracle_age > 120  # flag if >2hrs old
    lines.append(f"\nOracle Score: {oracle_score:.2f} | Regime: {oracle_regime}" +
                 (f" [STALE: {oracle_age:.0f}min ago]" if oracle_stale else f" [{oracle_age:.0f}min ago]"))
    if oracle_summary:
        lines.append(f"Oracle Insight: {oracle_summary}")
    if oracle.get("breadth_state"):
        lines.append(f"Oracle Breadth: {oracle.get('breadth_state')}")
    if oracle.get("drivers"):
        lines.append(f"Oracle Drivers: {', '.join(list(oracle.get('drivers') or [])[:3])}")
    if oracle.get("mentioned_symbols"):
        lines.append(f"Oracle Mentioned Symbols: {', '.join(list(oracle.get('mentioned_symbols') or [])[:6])}")
    if oracle.get("summary_source"):
        lines.append(f"Oracle Summary Source: {oracle.get('summary_source')}")

    macro_consensus = snapshot.get("macro_consensus", {})
    if macro_consensus:
        lines.append(
            f"Macro Consensus: {float(macro_consensus.get('risk_multiplier', 1.0) or 1.0):.2f} | "
            f"Regime: {macro_consensus.get('market_regime', 'normal')} | "
            f"Sentiment: {float(macro_consensus.get('macro_sentiment_score', 0.0) or 0.0):.0f}"
        )
        if macro_consensus.get("summary"):
            lines.append(f"Macro Summary: {macro_consensus.get('summary')}")

    bot_runtime = snapshot.get("bot", {})
    if bot_runtime:
        lines.append(
            f"Risk State: {bot_runtime.get('risk_state', 'normal')}" +
            (f" via {bot_runtime.get('risk_source', '')}" if bot_runtime.get('risk_source') else "")
        )
        if bot_runtime.get("risk_reason"):
            lines.append(f"Risk Reason: {str(bot_runtime.get('risk_reason') or '')[:180]}")
        effective_risk_profile = dict(bot_runtime.get("effective_risk_profile", {}) or {})
        if effective_risk_profile:
            lines.append(
                "Effective Risk Profile: "
                f"hard_cap=${float(effective_risk_profile.get('effective_hard_cap_usd', 0.0) or 0.0):,.2f} | "
                f"ai_ceiling=${float(effective_risk_profile.get('effective_ai_ceiling_usd', 0.0) or 0.0):,.2f} | "
                f"max_exposure=${float(effective_risk_profile.get('max_exposure_usd', 0.0) or 0.0):,.2f} | "
                f"capacity={int(effective_risk_profile.get('capacity_remaining', 0) or 0)}"
            )

    activity_governor = snapshot.get("activity_governor", {})
    if activity_governor:
        lines.append(
            f"Activity Governor: mode={activity_governor.get('mode', 'normal')} | "
            f"candidate={activity_governor.get('candidate_mode', 'normal')} | "
            f"pressure={float(activity_governor.get('pressure_score', 0.0) or 0.0):+.1f} | "
            f"effectiveness={float(activity_governor.get('effectiveness_score', 0.0) or 0.0):+.1f} | "
            f"confidence={float(activity_governor.get('confidence_score', 0.0) or 0.0):.0f} | "
            f"verdict={activity_governor.get('verdict', 'insufficient')}"
        )
        reason_codes = list(activity_governor.get("reason_codes") or [])
        if reason_codes:
            lines.append(f"Activity Flags: {', '.join(reason_codes[:6])}")
        effective = dict(activity_governor.get("effective") or {})
        if effective:
            lines.append(
                "Activity Thresholds: "
                f"min_signal_score={effective.get('min_signal_score', 'n/a')} | "
                f"trade_spacing_min={effective.get('trade_spacing_min', 'n/a')} | "
                f"rsi_oversold={effective.get('rsi_oversold', 'n/a')} | "
                f"breakout_volume_mult={effective.get('breakout_volume_mult', 'n/a')}"
            )

    component_calibration = dict(snapshot.get("bot", {}).get("component_calibration", {}) or {})
    if component_calibration:
        lines.append(f"Calibration Weights: {json.dumps(component_calibration.get('weights', {}), ensure_ascii=True)}")
    behavior_state = dict(snapshot.get("bot", {}).get("behavior_state", {}) or {})
    if behavior_state:
        aggregate = dict(behavior_state.get("aggregate") or {})
        lines.append(
            f"Behavior State: score={float(aggregate.get('aggregate_score', 0.0) or 0.0):+.1f} | confidence={float(aggregate.get('aggregate_confidence', 0.0) or 0.0):.0f}"
        )
        reasons = list(aggregate.get("reason_codes") or [])
        if reasons:
            lines.append(f"Behavior Flags: {', '.join(reasons[:6])}")

    activity_mode_counts = metrics.get("activity_mode_counts", {}) or {}
    if activity_mode_counts:
        lines.append(
            f"Activity Window: samples={metrics.get('activity_samples', 0)} | "
            f"avg_pressure={float(metrics.get('activity_pressure_avg', 0.0) or 0.0):+.1f} | "
            f"avg_effectiveness={float(metrics.get('activity_effectiveness_avg', 0.0) or 0.0):+.1f} | "
            f"avg_confidence={float(metrics.get('activity_confidence_avg', 0.0) or 0.0):.0f}"
        )
        mode_mix = ", ".join(f"{mode}={count}" for mode, count in activity_mode_counts.items())
        lines.append(f"Activity Mode Mix: {mode_mix}")

    outcome_labels = metrics.get("decision_outcome_labels", {}) or {}
    if outcome_labels:
        outcome_mix = ", ".join(f"{label}={count}" for label, count in sorted(outcome_labels.items()))
        lines.append(f"Decision Feedback: total={metrics.get('decision_outcomes_count', 0)} | {outcome_mix}")
    block_reason_counts = metrics.get("block_reason_counts", {}) or {}
    if block_reason_counts:
        top_block_reasons = ", ".join(
            f"{reason}={count}" for reason, count in sorted(block_reason_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        )
        lines.append(f"Block Reason Mix: {top_block_reasons}")
    candidate_class_counts = metrics.get("candidate_class_counts", {}) or {}
    if candidate_class_counts:
        candidate_mix = ", ".join(f"{label}={count}" for label, count in sorted(candidate_class_counts.items()))
        lines.append(f"Candidate Activity: total={metrics.get('candidate_trace_count', 0)} | {candidate_mix}")
    suppressed_reason_counts = metrics.get("candidate_suppressed_reason_counts", {}) or {}
    if suppressed_reason_counts:
        top_candidate_reasons = ", ".join(
            f"{reason}={count}" for reason, count in sorted(suppressed_reason_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        )
        lines.append(f"Candidate Suppression Mix: {top_candidate_reasons}")
    risk_transition_counts = metrics.get("risk_transition_counts", {}) or {}
    if risk_transition_counts:
        risk_mix = ", ".join(
            f"{label}={count}" for label, count in sorted(risk_transition_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        )
        lines.append(f"Risk State Transitions: total={metrics.get('risk_transition_count', 0)} | {risk_mix}")

    lines.append(f"\nCoin Prices: {coin_prices}")

    tracked_rows = snapshot.get("tracked_symbol_context", []) or []
    if tracked_rows:
        tracked_preview = ", ".join(
            f"{row.get('symbol')} [{row.get('active_state')}{'/' + str(row.get('active_reason')) if row.get('active_reason') else ''}]"
            for row in tracked_rows[:8]
        )
        lines.append(f"Tracked Symbol Context: {tracked_preview}")

    narrative_leaders = snapshot.get("narrative_leaders", {}) or {}
    if narrative_leaders:
        lines.append("Narrative Leaders:")
        for symbol, payload in list(narrative_leaders.items())[:6]:
            lines.append(
                f"  - {symbol}: {str(payload.get('label', 'neutral') or 'neutral')} | {str(payload.get('reason', '') or '')[:140]}"
            )

    provider_health = snapshot.get("news_provider_health", {}) or {}
    if provider_health:
        lines.append(
            "News Provider Health: "
            + ", ".join(
                f"{name}={str((meta or {}).get('status', 'idle') or 'idle')}"
                for name, meta in provider_health.items()
            )
        )

    coverage = snapshot.get("news_symbol_coverage", {}) or {}
    if coverage:
        coverage_line = (
            f"News Symbol Coverage: total={int(coverage.get('recent_total', 0) or 0)} | "
            f"symbol_tagged={int(coverage.get('symbol_tagged', 0) or 0)} | "
            f"unattributed={int(coverage.get('unattributed', 0) or 0)}"
        )
        source_counts = coverage.get("source_counts", {}) or {}
        if source_counts:
            coverage_line += " | sources=" + ", ".join(
                f"{name}={count}" for name, count in sorted(source_counts.items(), key=lambda item: item[0])
            )
        lines.append(coverage_line)
        context_counts = coverage.get("context_counts", {}) or {}
        if context_counts:
            lines.append(
                "News Context States: "
                + ", ".join(
                    f"{name}={count}" for name, count in sorted(context_counts.items(), key=lambda item: item[0])
                )
            )
        if int(coverage.get("recent_total", 0) or 0) == 0:
            lines.append("News Coverage Warning: No recent stored news is available right now. Treat missing news as unavailable, not neutral.")
        elif int(coverage.get("symbol_tagged", 0) or 0) == 0:
            lines.append("News Coverage Warning: Recent news exists but symbol attribution is limited. Do not narrate the market as broadly neutral from this gap.")

    # Equity Performance Block (PRIMARY P&L)
    eq_perf = snapshot.get("equity_perf", {})
    current_eq = eq_perf.get("current")
    period_key = {"hourly": "1h", "daily": "24h", "weekly": "7d", "monthly": "7d"}.get(report_type, "24h")
    period_eq = eq_perf.get(period_key, {})

    lines.append("\n--- ACCOUNT EQUITY (Primary Performance Metric) ---")
    if current_eq is not None:
        lines.append(f"  Current Equity:     ${current_eq:,.2f}")
    else:
        lines.append("  Current Equity:     Accumulating (bot must run 5+ min)")

    if period_eq:
        eq_usd = period_eq.get("usd", 0)
        eq_pct = period_eq.get("pct", 0)
        eq_sign = "+" if eq_usd >= 0 else ""
        period_label = {"1h": "1h", "24h": "24h", "7d": "7d"}.get(period_key, period_key)
        lines.append(f"  Change ({period_label}):        {eq_sign}${eq_usd:,.2f} ({eq_sign}{eq_pct:.2f}%)")
        # Risk flag
        if eq_pct <= -2.0:
            severity = "âš ï¸ ELEVATED" if eq_pct > -4.0 else "ðŸš¨ CRITICAL"
            lines.append(f"  Risk Flag: {severity} â€” equity drawdown {eq_pct:.2f}% in {period_label}")
    else:
        lines.append(f"  Change ({period_key}):        No equity history yet")

    # Show all timeframes for context
    for tf_key, tf_label in [("1h", "1h"), ("24h", "24h"), ("7d", "7d")]:
        tf_data = eq_perf.get(tf_key)
        if tf_data and tf_key != period_key:
            s = "+" if tf_data["usd"] >= 0 else ""
            lines.append(f"  Equity {tf_label}:          {s}${tf_data['usd']:,.2f} ({s}{tf_data['pct']:.2f}%)")

    lines.append(f"\n  [Note: Equity delta = real money won/lost. Unrealized P&L below is a snapshot only.]")

    # Open positions snapshot (secondary â€” unrealized P&L)
    open_positions = snapshot.get("open_positions", [])
    if open_positions:
        lines.append(f"\nOpen Positions ({len(open_positions)}) â€” Unrealized P&L (snapshot):")
        for pos in open_positions:
            pl_sign = "+" if pos['unrealized_pl'] >= 0 else ""
            lines.append(
                f"  â€¢ {pos['symbol']}: {pos['qty']} @ ${pos['avg_entry']:,.2f} "
                f"| MV: ${pos['market_value']:,.2f} "
                f"| uPnL: {pl_sign}${pos['unrealized_pl']:,.2f} ({pl_sign}{pos['unrealized_plpc']:.2f}%)"
            )
        upl = snapshot.get('total_unrealized_pl', 0)
        lines.append(f"  Total Unrealized: {'+' if upl >= 0 else ''}${upl:,.2f}")
    else:
        lines.append("\nNo open positions.")

    # Action metrics
    lines.append(f"\nActions in window: {metrics.get('total_actions', 0)}")
    breakdown = metrics.get("action_breakdown", {})
    if breakdown:
        for k, v in breakdown.items():
            lines.append(f"  {k}: {v['count']}x (${v['total_notional']:,.0f} total)")

    # Closed trades
    closed_trades = metrics.get("closed_trades", [])
    if closed_trades:
        lines.append(f"\nRecent Closed Trades (FIFO P&L):")
        for ct in closed_trades:
            sign = "+" if ct['pnl_dollar'] >= 0 else ""
            lines.append(f"  â€¢ {ct['symbol']}: {sign}${ct['pnl_dollar']} ({sign}{ct['pnl_pct']}%)")

    # Oracle trend
    lines.append(f"\nOracle readings: {metrics.get('oracle_readings', 0)} | Avg: {metrics.get('oracle_avg', 1.0)} | Range: [{metrics.get('oracle_min', 1.0)}, {metrics.get('oracle_max', 1.0)}]")

    lines.append(
        f"News events in window: {metrics.get('news_event_count', 0)} | "
        f"Positive: {metrics.get('positive_news_events', 0)} | "
        f"Negative: {metrics.get('negative_news_events', 0)} | "
        f"Hard veto: {metrics.get('hard_veto_events', 0)}"
    )

    top_event_states = snapshot.get("top_symbol_event_states", [])
    if top_event_states:
        lines.append("\nTop cached event states:")
        for state in top_event_states[:5]:
            lines.append(
                f"  - {state['symbol']}: score {state['net_event_score']:+.2f} | "
                f"bias {state['event_bias']} | veto={state['hard_veto']} | top={state['top_event_type']}"
            )

    recent_news = snapshot.get("recent_news_events", [])
    if recent_news:
        lines.append("\nRecent stored news events:")
        for event in recent_news[:6]:
            lines.append(
                f"  - [{event['source']}] {event['symbol'] or 'market'} {event['event_type']} "
                f"{event['sentiment']} ({event['impact_score']:.0f}): {event['headline']}"
            )

    recent_outcomes = snapshot.get("recent_decision_outcomes", [])
    if recent_outcomes:
        lines.append("\nRecent decision feedback:")
        for item in recent_outcomes[:6]:
            symbol = item.get("symbol") or "market"
            outcome = item.get("outcome_label") or "unknown"
            pnl_pct = float(item.get("pnl_pct", 0.0) or 0.0)
            lines.append(
                f"  - {symbol}: {outcome} | submitted={item.get('submitted')} | "
                f"block_reason={item.get('block_reason') or 'n/a'} | pnl_pct={pnl_pct:+.2f}"
            )

    recent_candidates = snapshot.get("recent_candidate_traces", [])
    if recent_candidates:
        lines.append("\nRecent suppressed candidates:")
        for item in recent_candidates[:6]:
            lines.append(
                f"  - {item.get('symbol')}: {item.get('strategy')} {item.get('side')} "
                f"score={float(item.get('score', 0.0) or 0.0):.1f} | "
                f"class={item.get('candidate_class') or 'n/a'} | reason={item.get('suppressed_reason') or 'n/a'}"
            )

    recent_risk = snapshot.get("recent_risk_transitions", [])
    if recent_risk:
        lines.append("\nRecent risk transitions:")
        for item in recent_risk[:6]:
            lines.append(
                f"  - {item.get('event_type')} {item.get('symbol') or 'global'} -> {item.get('state_value') or 'n/a'} | "
                f"{item.get('reason') or ''}"
            )

    # Recent actions for hourly
    if report_type == "hourly":
        actions = snapshot.get("recent_actions", [])
        if actions:
            lines.append("\nRecent signals:")
            for a in actions[:5]:
                lines.append(f"  â€¢ {a['side'].upper()} {a['symbol']} ({a['type']}) â€” {a['reason']}")
        else:
            lines.append("\nNo recent signals fired.")

    # Hierarchical: inject lower-level reports for daily/weekly/monthly
    if report_type in ("daily", "weekly", "monthly"):
        child_type = {"daily": "hourly", "weekly": "daily", "monthly": "weekly"}[report_type]
        since_ms = _calendar_start_ms(report_type)
        child_reports = store.get_reports_in_window_sync(child_type, since_ms)
        if child_reports:
            lines.append(f"\n--- {child_type.capitalize()} reports from this {report_type} period ({len(child_reports)} total) ---")
            for cr in child_reports[-12:]:  # Last 12 to keep prompt manageable
                lines.append(f"[{time.strftime('%m/%d %H:%M', time.localtime(cr['ts'] / 1000))}] {cr['content'][:300]}")

    return "\n".join(lines)


def _judge_decisions(snapshot: Dict, metrics: Dict) -> Dict[str, Any]:
    """LLM-as-judge: Gemini grades the bot's recent trading decisions."""
    try:
        actions = snapshot.get("recent_actions", [])
        if not actions:
            return {"grade": "N/A", "score": 0, "strengths": "No actions to evaluate", "weaknesses": "", "suggestion": "Wait for signals"}

        prompt_parts = ["Evaluate these recent bot trading decisions:"]
        for a in actions[:8]:
            prompt_parts.append(f"  â€¢ {a['side'].upper()} {a['symbol']} via {a['type']} â€” {a['reason']}")
        prompt_parts.append(f"\nOracle score: {snapshot.get('oracle', {}).get('score', 1.0):.2f} ({snapshot.get('oracle', {}).get('market_regime', 'normal')})")
        prompt_parts.append(f"Total actions in period: {metrics.get('total_actions', 0)}")

        # Include equity context in judge evaluation
        eq_perf = snapshot.get("equity_perf", {})
        if eq_perf.get("current"):
            prompt_parts.append(f"Current equity: ${eq_perf['current']:,.2f}")
        for tf in ["1h", "24h", "7d"]:
            tf_data = eq_perf.get(tf)
            if tf_data:
                s = "+" if tf_data["usd"] >= 0 else ""
                prompt_parts.append(f"Equity {tf}: {s}${tf_data['usd']:,.2f} ({s}{tf_data['pct']:.2f}%)")

        response = _call_gemini("\n".join(prompt_parts), _JUDGE_SYSTEM, max_tokens=512, json_mode=True)
        
        # Robustly extract JSON object ignoring markdown formatting
        text = response.strip()
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            text = text[start_idx:end_idx+1]
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
                
        logger.warning(f"Judge returned invalid JSON: {text[:100]}")
        return {"grade": "N/A", "score": 0, "error": "Invalid format"}
    except Exception as e:
        logger.warning(f"Judge grading failed: {e}")
        return {"grade": "N/A", "score": 0, "error": str(e)}


def generate_report(report_type: str) -> Optional[Dict[str, Any]]:
    """
    Generate a bot report of the specified type.
    Gathers data, calls Gemini, persists to SQLite, caches in memory.
    Returns the report dict or None on failure.
    """
    from . import store

    try:
        logger.info(f"ðŸ“ Generating {report_type} bot report...")

        # 1. Gather data
        snapshot = _gather_snapshot()
        metrics = _build_metrics(report_type, snapshot=snapshot)
        coin_prices = _gather_coin_prices(snapshot=snapshot)

        # 2. Build prompt and generate report
        system = _SYSTEM_PROMPTS.get(report_type, _HOURLY_SYSTEM)
        prompt = _build_prompt(report_type, snapshot, metrics, coin_prices)
        content = _call_gemini(prompt, system, max_tokens=1024 if report_type == "hourly" else 2048)

        # 3. LLM-as-judge grading (for daily and weekly reports)
        grade = {}
        if report_type in ("daily", "weekly"):
            grade = _judge_decisions(snapshot, metrics)

        # 4. Persist to SQLite
        metrics_str = json.dumps(metrics)
        grade_str = json.dumps(grade)
        store.record_bot_report_sync(report_type, content, metrics_str, grade_str)

        # 4b. Log to action feed so user sees report generation events
        grade_label = f" | Grade: {grade.get('grade', '')}" if grade.get("grade") else ""
        store._record_action_sync(
            action_type="gemini_report",
            symbol="",
            side="",
            status="info",
            reason=f"{report_type.capitalize()} report generated ({len(content)} chars){grade_label}",
            payload={"report_type": report_type, "content_preview": content[:200]},
        )

        # 5. Cache in memory
        result = {
            "content": content,
            "ts": int(time.time() * 1000),
            "metrics": metrics,
            "grade": grade,
            "report_type": report_type,
        }

        # 5b. Push the full saved report to Telegram.
        try:
            from integrations.telegram.telegram_client import send_report
            send_report(report_type, content, grade=grade)
        except Exception as send_exc:
            logger.debug(f"Could not send full {report_type} report to Telegram: {send_exc}")
        with _cache_lock:
            _cached_reports[report_type] = result

        logger.info(f"âœ… {report_type.capitalize()} report generated ({len(content)} chars)")
        return result

    except Exception as e:
        logger.error(f"Failed to generate {report_type} report: {e}\n{traceback.format_exc()}")
        raise  # Let callers handle â€” cron loops have their own try/except


def get_cached_report(report_type: str) -> Optional[Dict[str, Any]]:
    """Return the latest cached report for a given type."""
    with _cache_lock:
        return _cached_reports.get(report_type)


def get_all_latest_reports() -> Dict[str, Any]:
    """Return the latest report of each type (cached or from DB)."""
    from . import store

    result = {}
    for rtype in ("hourly", "daily", "weekly", "monthly"):
        # Try memory cache first
        with _cache_lock:
            cached = _cached_reports.get(rtype)
        if cached:
            result[rtype] = cached
            continue
        # Fall back to DB
        db_reports = store.get_latest_reports_sync(report_type=rtype, limit=1)
        if db_reports:
            r = db_reports[0]
            result[rtype] = {
                "content": r["content"],
                "ts": r["ts"],
                "metrics": r.get("metrics", {}),
                "grade": r.get("grade", {}),
                "report_type": rtype,
            }
        else:
            result[rtype] = None
    return result

def get_historical_reports(limit_per_type: int = 50) -> Dict[str, List[Dict[str, Any]]]:
    """Return historical reports of each type from DB."""
    from . import store

    result = {"hourly": [], "daily": [], "weekly": [], "monthly": []}
    for rtype in result.keys():
        db_reports = store.get_latest_reports_sync(report_type=rtype, limit=limit_per_type)
        if db_reports:
            for r in db_reports:
                result[rtype].append({
                    "content": r["content"],
                    "ts": r["ts"],
                    "metrics": r.get("metrics", {}),
                    "grade": r.get("grade", {}),
                    "report_type": rtype,
                })
    return result

