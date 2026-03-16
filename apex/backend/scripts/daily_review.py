#!/usr/bin/env python3
"""
daily_review.py — Phase 4: Daily Config Self-Review (Meta-Learning)

At the end of each trading day, this script:
  1. Pulls the day's performance stats from SQLite
  2. Reads the current crypto_config.json
  3. Asks Gemini: "You are the manager. The bot performed like this today.
     Propose up to 3 parameter changes to improve tomorrow."
  4. Writes Gemini's suggestions to data/pending_config.json for your review
  5. NOTHING is auto-applied — you must manually approve and swap the file

Usage:
  python3 scripts/daily_review.py

Recommended: Run via cron at 11:55 PM every night:
  55 23 * * * cd /Users/johan/Projects/trading/apex/backend && source venv_wsl/bin/activate && python3 scripts/daily_review.py >> logs/daily_review.log 2>&1
"""

import os
import sys
import json
import time
import logging
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

_ENV_FILE = os.path.join(_BACKEND_DIR, ".env")
if os.path.exists(_ENV_FILE):
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=True)
    except ImportError:
        pass
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip(); v = v.strip().strip('"').strip("'")
                if "\n" not in v and k:
                    os.environ.setdefault(k, v)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DAILY_REVIEW] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_REVIEW_SYSTEM_PROMPT = """You are the performance manager for an automated crypto trading bot.
Your job: given a daily performance report and the current bot config, propose up to 3 specific,
conservative parameter tweaks that would improve tomorrow's results.

Rules:
- Only suggest parameters that already exist in the config file (do not invent new keys).
- Be conservative — small, measurable changes only (e.g. rsi_oversold: 30 → 28).
- If performance was good today (positive PnL, win rate > 50%), suggest minimal changes.
- If performance was poor (negative PnL, lots of stop-loss hits), suggest tighter risk controls.
- Never suggest changes that would disable safety features (stop-loss, oversell guard).

Respond ONLY with a strict JSON object describing the suggested parameter path and new value.
Example format:
{
  "changes": [
    {"path": "short_term.rsi_oversold", "old": 30, "new": 28, "reason": "RSI was too high, missing deep dips"},
    {"path": "synthetic_exits.stop_loss_pct", "old": 1.8, "new": 2.2, "reason": "Too many premature stop-outs"}
  ],
  "summary": "One sentence summary of the bot's main weakness today."
}
If no changes are needed, respond with: {"changes": [], "summary": "Performance was acceptable. No changes needed."}
"""


def _build_review_prompt(stats: dict, cfg: dict) -> str:
    return f"""Here is today's trading performance report:

Date: {datetime.now().strftime("%Y-%m-%d")}
Total Trades (Buys): {stats.get("total_buys", 0)}
Total Exits: {stats.get("total_exits", 0)}
Win Rate: {stats.get("win_rate_pct", 0):.1f}%
Realized PnL: {stats.get("total_pnl_pct", 0):+.2f}%
Stop-Losses Hit: {stats.get("stop_losses", 0)}
Trailing Stop Exits: {stats.get("trailing_exits", 0)}
RSI Exits: {stats.get("rsi_exits", 0)}
TP1 Exits: {stats.get("tp1_exits", 0)}
Most Traded Symbol: {stats.get("top_symbol", "N/A")}

Current Config (relevant excerpts):
{json.dumps(cfg.get("short_term", {}), indent=2)}
{json.dumps(cfg.get("synthetic_exits", {}), indent=2)}

Based on this performance, propose up to 3 conservative parameter changes that would improve tomorrow's results.
Output strict JSON only."""


def _pull_daily_stats() -> dict:
    from services.crypto.store import _connect, _init_db_sync, _LOCK
    _init_db_sync()

    since_ms = int((time.time() - 86400) * 1000)  # Last 24 hours
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT action_type, symbol, payload_json
                FROM actions
                WHERE ts >= ? AND status = 'success'
                """,
                (since_ms,),
            ).fetchall()
        finally:
            con.close()

    total_buys = sum(1 for r in rows if r["action_type"] == "crypto_order" and "buy" in str(r.get("action_type", "")))
    total_exits = sum(1 for r in rows if "synthetic_exit" in str(r.get("action_type", "")))
    stop_losses  = sum(1 for r in rows if r["action_type"] == "synthetic_exit")
    trailing     = sum(1 for r in rows if r["action_type"] == "synthetic_exit_trailing")
    rsi_exits    = sum(1 for r in rows if r["action_type"] == "synthetic_exit_rsi")
    tp1_exits    = sum(1 for r in rows if r["action_type"] in ("synthetic_exit_tp1", "synthetic_exit_tp1_full"))

    wins = 0
    total_pnl = 0.0
    symbol_counts: dict = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            pnl = float(payload.get("pnl_pct", 0.0))
            total_pnl += pnl
            if pnl > 0:
                wins += 1
        except Exception:
            pass
        sym = str(row.get("symbol", "") or "")
        if sym:
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1

    top_symbol = max(symbol_counts, key=symbol_counts.get) if symbol_counts else "N/A"
    win_rate = (wins / total_exits * 100.0) if total_exits > 0 else 0.0

    return {
        "total_buys": total_buys,
        "total_exits": total_exits,
        "win_rate_pct": round(win_rate, 1),
        "total_pnl_pct": round(total_pnl, 2),
        "stop_losses": stop_losses,
        "trailing_exits": trailing,
        "rsi_exits": rsi_exits,
        "tp1_exits": tp1_exits,
        "top_symbol": top_symbol,
    }


def run():
    logger.info("=" * 60)
    logger.info("APEX DAILY SELF-REVIEW — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.error("GEMINI_API_KEY not set. Cannot run self-review.")
        return

    # ── 1. Pull performance stats ─────────────────────────────────────────────
    logger.info("[1/3] Pulling daily performance stats...")
    stats = _pull_daily_stats()
    logger.info("  Trades: %d exits | Win Rate: %.1f%% | PnL: %+.2f%%",
                stats["total_exits"], stats["win_rate_pct"], stats["total_pnl_pct"])

    # ── 2. Load current config from master config.json ───────────────────────
    logger.info("[2/3] Loading current config and calling Gemini for review...")
    from core.config_manager import get_config
    full_cfg = get_config()
    
    # Navigate to the active exchange profile
    crypto_cfg = full_cfg.get("stocks", {}).get("crypto", {})
    active_exchange = str(crypto_cfg.get("active_exchange", "alpaca")).lower()
    active_mode = str(crypto_cfg.get("account_mode", "paper")).lower()
    profile_key = f"{active_exchange}-{active_mode}"
    cfg = crypto_cfg.get(profile_key, crypto_cfg)

    # ── 3. Ask Gemini for config suggestions ──────────────────────────────────
    try:
        from services.crypto.gemini_client import call_gemini_json
        result = call_gemini_json(
            prompt=_build_review_prompt(stats, cfg),
            system=_REVIEW_SYSTEM_PROMPT,
            max_tokens=800,
            temperature=0.2,
            model="gemini-2.5-flash",
            use_search=False,  # No web search needed — pure reasoning
        )

        # Save to pending_config.json for user review
        pending = {
            "generated_at": datetime.now().isoformat(),
            "performance_stats": stats,
            "active_config_excerpt": {
                "short_term": cfg.get("short_term", {}),
                "synthetic_exits": cfg.get("synthetic_exits", {}),
            },
            "gemini_suggestions": result,
        }

        pending_path = os.path.join(_BACKEND_DIR, "data", "pending_config.json")
        with open(pending_path, "w") as f:
            json.dump(pending, f, indent=2)

        logger.info("[3/3] ✅ Gemini review written to data/pending_config.json")
        logger.info("  Summary: %s", result.get("summary", "No summary."))
        changes = result.get("changes", [])
        if changes:
            logger.info("  Proposed %d change(s):", len(changes))
            for c in changes:
                logger.info("    · %s: %s → %s  (%s)", c.get("path"), c.get("old"), c.get("new"), c.get("reason", ""))
        else:
            logger.info("  No parameter changes suggested.")

    except Exception as e:
        logger.error("Gemini self-review failed: %s", e)

    logger.info("=" * 60)
    logger.info("Review complete. Check data/pending_config.json before applying any changes.")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
