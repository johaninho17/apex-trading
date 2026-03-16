"""
train_from_live_experience.py — Retrain DRL using the bot's own closed trade history.

Instead of training on 3-year historical CSVs with gemini_macro=1.0 hardcoded,
this script merges the live_experience SQLite rows (which have REAL oracle scores,
session memory, RSI at the time of entry, and actual P&L outcomes) with historical
market bars to retrain the PPO model.

Run manually:
  python -m services.crypto.ml.train_from_live_experience

Or triggered automatically by the monthly cron in oracle.py.
"""

import os
import sys
import logging
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Bootstrap .env for standalone runs (no FastAPI context) ───────────────────
# When called as a script or from execute_daily_ml.py, environment variables
# are not yet set. Load them from the project .env file once.
_ENV_BOOTSTRAPPED = False

def _bootstrap_env():
    global _ENV_BOOTSTRAPPED
    if _ENV_BOOTSTRAPPED:
        return
    _ENV_BOOTSTRAPPED = True
    try:
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        _env_file = os.path.join(_root, ".env")
        if not os.path.exists(_env_file):
            # Also try backend root
            _env_file = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")
        if os.path.exists(_env_file):
            with open(_env_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
            logger.info("[live_retrain] Loaded credentials from .env file")
        else:
            logger.debug("[live_retrain] No .env file found — using existing environment")
    except Exception as e:
        logger.debug(f"[live_retrain] .env bootstrap skipped: {e}")

# ── Paths ─────────────────────────────────────────────────────────────────────
# Import DB path from store to guarantee we use the exact same file
try:
    from services.crypto.store import _DB_FILE
except ImportError:
    # fallback if called as __main__ from backend dir
    import sys as _sys
    _sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    from services.crypto.store import _DB_FILE

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_DATA_DIR = os.path.dirname(_DB_FILE)
_MODELS_DIR = os.path.join(_DATA_DIR, "models")
_TRAINING_DIR = os.path.join(_DATA_DIR, "live_training")
os.makedirs(_TRAINING_DIR, exist_ok=True)
os.makedirs(_MODELS_DIR, exist_ok=True)

MIN_ROWS_TO_RETRAIN = 50   # Don't bother retraining with fewer than 50 closed trades


def _load_live_experience() -> List[Dict[str, Any]]:
    """Load all closed live_experience rows from SQLite."""
    con = sqlite3.connect(_DB_FILE)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM live_experience WHERE exit_ts IS NOT NULL ORDER BY entry_ts ASC"
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _fetch_bars_for_window(symbol: str, entry_ts_ms: int, lookback_bars: int = 300) -> List[Dict]:
    """
    Pull ~300 15-min bars ending at entry_ts from Alpaca for context.
    Bootstraps .env credentials when running outside FastAPI context.
    """
    _bootstrap_env()   # no-op if already loaded or running inside FastAPI
    try:
        import pandas as pd
        from services.crypto.market_data import fetch_bars
        bars_df = fetch_bars(symbol, timeframe="15Min", limit=lookback_bars)
        if bars_df.empty:
            return []
        entry_ts_sec = entry_ts_ms / 1000.0
        bars_df["ts_float"] = pd.to_datetime(bars_df["timestamp"]).apply(
            lambda x: x.timestamp() if hasattr(x, "timestamp") else 0
        )
        bars_df = bars_df[bars_df["ts_float"] <= entry_ts_sec].tail(lookback_bars)
        return bars_df.to_dict(orient="records")
    except Exception as e:
        logger.warning(f"[live_retrain] Could not fetch bars for {symbol}: {e}")
        return []


def _load_enrichment_context() -> Dict[str, Any]:
    """
    Pre-load enrichment data from DB once per training run.

    Returns:
      decision_by_trace: {trace_id -> {horizon_min -> pnl_pct}}
        Forward P&L measured 15m/30m/60m after each brain decision.
        This tells the model "was my reasoning correct?" independent of trade execution.

      strategy_win_rates: {strategy_name -> win_rate_pct}
        Historical win rate per strategy across all closed live_experience rows.
        Model learns which strategies are currently working.

      oracle_scores_ts: [(ts_ms, score), ...]  sorted ascending
        All oracle scores ever recorded. Used to compute oracle momentum
        (is sentiment trending up or down toward this entry).
    """
    ctx: Dict[str, Any] = {
        "decision_by_trace": {},
        "strategy_win_rates": {},
        "oracle_scores_ts": [],
    }
    try:
        con = sqlite3.connect(_DB_FILE)
        con.row_factory = sqlite3.Row

        # ── Decision outcomes: forward P&L by horizon ────────────────────────
        outcome_rows = con.execute(
            "SELECT trace_id, horizon_min, pnl_pct FROM decision_outcomes WHERE pnl_pct IS NOT NULL"
        ).fetchall()
        for r in outcome_rows:
            tid = int(r["trace_id"] or 0)
            hz  = int(r["horizon_min"] or 0)
            pnl = float(r["pnl_pct"] or 0.0)
            if tid not in ctx["decision_by_trace"]:
                ctx["decision_by_trace"][tid] = {}
            ctx["decision_by_trace"][tid][hz] = pnl

        # ── Strategy win rates from live_experience ──────────────────────────
        strat_rows = con.execute(
            "SELECT strategy_used, outcome_pnl_pct FROM live_experience WHERE exit_ts IS NOT NULL AND strategy_used IS NOT NULL"
        ).fetchall()
        strat_wins: Dict[str, List[float]] = {}
        for r in strat_rows:
            s = str(r["strategy_used"] or "")
            p = float(r["outcome_pnl_pct"] or 0.0)
            if s:
                strat_wins.setdefault(s, []).append(p)
        for strat, pnls in strat_wins.items():
            wins = sum(1 for p in pnls if p > 0)
            ctx["strategy_win_rates"][strat] = round(wins / len(pnls) * 100.0, 1) if pnls else 50.0

        # ── Oracle score history ─────────────────────────────────────────────
        oracle_rows = con.execute(
            "SELECT ts, score FROM oracle_history ORDER BY ts ASC LIMIT 500"
        ).fetchall()
        ctx["oracle_scores_ts"] = [(int(r["ts"]), float(r["score"])) for r in oracle_rows]

        con.close()
    except Exception as e:
        logger.warning(f"[live_retrain] enrichment context load failed: {e}")

    logger.info(
        f"[live_retrain] Enrichment context: "
        f"{len(ctx['decision_by_trace'])} decision traces, "
        f"{len(ctx['strategy_win_rates'])} strategies, "
        f"{len(ctx['oracle_scores_ts'])} oracle scores"
    )
    return ctx


def _encode_regime(regime: str) -> float:
    """Encode Gemini regime as a numeric multiplier for the obs vector."""
    mapping = {"bullish": 1.2, "strong_bullish": 1.4, "neutral": 1.0, "bearish": 0.8, "strong_bearish": 0.6}
    return mapping.get(str(regime or "neutral").lower(), 1.0)


def _oracle_momentum(entry_ts_ms: int, oracle_scores_ts: List) -> float:
    """
    Slope of oracle scores in the 2 hours before entry.
    Positive = sentiment improving toward entry (good sign for buys).
    Negative = sentiment degrading (warning sign).
    Returns 0.0 if not enough data.
    """
    window_start = entry_ts_ms - 2 * 3600 * 1000  # 2h window
    recent = [(ts, sc) for ts, sc in oracle_scores_ts if window_start <= ts <= entry_ts_ms]
    if len(recent) < 2:
        return 0.0
    # Linear slope approximation
    xs = [ts for ts, _ in recent]
    ys = [sc for _, sc in recent]
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n)) + 1e-9
    slope = num / den
    # Normalise to a small float ~[-1, 1] range
    return float(max(-1.0, min(1.0, slope * 1e9)))


def build_live_training_csv(rows: List[Dict], output_path: str,
                             enrichment_ctx: Dict[str, Any] = None) -> int:
    """
    Merge live_experience rows with market bars + enrichment signals to produce
    an engineered training CSV.

    NEW signals injected per row (beyond base OHLCV features):
      gemini_macro        — real oracle score at entry (overrides synthetic 1.0)
      gemini_regime_enc   — bullish=1.2 / neutral=1.0 / bearish=0.8
      session_win_rate    — rolling 6h win rate at entry
      session_pnl_pct     — rolling 6h P&L at entry
      slm_confidence      — Ollama/SLM confidence score at entry
      event_score         — news event score at entry
      oracle_momentum     — slope of oracle scores in 2h before entry
      strategy_win_rate   — historical win% of the strategy used
      outcome_pnl_15m     — forward P&L 15m after brain decision (from decision_outcomes)
      outcome_pnl_30m     — forward P&L 30m after brain decision
      outcome_pnl_60m     — forward P&L 60m after brain decision

    The CryptoTradingEnv consumes ALL columns dynamically, so these automatically
    expand the PPO observation space — no env code changes required.
    """
    import pandas as pd
    from services.crypto.ml.feature_engineering import engineer_features

    if enrichment_ctx is None:
        enrichment_ctx = {}
    decision_by_trace = enrichment_ctx.get("decision_by_trace", {})
    strategy_win_rates = enrichment_ctx.get("strategy_win_rates", {})
    oracle_scores_ts   = enrichment_ctx.get("oracle_scores_ts", [])

    all_frames = []
    seen_symbols = set()

    for exp in rows:
        symbol = exp.get("symbol", "")
        if not symbol:
            continue

        # ── Base signals from live_experience ────────────────────────────────
        gemini_score     = float(exp.get("gemini_score", 1.0) or 1.0)
        gemini_regime    = str(exp.get("gemini_regime", "neutral") or "neutral")
        outcome_pnl_pct  = float(exp.get("outcome_pnl_pct", 0.0) or 0.0)
        session_win_rate = float(exp.get("session_win_rate", 0.0) or 0.0)
        session_pnl_pct  = float(exp.get("session_pnl_pct", 0.0) or 0.0)
        slm_confidence   = float(exp.get("slm_confidence", 0.5) or 0.5)
        event_score      = float(exp.get("event_score_at_entry", 0.0) or 0.0)
        strategy         = str(exp.get("strategy_used", "") or "")
        trace_id         = int(exp.get("decision_trace_id", 0) or 0)
        entry_ts_ms      = int(exp.get("entry_ts", 0) or 0)

        # ── Enrichment signals ────────────────────────────────────────────────
        regime_enc        = _encode_regime(gemini_regime)
        oracle_mom        = _oracle_momentum(entry_ts_ms, oracle_scores_ts)
        strat_win_rate    = strategy_win_rates.get(strategy, 50.0)

        # Forward P&L from decision_outcomes (how good was the brain read?)
        trace_outcomes = decision_by_trace.get(trace_id, {})
        outcome_15m = float(trace_outcomes.get(15,  0.0))
        outcome_30m = float(trace_outcomes.get(30,  0.0))
        outcome_60m = float(trace_outcomes.get(60,  0.0))

        # ── Fetch OHLCV bars for this trade's entry window ────────────────────
        bars = _fetch_bars_for_window(symbol, entry_ts_ms)
        if len(bars) < 50:
            logger.debug(f"[live_retrain] Skipping {symbol} @ {entry_ts_ms} — only {len(bars)} bars")
            continue

        df = pd.DataFrame(bars)
        try:
            eng = engineer_features(df)
        except Exception as e:
            logger.warning(f"[live_retrain] Feature engineering failed for {symbol}: {e}")
            continue

        # ── Inject all signals into every bar of the window ──────────────────
        # (constant per trade — the model learns the context around the entry)
        eng["gemini_macro"]       = gemini_score          # real oracle score
        eng["gemini_regime_enc"]  = regime_enc             # encoded regime
        eng["session_win_rate"]   = session_win_rate       # rolling perf
        eng["session_pnl_pct"]    = session_pnl_pct
        eng["slm_confidence"]     = slm_confidence         # Ollama confidence
        eng["event_score"]        = event_score            # news event signal
        eng["oracle_momentum"]    = oracle_mom             # oracle trend direction
        eng["strategy_win_rate"]  = strat_win_rate         # strategy historical edge
        eng["outcome_pnl_15m"]    = outcome_15m            # was brain right at 15m?
        eng["outcome_pnl_30m"]    = outcome_30m            # was brain right at 30m?
        eng["outcome_pnl_60m"]    = outcome_60m            # was brain right at 60m?
        # Diagnostics only — not used by env obs vector
        eng["outcome_pnl_pct"]    = outcome_pnl_pct
        eng["strategy_used"]      = strategy
        eng["symbol"]             = symbol

        all_frames.append(eng.tail(100))
        seen_symbols.add(symbol)

    if not all_frames:
        logger.warning("[live_retrain] No training frames produced. Check bar fetch and feature engineering.")
        return 0

    combined = pd.concat(all_frames, ignore_index=True)

    # Drop string-only diagnostic columns — CryptoTradingEnv converts all features to
    # np.float32, so string columns will crash it. Keep them out of the CSV entirely.
    _STRING_COLS = ["strategy_used", "symbol", "outcome_pnl_pct"]  # diagnostics only
    combined = combined.drop(columns=[c for c in _STRING_COLS if c in combined.columns], errors="ignore")
    combined = combined.dropna()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    combined.to_csv(output_path, index=False)
    logger.info(
        f"[live_retrain] Saved {len(combined)} training rows → {output_path} "
        f"(symbols: {', '.join(sorted(seen_symbols))}, "
        f"new signals: gemini_regime_enc, slm_confidence, event_score, oracle_momentum, "
        f"strategy_win_rate, outcome_pnl_15m/30m/60m)"
    )
    return len(combined)


def run_live_retrain(force: bool = False) -> Dict[str, Any]:
    """
    Main entry point. Loads live_experience, builds training data, retrains PPO.
    Returns a summary dict.
    """
    rows = _load_live_experience()
    logger.info(f"[live_retrain] Loaded {len(rows)} closed live_experience rows.")

    if len(rows) < MIN_ROWS_TO_RETRAIN and not force:
        return {
            "status": "skipped",
            "reason": f"Only {len(rows)} closed trades. Need >= {MIN_ROWS_TO_RETRAIN} to retrain.",
            "rows": len(rows),
        }

    # Load enrichment context once — decision outcomes, strategy win rates, oracle history
    enrich_ctx = _load_enrichment_context()

    # Build per-symbol CSV files for training
    symbols = list(set(r["symbol"] for r in rows if r.get("symbol")))
    results = {}

    # train_agent reads CSV from DATA_DIR/{ticker}_15Min_engineered.csv
    _AI_TRAINING_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, "data", "ai_training"))
    os.makedirs(_AI_TRAINING_DIR, exist_ok=True)

    for symbol in symbols:
        sym_rows = [r for r in rows if r.get("symbol") == symbol]
        if len(sym_rows) < 10:
            logger.info(f"[live_retrain] {symbol}: only {len(sym_rows)} rows, skipping.")
            continue

        safe_sym = symbol.replace("/", "_")

        # Archive copy in live_training/ for record-keeping
        archive_csv = os.path.join(_TRAINING_DIR, f"{safe_sym}_live_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv")
        # Primary path — this is where train_agent reads from
        primary_csv = os.path.join(_AI_TRAINING_DIR, f"{safe_sym}_15Min_engineered.csv")

        n_rows = build_live_training_csv(sym_rows, archive_csv, enrichment_ctx=enrich_ctx)
        if n_rows < 10:
            results[symbol] = {"status": "insufficient_data", "rows": n_rows}
            continue

        # Copy to primary path so train_agent finds it
        import shutil
        shutil.copy2(archive_csv, primary_csv)
        logger.info(f"[live_retrain] Written {n_rows} rows to {primary_csv}")

        # Retrain PPO on this CSV
        try:
            from services.crypto.ml.train_specialist import train_agent
            model_path = os.path.join(_MODELS_DIR, f"ppo_{safe_sym}_specialist.zip")
            train_agent(
                ticker=safe_sym,
                timesteps=50_000,   # Half of normal — live data quality > quantity
                resume=os.path.exists(model_path),
            )
            results[symbol] = {"status": "retrained", "rows": n_rows}
            logger.info(f"[live_retrain] ✅ {symbol} retrained on {n_rows} real experience rows.")
        except Exception as e:
            results[symbol] = {"status": "error", "error": str(e)}
            logger.error(f"[live_retrain] ❌ {symbol} training failed: {e}")

    return {
        "status": "complete",
        "total_experience_rows": len(rows),
        "symbols_retrained": results,
        "run_ts": int(time.time()),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.path.insert(0, _BACKEND_DIR)
    result = run_live_retrain(force="--force" in sys.argv)
    print(json.dumps(result, indent=2))
