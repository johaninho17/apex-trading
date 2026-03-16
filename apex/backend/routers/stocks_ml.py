"""
Stocks ML Router — Fine-Tuning + Auto-Trading Bot endpoints
POST /api/v1/stocks/{symbol}/fine-tune      — kick off specialist fine-tuning
GET  /api/v1/stocks/{symbol}/model-status   — check fine-tune progress
GET  /api/v1/stocks/bot/status              — bot runtime stats
GET  /api/v1/stocks/bot/logs               — live AI reasoning log stream
GET  /api/v1/stocks/bot/context            — Gemini macro + SPY canary data
POST /api/v1/stocks/bot/start               — start stock auto-bot
POST /api/v1/stocks/bot/stop                — stop stock auto-bot
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import os
import json
import subprocess
import threading
import time
import sqlite3
import collections

router = APIRouter()

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BACKEND_ROOT, "data", "stocks", "model_registry.db")
MODELS_DIR = os.path.join(BACKEND_ROOT, "data", "stocks", "models")
SCRIPTS_DIR = os.path.join(BACKEND_ROOT, "scripts", "stocks")
ML_HOME = os.path.expanduser("~/apex_ml/services/stocks/ml")
VENV_PYTHON = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "venv_wsl", "bin", "python3"))

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# ── DB: model_registry ──────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_registry (
            symbol      TEXT PRIMARY KEY,
            status      TEXT NOT NULL DEFAULT 'idle',
            progress    INTEGER DEFAULT 0,
            started_at  REAL,
            completed_at REAL,
            ppo_confidence REAL,
            error_msg   TEXT
        )
    """)
    conn.commit()
    return conn

def _get_model_row(symbol: str) -> Optional[Dict]:
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM model_registry WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
        return dict(row) if row else None

def _upsert_model(symbol: str, **fields):
    with _get_db() as conn:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values())
        conn.execute(
            f"""
            INSERT INTO model_registry (symbol, {cols})
            VALUES (?, {placeholders})
            ON CONFLICT(symbol) DO UPDATE SET {updates}
            """,
            [symbol.upper()] + vals + vals,
        )
        conn.commit()

# ── In-memory bot state ──────────────────────────────────────────────────────

_bot_state: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "positions": 0,
    "daily_trades": 0,
    "last_signal": None,
    "thread": None,
}
_bot_lock = threading.Lock()

# ── Bot reasoning log ring-buffer ────────────────────────────────────────────
# Stores the last 25 lines of AI decision reasoning for the SLM stream.
_MAX_LOG_LINES = 25
_bot_log: collections.deque = collections.deque(maxlen=_MAX_LOG_LINES)
_bot_log_lock = threading.Lock()

def _log(msg: str):
    """Append a timestamped reasoning line to the ring buffer."""
    ts = time.strftime("%H:%M:%S")
    with _bot_log_lock:
        _bot_log.append(f"[{ts}] {msg}")

# ── SPY canary cache ──────────────────────────────────────────────────────────
_spy_cache: Dict[str, Any] = {"change_pct": None, "ts": 0.0}
_SPY_CACHE_TTL = 120  # seconds

def _fetch_spy_change() -> float:
    """Return SPY 24h change % using yfinance."""
    global _spy_cache
    now = time.time()
    if _spy_cache["change_pct"] is not None and now - _spy_cache["ts"] < _SPY_CACHE_TTL:
        return _spy_cache["change_pct"]
    try:
        import yfinance as yf
        tk = yf.Ticker("SPY")
        info = tk.fast_info
        price = float(info.get("lastPrice") or info.get("last_price") or 0)
        prev = float(info.get("previousClose") or info.get("previous_close") or 0)
        pct = round(((price - prev) / prev * 100) if prev else 0.0, 3)
        _spy_cache = {"change_pct": pct, "ts": now}
        return pct
    except Exception:
        return _spy_cache.get("change_pct") or 0.0

# ── Background fine-tune runner ──────────────────────────────────────────────

def _run_fine_tune(symbol: str):
    """Run specialist fine-tune in a background thread using native Linux venv."""
    _upsert_model(symbol, status="training", progress=5, started_at=time.time())

    # Build the script path (native Linux or fallback)
    script = os.path.join(ML_HOME, "train_specialist.py")
    python = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "python3"

    env = os.environ.copy()
    env["FINE_TUNE_SYMBOL"] = symbol
    env["FINE_TUNE_FROM_UNIVERSAL"] = "1"

    try:
        proc = subprocess.Popen(
            [python, script, "--symbol", symbol, "--from-universal"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
        )

        # Simulate progress ticks while the process runs
        tick = 10
        while proc.poll() is None:
            time.sleep(8)
            if tick < 90:
                tick += 5
            _upsert_model(symbol, progress=tick)

        rc = proc.returncode
        if rc == 0:
            # Try to load the confidence from a result JSON if the script produces one
            result_path = os.path.join(MODELS_DIR, f"{symbol}_result.json")
            ppo_conf = 68.0  # placeholder until script writes real eval metric
            if os.path.exists(result_path):
                try:
                    with open(result_path) as f:
                        result = json.load(f)
                        ppo_conf = float(result.get("ppo_confidence", ppo_conf))
                except Exception:
                    pass
            _upsert_model(symbol, status="complete", progress=100,
                         completed_at=time.time(), ppo_confidence=ppo_conf)
        else:
            _upsert_model(symbol, status="failed", progress=0,
                         error_msg=f"Process exited with code {rc}")

    except Exception as e:
        _upsert_model(symbol, status="failed", error_msg=str(e))

# ── Stock Bot loop (paper trading stub) ─────────────────────────────────────

# Watchlist for the bot loop
_BOT_WATCHLIST = ["NVDA", "AAPL", "TSLA", "AMD", "MSFT", "META", "GOOGL", "SPY"]

import random

def _bot_loop():
    """Paper-trading reasoning loop. Emits live AI decision logs."""
    import math
    _log("🚀 Bot started — initializing context...")
    cycle = 0
    while True:
        with _bot_lock:
            if not _bot_state["running"]:
                break

        cycle += 1
        try:
            # ── Fetch SPY macro canary ──
            spy_chg = _fetch_spy_change()
            canary_label = "bullish" if spy_chg > 0.1 else "bearish" if spy_chg < -0.1 else "neutral"
            _log(f"📊 SPY Canary: {spy_chg:+.2f}% → macro {canary_label}")

            # ── Get Gemini macro score ──
            try:
                from services.crypto.oracle import get_macro_score
                macro = get_macro_score()
                macro_label = "Bullish" if macro >= 1.2 else "Bearish" if macro <= 0.8 else "Neutral"
                _log(f"🧭 Gemini Macro Oracle: {macro:.2f} ({macro_label})")
            except Exception:
                macro = 1.0
                _log("🧭 Gemini Macro: unavailable — using neutral 1.0")

            # ── Scan each ticker ──
            for ticker in _BOT_WATCHLIST:
                # Simulate PPO confidence score (will be replaced with real model later)
                ppo_conf = round(50 + (random.random() * 40) - 5, 1)   # 45–90 range
                rsi_sim  = round(30 + random.random() * 40, 1)
                atr_sim  = round(1.5 + random.random() * 4, 2)
                _log(f"🔍 Scanning {ticker} … RSI {rsi_sim}, ATR {atr_sim}%")

                if ppo_conf >= 72:
                    direction = "LONG" if spy_chg > 0 else "HOLD"
                    _log(f"🤖 PPO conviction: {ppo_conf}%. Macro: {macro:.2f}. Signal: {direction}")
                    if direction == "LONG":
                        _log(f"⚡ Signal: {ticker} LONG — stop {atr_sim * 2:.1f}% below")
                        with _bot_lock:
                            _bot_state["daily_trades"] += 1
                    else:
                        _log(f"⏸  {ticker} skipped — macro override (SPY {spy_chg:+.2f}%)")
                else:
                    _log(f"⏭  {ticker} conviction too low ({ppo_conf}%) — SKIP")

        except Exception as e:
            _log(f"⚠️ Cycle error: {str(e)[:60]}")

        _log(f"✅ Cycle {cycle} complete — sleeping 30s...")
        time.sleep(30)

# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/stocks/bot/logs")
async def get_bot_logs():
    """Return the live AI reasoning log buffer (last 25 lines)."""
    with _bot_log_lock:
        lines = list(_bot_log)
    return {"logs": lines, "count": len(lines)}


@router.get("/stocks/bot/context")
async def get_bot_context():
    """
    Return the real-time Context Radar data:
    - gemini_macro: Gemini News Oracle score (0.2-1.8)
    - spy_canary:   SPY 24h change %
    - ppo_confidence: Placeholder (will be real model output)
    """
    import asyncio
    spy_task = asyncio.to_thread(_fetch_spy_change)
    spy_chg = await spy_task

    macro = 1.0
    macro_label = "Neutral"
    try:
        from services.crypto.oracle import get_macro_score
        macro = get_macro_score()
        macro_label = "Bullish" if macro >= 1.2 else "Bearish" if macro <= 0.8 else "Neutral"
    except Exception:
        pass

    # TODO: replace with real PPO inference from the universal model
    ppo_conf = 68.0

    return {
        "gemini_macro": {"score": round(macro, 2), "label": macro_label},
        "spy_canary": {"change_pct": spy_chg, "label": "bullish" if spy_chg > 0.1 else "bearish" if spy_chg < -0.1 else "neutral"},
        "ppo_confidence": {"score": round(ppo_conf, 1)},
    }


@router.post("/stocks/{symbol}/fine-tune")
async def start_fine_tune(symbol: str):
    """Kick off a background specialist fine-tune for the given symbol."""
    sym = symbol.upper()
    row = _get_model_row(sym)
    if row and row["status"] == "training":
        raise HTTPException(status_code=409, detail=f"{sym} is already being fine-tuned")

    thread = threading.Thread(target=_run_fine_tune, args=(sym,), daemon=True)
    thread.start()
    return {"symbol": sym, "status": "training", "message": "Fine-tune started in background"}


@router.get("/stocks/{symbol}/model-status")
async def get_model_status(symbol: str):
    """Return fine-tune status + PPO confidence for a symbol."""
    sym = symbol.upper()
    row = _get_model_row(sym)
    if not row:
        return {"symbol": sym, "status": "idle", "progress": 0, "ppo_confidence": None}

    return {
        "symbol": sym,
        "status": row["status"],
        "progress": row["progress"],
        "ppo_confidence": row["ppo_confidence"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "error_msg": row["error_msg"],
    }


@router.get("/stocks/bot/status")
async def get_bot_status():
    """Return the stock auto-bot runtime stats."""
    with _bot_lock:
        return {
            "running": _bot_state["running"],
            "started_at": _bot_state["started_at"],
            "positions": _bot_state["positions"],
            "daily_trades": _bot_state["daily_trades"],
            "last_signal": _bot_state["last_signal"],
        }


@router.post("/stocks/bot/start")
async def start_bot():
    """Start the stock auto-bot."""
    with _bot_lock:
        if _bot_state["running"]:
            raise HTTPException(status_code=409, detail="Bot is already running")
        _bot_state["running"] = True
        _bot_state["started_at"] = time.time()
        t = threading.Thread(target=_bot_loop, daemon=True)
        _bot_state["thread"] = t
    t.start()
    return {"status": "started"}


@router.post("/stocks/bot/stop")
async def stop_bot():
    """Stop the stock auto-bot."""
    with _bot_lock:
        if not _bot_state["running"]:
            raise HTTPException(status_code=409, detail="Bot is not running")
        _bot_state["running"] = False
        _bot_state["started_at"] = None
    return {"status": "stopped"}
