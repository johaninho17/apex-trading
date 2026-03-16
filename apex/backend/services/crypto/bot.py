import asyncio
import threading
import time
from importlib import import_module
from typing import Any, Dict, List, Optional, Tuple

from core.config_manager import get_config, update_config
from core.state import state as global_state
from services import notification_manager

from . import store
from .decision_feedback import evaluate_pending_decision_outcomes
from .policy_overlays import ensure_brain_self_score_snapshot, refresh_symbol_policy_overlays
from .strategy import evaluate_symbol

DEFAULT_CRYPTO_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "trading_mode": "offline",  # live | offline
    "account_mode": "paper",  # paper | live
    "poll_interval_sec": 30,
    "timeframe": "1Min",
    "symbols": ["BTC/USD", "ETH/USD"],
    "scanner_watchlist": [],
    "auto_discover_pairs": True,
    "auto_discover_limit": 300,
    "auto_discover_quote": "USD",
    "auto_discover_tradable_only": True,
    "min_order_notional_usd": 10.0,
    "max_open_positions": 10,
    "max_notional_per_trade": 5000.0,
    "max_total_exposure": 50000.0,
    "max_daily_drawdown_pct": 5.0,
    "cooldown_sec": 90,
    "anti_spam_sec": 30,
    "short_term": {
        "mean_reversion_enabled": True,
        "breakout_enabled": True,
        "base_notional": 500.0,
        "breakout_notional": 750.0,
        "rsi_oversold": 28.0,
        "rsi_overbought": 72.0,
        "breakout_lookback_bars": 20,
        "breakout_volume_mult": 1.9,
        "breakout_buffer_pct": 0.15,
        "dip_notional_multiplier": 1.3,
    },
    "long_term": {
        "ma_crossover_enabled": True,
        "ma_fast": 50,
        "ma_slow": 200,
        "crossover_notional": 800.0,
        "dca_enabled": True,
        "dca_notional": 250.0,
        "dca_interval_min": 180,
        "dca_dip_pct": 1.5,
        "dca_dip_multiplier": 1.5,
    },
    "synthetic_exits": {
        "enabled": True,
        "take_profit_pct": 3.0,
        "stop_loss_pct": 1.8,
    },
}

_DISCOVERY_CACHE: Dict[str, Any] = {
    "ts_sec": 0.0,
    "key": None,
    "symbols": [],
}

_ACTIVE_MIN_MS = 6 * 60 * 60 * 1000
_RECENT_ACTIVE_MS = 24 * 60 * 60 * 1000
_RECENT_TRACKED_INTERVAL_MS = 5 * 60 * 1000
_ELIGIBLE_SWEEP_WINDOW_MS = 15 * 60 * 1000


def _market_data_module():
    return import_module(".market_data", __package__)


def _execution_module():
    return import_module(".execution", __package__)


def _normalize_discovery_symbol(raw_symbol: Any) -> str:
    raw = str(raw_symbol or "").strip().upper()
    if not raw:
        return ""
    if "/" in raw:
        return raw
    for quote in ("USD", "USDT", "USDC"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}/{quote}"
    return raw


def _normalize_account_mode(value: Optional[str]) -> str:
    return "live" if str(value or "").strip().lower() == "live" else "paper"


def _current_credential_mode() -> str:
    raw = get_config().get("stocks", {}).get("crypto", {}).get("account_mode", "paper")
    return _normalize_account_mode(raw)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _get_crypto_cfg(resolve_symbols: bool = True) -> Dict[str, Any]:
    cfg = get_config().get("stocks", {}).get("crypto", {})
    if not isinstance(cfg, dict):
        cfg = {}
    merged = _deep_merge(DEFAULT_CRYPTO_CONFIG, cfg)
    manual_symbols = [str(s).strip().upper() for s in merged.get("symbols", []) if str(s).strip()]
    scanner_watchlist = [
        _normalize_discovery_symbol(s)
        for s in merged.get("scanner_watchlist", [])
        if _normalize_discovery_symbol(s)
    ]
    merged["trading_mode"] = "live" if str(merged.get("trading_mode", "offline")).lower() == "live" else "offline"
    merged["account_mode"] = _normalize_account_mode(merged.get("account_mode", "paper"))
    credential_mode = merged["account_mode"]
    merged["auto_discover_pairs"] = bool(merged.get("auto_discover_pairs", False))
    merged["auto_discover_limit"] = max(1, min(300, int(merged.get("auto_discover_limit", 20) or 20)))
    merged["auto_discover_quote"] = str(merged.get("auto_discover_quote", "USD") or "USD").strip().upper() or "USD"
    merged["auto_discover_tradable_only"] = bool(merged.get("auto_discover_tradable_only", True))
    merged["min_order_notional_usd"] = max(1.0, float(merged.get("min_order_notional_usd", 10.0) or 10.0))

    discovered_symbols: List[str] = []
    if resolve_symbols and merged["auto_discover_pairs"]:
        discovered_symbols = _discover_symbols(
            quote=merged["auto_discover_quote"],
            limit=merged["auto_discover_limit"],
            tradable_only=merged["auto_discover_tradable_only"],
            mode=credential_mode,
        )

    merged["symbols"] = discovered_symbols or manual_symbols or list(DEFAULT_CRYPTO_CONFIG["symbols"])
    merged["scanner_watchlist"] = scanner_watchlist[:30]
    try:
        store.migrate_tracked_saved_symbols_sync(credential_mode, merged["scanner_watchlist"])
    except Exception:
        pass
    merged["poll_interval_sec"] = max(5, int(merged.get("poll_interval_sec", 30) or 30))
    merged["anti_spam_sec"] = max(1, int(merged.get("anti_spam_sec", 30)))
    merged["cooldown_sec"] = max(0, int(merged.get("cooldown_sec", 90)))
    return merged


def _discover_symbols(quote: str, limit: int, tradable_only: bool, mode: Optional[str] = None) -> List[str]:
    now_sec = time.time()
    cache_key = (str(quote or "").upper(), int(limit), bool(tradable_only), _normalize_account_mode(mode))
    try:
        cached_key = _DISCOVERY_CACHE.get("key")
        cached_ts = float(_DISCOVERY_CACHE.get("ts_sec", 0.0) or 0.0)
        cached_symbols = _DISCOVERY_CACHE.get("symbols") or []
        if cached_key == cache_key and (now_sec - cached_ts) <= 300 and cached_symbols:
            return [str(s).upper() for s in cached_symbols]
    except Exception:
        pass

    out: List[str] = []
    try:
        market_data = _market_data_module()
        assets = market_data.list_crypto_assets(limit=900, mode=mode)
        for a in assets:
            symbol = _normalize_discovery_symbol(a.get("symbol", ""))
            if not symbol:
                continue
            if tradable_only and not bool(a.get("tradable", True)):
                continue
            status = str(a.get("status", "active") or "").strip().lower()
            if status and ("inactive" in status or "delisted" in status):
                continue
            if quote and not symbol.endswith(f"/{quote}"):
                continue
            out.append(symbol)
    except Exception:
        return []

    out = sorted(set(out))[: max(1, min(int(limit), 300))]
    _DISCOVERY_CACHE["ts_sec"] = now_sec
    _DISCOVERY_CACHE["key"] = cache_key
    _DISCOVERY_CACHE["symbols"] = out
    return out


def _persist_crypto_cfg(next_cfg: Dict[str, Any]) -> Dict[str, Any]:
    merged = _deep_merge(DEFAULT_CRYPTO_CONFIG, next_cfg or {})
    updated = update_config({"stocks": {"crypto": merged}})
    return _deep_merge(DEFAULT_CRYPTO_CONFIG, updated.get("stocks", {}).get("crypto", {}))


def _run_coro(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _send_crypto_event(event_type: str, payload: Dict[str, Any]) -> None:
    _run_coro(notification_manager.broadcast("crypto", event_type, payload))


def _send_crypto_toast(title: str, message: str, type_: str = "info", duration: int = 3200, extra: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        "title": title,
        "message": message,
        "type": type_,
        "duration": duration,
    }
    if extra:
        payload.update(extra)
    _send_crypto_event("toast", payload)


def _max_feedback_horizon_min(cfg: Dict[str, Any]) -> int:
    feedback_cfg = cfg.get("decision_feedback", {}) or {}
    horizons = [int(value or 0) for value in (feedback_cfg.get("horizons_min", []) or []) if int(value or 0) > 0]
    return max(horizons) if horizons else (24 * 60)


def _tracked_runtime_sets(account_mode: str, *, held_symbols: Optional[List[str]] = None) -> Dict[str, List[str]]:
    tracked_rows = store.list_tracked_symbols_sync(account_mode, include_idle=False, limit=1200)
    active: List[str] = []
    recent: List[str] = []
    saved: List[str] = []
    held = [_normalize_discovery_symbol(symbol) for symbol in (held_symbols or []) if _normalize_discovery_symbol(symbol)]

    for row in tracked_rows:
        symbol = _normalize_discovery_symbol(row.get("symbol"))
        if not symbol:
            continue
        if bool(row.get("saved_manual")) and symbol not in saved:
            saved.append(symbol)
        active_state = str(row.get("active_state", "idle") or "idle").strip().lower()
        if active_state == "active":
            active.append(symbol)
        elif active_state == "recently_active":
            recent.append(symbol)

    return {
        "held": held,
        "active": [symbol for symbol in active if symbol not in held],
        "recent": [symbol for symbol in recent if symbol not in held and symbol not in active],
        "saved": [symbol for symbol in saved if symbol not in held and symbol not in active and symbol not in recent],
    }


def _touch_tracked_decision_state(symbol: str, account_mode: str, decision_ts: int, feedback_window_ms: int) -> None:
    normalized = _normalize_discovery_symbol(symbol)
    if not normalized:
        return
    try:
        existing = store.get_tracked_symbol_sync(normalized, account_mode)
        recent_until_ts = max(
            int(existing.get("recent_until_ts", 0) or 0),
            int(decision_ts or 0) + max(int(feedback_window_ms or 0), _RECENT_ACTIVE_MS),
        )
        active_state = str(existing.get("active_state", "idle") or "idle").strip().lower()
        updates: Dict[str, Any] = {
            "saved_manual": bool(existing.get("saved_manual")),
            "last_decision_ts": int(decision_ts or 0),
            "recent_until_ts": recent_until_ts,
            "last_rank_score": float(existing.get("last_rank_score", 0.0) or 0.0),
            "metadata": dict(existing.get("metadata") or {}),
        }
        if active_state == "active":
            updates.update({
                "active_state": "active",
                "active_reason": str(existing.get("active_reason", "") or "recent_decision_feedback"),
                "active_since_ts": int(existing.get("active_since_ts", 0) or 0),
                "active_min_until_ts": int(existing.get("active_min_until_ts", 0) or 0),
                "monitor_tier": "active",
            })
        else:
            updates.update({
                "active_state": "recently_active",
                "active_reason": "recent_decision_feedback",
                "active_since_ts": int(existing.get("active_since_ts", 0) or 0),
                "active_min_until_ts": 0,
                "monitor_tier": "recently_active",
            })
        store.upsert_tracked_symbol_sync(normalized, account_mode, **updates)
    except Exception:
        pass


def _runtime_symbols(cfg: Dict[str, Any], *, held_symbols: Optional[List[str]] = None) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()

    def _append(symbol: Any) -> None:
        normalized = _normalize_discovery_symbol(symbol)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)

    for symbol in held_symbols or []:
        _append(symbol)
    tracked_sets = _tracked_runtime_sets(str(cfg.get("account_mode", "paper") or "paper"), held_symbols=held_symbols)
    for symbol in tracked_sets.get("active", []):
        _append(symbol)

    try:
        snapshot = store.get_latest_universe_snapshot_sync(limit_rankings=60, selected_only=False) or {}
    except Exception:
        snapshot = {}
    for symbol in snapshot.get("selected_symbols", []) or []:
        _append(symbol)
    for row in snapshot.get("rankings", []) or []:
        _append(row.get("symbol"))
    for symbol in tracked_sets.get("recent", []):
        _append(symbol)
    for symbol in cfg.get("symbols", []) or []:
        _append(symbol)

    return ordered[:120] or [
        _normalize_discovery_symbol(symbol)
        for symbol in (cfg.get("symbols", []) or DEFAULT_CRYPTO_CONFIG["symbols"])
        if _normalize_discovery_symbol(symbol)
    ]


def _signed_pnl_pct(side: str, entry_price: float, exit_price: float) -> float:
    if entry_price <= 0 or exit_price <= 0:
        return 0.0
    pnl_pct = ((float(exit_price) - float(entry_price)) / float(entry_price)) * 100.0
    if str(side or "").strip().lower() == "sell":
        pnl_pct *= -1.0
    return round(pnl_pct, 4)


def _close_open_live_experience(symbol: str, exit_ts: int, exit_price: float, exit_reason: str) -> None:
    rows = store.get_live_experience_sync(limit=20, closed_only=False, symbol=symbol)
    for row in rows:
        if int(row.get("exit_ts", 0) or 0) > 0:
            continue
        entry_ts = int(row.get("entry_ts", exit_ts) or exit_ts)
        entry_price = float(row.get("entry_price", 0.0) or 0.0)
        side = str(row.get("side", "buy") or "buy")
        hold_duration_min = max(0.0, (float(exit_ts) - float(entry_ts)) / 60000.0)
        pnl_pct = _signed_pnl_pct(side, entry_price, exit_price)
        store.close_live_experience_sync(
            int(row.get("id", 0) or 0),
            exit_ts=exit_ts,
            exit_price=float(exit_price or 0.0),
            outcome_pnl_pct=pnl_pct,
            hold_duration_min=hold_duration_min,
            exit_reason=exit_reason,
        )
        break


class CryptoBotRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._iterations = 0
        self._started_at_ms: Optional[int] = None
        self._last_cycle_ms: Optional[int] = None
        self._last_error: Optional[str] = None
        self._halted = False
        self._halted_reason: Optional[str] = None
        self._last_signal_ts: Dict[Tuple[str, str, str], int] = {}
        self._cooldown_until: Dict[str, int] = {}
        self._last_dca_ts: Dict[str, int] = {}
        self._last_status: Dict[str, Any] = {}
        self._last_error_toast_ms: int = 0
        self._last_error_toast_msg: str = ""
        self._last_feedback_eval_ms: int = 0
        self._symbol_last_eval_ms: Dict[str, int] = {}
        self._eligible_cursor: int = 0

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "started_at": self._started_at_ms,
                "last_cycle_at": self._last_cycle_ms,
                "iterations": self._iterations,
                "last_error": self._last_error,
                "halted": self._halted,
                "halted_reason": self._halted_reason,
                "last_status": self._last_status,
            }

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._running and self._thread and self._thread.is_alive():
                return self.status()
            self._stop_event.clear()
            self._running = True
            self._iterations = 0
            self._last_error = None
            self._halted = False
            self._halted_reason = None
            self._started_at_ms = int(time.time() * 1000)
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="crypto-bot")
            self._thread.start()
        _run_coro(store.update_runtime_state(
            running=True,
            started_at=self._started_at_ms,
            last_error=None,
            halted=False,
            halted_reason=None,
        ))
        _send_crypto_toast("Crypto Bot", "Crypto auto-trader started.", "success")
        return self.status()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.5)
        with self._lock:
            self._running = False
        _run_coro(store.update_runtime_state(running=False))
        _send_crypto_toast("Crypto Bot", "Crypto auto-trader stopped.", "warning")
        return self.status()

    def _set_error(self, err: str) -> None:
        with self._lock:
            self._last_error = err
        _run_coro(store.update_runtime_state(last_error=err))

    def _set_halted(self, halted: bool, reason: Optional[str]) -> None:
        with self._lock:
            self._halted = bool(halted)
            self._halted_reason = reason
        _run_coro(store.update_runtime_state(halted=bool(halted), halted_reason=reason))

    def _should_emit_error_toast(self, msg: str, min_interval_sec: int = 90) -> bool:
        now_ms = int(time.time() * 1000)
        last_ms = int(self._last_error_toast_ms or 0)
        last_msg = str(self._last_error_toast_msg or "")
        if msg != last_msg or (now_ms - last_ms) >= (min_interval_sec * 1000):
            self._last_error_toast_ms = now_ms
            self._last_error_toast_msg = msg
            return True
        return False

    def _run_loop(self) -> None:
        _run_coro(store.init_db())
        while not self._stop_event.is_set():
            cfg = _get_crypto_cfg()
            try:
                if global_state.is_paused or global_state.is_domain_paused("stocks"):
                    _run_coro(store.update_runtime_state(
                        running=True,
                        last_heartbeat=int(time.time() * 1000),
                    ))
                    time.sleep(1.0)
                    continue

                if not bool(cfg.get("enabled", True)):
                    time.sleep(2.0)
                    continue

                self._cycle(cfg)
            except Exception as e:
                err_text = str(e)
                self._set_error(err_text)
                if self._should_emit_error_toast(err_text):
                    _send_crypto_toast("Crypto Bot Error", err_text, "error", duration=6000)
            time.sleep(float(cfg.get("poll_interval_sec", 30)))

        with self._lock:
            self._running = False
        _run_coro(store.update_runtime_state(running=False, last_heartbeat=int(time.time() * 1000)))

    def _allow_signal_toast(self, symbol: str, strategy: str, side: str, anti_spam_sec: int) -> bool:
        key = (symbol, strategy, side)
        now_ms = int(time.time() * 1000)
        last = int(self._last_signal_ts.get(key, 0))
        if now_ms - last < anti_spam_sec * 1000:
            return False
        self._last_signal_ts[key] = now_ms
        return True

    def _on_cooldown(self, symbol: str) -> bool:
        until = int(self._cooldown_until.get(symbol, 0))
        return int(time.time() * 1000) < until

    def _set_cooldown(self, symbol: str, sec: int) -> None:
        if sec <= 0:
            return
        self._cooldown_until[symbol] = int(time.time() * 1000) + (sec * 1000)

    def _symbol_due(self, symbol: str, now_ms: int, min_interval_ms: int) -> bool:
        last_ms = int(self._symbol_last_eval_ms.get(symbol, 0) or 0)
        if last_ms <= 0:
            return True
        return (now_ms - last_ms) >= max(0, int(min_interval_ms or 0))

    def _mark_symbol_evaluated(self, symbol: str, now_ms: int) -> None:
        if symbol:
            self._symbol_last_eval_ms[str(symbol).upper()] = int(now_ms or 0)

    def _eligible_rotation_symbols(
        self,
        eligible_symbols: List[str],
        exclude: List[str],
        *,
        poll_interval_sec: int,
    ) -> List[str]:
        pool = [symbol for symbol in eligible_symbols if symbol not in set(exclude)]
        if not pool:
            return []
        cycles_per_window = max(1, int(_ELIGIBLE_SWEEP_WINDOW_MS / max(1000, int(poll_interval_sec) * 1000)))
        slice_size = max(1, min(25, (len(pool) + cycles_per_window - 1) // cycles_per_window))
        start = int(self._eligible_cursor % len(pool))
        picked: List[str] = []
        for offset in range(min(slice_size, len(pool))):
            picked.append(pool[(start + offset) % len(pool)])
        self._eligible_cursor = (start + slice_size) % max(1, len(pool))
        return picked

    def _cycle(self, cfg: Dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        trading_live = str(cfg.get("trading_mode", "offline")).lower() == "live"
        credential_mode = _normalize_account_mode(cfg.get("account_mode", "paper"))
        market_data = _market_data_module()
        execution = _execution_module()
        account = market_data.get_account_summary(mode=credential_mode)
        positions = market_data.get_crypto_positions(mode=credential_mode)
        pos_map = {str(p.get("symbol", "")): p for p in positions}
        total_exposure = sum(abs(float(p.get("market_value", 0.0) or 0.0)) for p in positions)
        live_equity = float(account.get("equity", 0.0) or 0.0)
        oracle_state: Dict[str, Any] = {}
        try:
            from .oracle import get_current_oracle_state

            oracle_state = dict(get_current_oracle_state() or {})
        except Exception:
            oracle_state = {}
        macro_risk_mult = float(oracle_state.get("risk_multiplier", 1.0) or 1.0)
        gemini_regime = str(oracle_state.get("market_regime", "normal") or "normal")

        runtime_state = _run_coro(store.get_runtime_state())
        day_start_eq = runtime_state.get("day_start_equity")
        day_start_ts = runtime_state.get("day_start_ts")
        if day_start_eq is None or day_start_ts is None:
            _run_coro(store.update_runtime_state(
                day_start_equity=float(account.get("equity", 0.0) or 0.0),
                day_start_ts=now_ms,
            ))
            day_start_eq = float(account.get("equity", 0.0) or 0.0)
            day_start_ts = now_ms

        # Reset day baseline every 24h.
        if now_ms - int(day_start_ts) >= 24 * 60 * 60 * 1000:
            _run_coro(store.update_runtime_state(
                day_start_equity=float(account.get("equity", 0.0) or 0.0),
                day_start_ts=now_ms,
                halted=False,
                halted_reason=None,
            ))
            self._set_halted(False, None)
            day_start_eq = float(account.get("equity", 0.0) or 0.0)

        max_daily_dd = float(cfg.get("max_daily_drawdown_pct", 4.0) or 4.0)
        if day_start_eq > 0:
            floor = day_start_eq * (1.0 - max(0.1, max_daily_dd) / 100.0)
            if float(account.get("equity", 0.0) or 0.0) <= floor:
                if not self._halted:
                    reason = f"Daily drawdown limit reached ({max_daily_dd:.2f}%)."
                    self._set_halted(True, reason)
                    _send_crypto_toast("Crypto Risk Halt", reason, "error", duration=6000)
                    _run_coro(store.record_action(
                        action_type="risk_halt",
                        status="blocked",
                        reason=reason,
                        payload={"equity": account.get("equity"), "day_start_equity": day_start_eq},
                    ))

        anti_spam_sec = int(cfg.get("anti_spam_sec", 30) or 30)
        cooldown_sec = int(cfg.get("cooldown_sec", 90) or 90)
        min_order_notional = float(cfg.get("min_order_notional_usd", 10.0) or 10.0)

        tracked_sets = _tracked_runtime_sets(credential_mode, held_symbols=list(pos_map.keys()))
        try:
            universe_snapshot = store.get_latest_universe_snapshot_sync(limit_rankings=400, selected_only=False) or {}
        except Exception:
            universe_snapshot = {}
        ranking_rows = list(universe_snapshot.get("rankings", []) or [])
        eligible_ranked = [
            _normalize_discovery_symbol(row.get("symbol"))
            for row in ranking_rows
            if _normalize_discovery_symbol(row.get("symbol")) and bool(row.get("passes_filters", True))
        ]
        active_symbols = list(dict.fromkeys(_runtime_symbols(cfg, held_symbols=list(pos_map.keys()))))
        recent_symbols = [
            symbol
            for symbol in tracked_sets.get("recent", [])
            if self._symbol_due(symbol, now_ms, _RECENT_TRACKED_INTERVAL_MS)
        ]
        eligible_slice = self._eligible_rotation_symbols(
            eligible_ranked,
            active_symbols + recent_symbols,
            poll_interval_sec=int(cfg.get("poll_interval_sec", 30) or 30),
        )
        feedback_window_ms = max(_RECENT_ACTIVE_MS, _max_feedback_horizon_min(cfg) * 60 * 1000)
        symbols = []
        for symbol in [*tracked_sets.get("held", []), *active_symbols, *recent_symbols, *eligible_slice]:
            normalized = _normalize_discovery_symbol(symbol)
            if normalized and normalized not in symbols:
                symbols.append(normalized)
        for symbol in symbols:
            self._mark_symbol_evaluated(symbol, now_ms)
            bars_15m = market_data.fetch_bars(symbol, timeframe="15Min", limit=240, mode=credential_mode)
            bars_1m = market_data.fetch_bars(symbol, timeframe="1Min", limit=180, mode=credential_mode)
            bars_1h = None
            bars_1d = None
            if symbol.split("/")[0] in {"BTC", "ETH", "SOL"}:
                bars_1h = market_data.fetch_bars(symbol, timeframe="1H", limit=240, mode=credential_mode)
                bars_1d = market_data.fetch_bars(symbol, timeframe="1D", limit=365, mode=credential_mode)

            diagnostics = _run_coro(
                evaluate_symbol(
                    symbol=symbol,
                    bars_15m=bars_15m,
                    bars_1m=bars_1m,
                    cfg=cfg,
                    now_ms=now_ms,
                    macro_risk_mult=macro_risk_mult,
                    position=pos_map.get(symbol),
                    live_equity=live_equity,
                    total_exposure=total_exposure,
                    bars_1h=bars_1h,
                    bars_1d=bars_1d,
                    return_diagnostics=True,
                )
            )
            signal = dict(diagnostics.get("signal") or {}) if isinstance(diagnostics, dict) else {}
            candidates = list(diagnostics.get("candidates") or []) if isinstance(diagnostics, dict) else []
            best_candidate = dict(diagnostics.get("best_candidate") or {}) if isinstance(diagnostics, dict) else {}
            suppressed_reason = str(diagnostics.get("suppressed_reason", "") or "") if isinstance(diagnostics, dict) else ""
            decision_source = signal or best_candidate
            if not decision_source:
                continue

            strategy_name = str(decision_source.get("strategy", ""))
            side = str(decision_source.get("side", "buy"))
            reason = str(decision_source.get("reason", "") or suppressed_reason)
            notional = float(decision_source.get("notional", 0.0) or 0.0)
            close = float(decision_source.get("close", 0.0) or 0.0)
            meta = dict(decision_source.get("meta") or {})
            tracked_row = store.get_tracked_symbol_sync(symbol, credential_mode)
            meta.update({
                "active_state": str(tracked_row.get("active_state", "idle") or "idle"),
                "active_reason": str(tracked_row.get("active_reason", "") or ""),
                "monitor_tier": str(tracked_row.get("monitor_tier", "eligible") or "eligible"),
            })
            brain_mode = str(meta.get("brain_mode", cfg.get("active_brain", "drl_event_fusion")) or cfg.get("active_brain", "drl_event_fusion"))
            opening_strategy = str(meta.get("opening_strategy", strategy_name) or strategy_name)
            candidate_class = "signal" if signal else "suppressed"
            should_dedupe = not bool(signal)
            if should_dedupe and store.has_recent_candidate_trace_sync(
                symbol,
                strategy_name,
                candidate_class,
                suppressed_reason or reason,
                float(decision_source.get("score", 0.0) or 0.0),
                within_sec=max(anti_spam_sec, cooldown_sec, 180),
                score_epsilon=1.5,
            ):
                continue

            candidate_payload = {
                "signal": signal,
                "best_candidate": best_candidate,
                "candidates": candidates[:6],
                "suppressed_reason": suppressed_reason,
                "oracle": oracle_state,
            }
            store.record_candidate_trace_sync(
                symbol=symbol,
                brain_mode=brain_mode,
                strategy=strategy_name,
                side=side,
                score=float(decision_source.get("score", 0.0) or 0.0),
                submitted=False,
                candidate_class=candidate_class,
                suppressed_reason=suppressed_reason or reason,
                payload=candidate_payload,
            )

            if not signal and best_candidate:
                store.record_brain_decision_trace_sync(
                    symbol=symbol,
                    brain_mode=brain_mode,
                    decision=side,
                    final_score=float(best_candidate.get("score", 0.0) or 0.0),
                    submitted=False,
                    block_reason=suppressed_reason or "strategy_score_gate",
                    payload={
                        "signal": {},
                        "best_candidate": best_candidate,
                        "candidates": candidates[:6],
                        "meta": meta,
                    },
                )
                _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                continue

            if self._allow_signal_toast(symbol, strategy_name, side, anti_spam_sec):
                _send_crypto_toast(
                    "Crypto Candidate",
                    f"{symbol} {side.upper()} candidate via {strategy_name.replace('_', ' ')}.",
                    "signal",
                    duration=3200,
                    extra={
                        "symbol": symbol,
                        "strategy": strategy_name,
                        "signal_side": side,
                        "score": signal.get("score"),
                    },
                )
            _run_coro(store.record_action(
                action_type="signal_detected",
                symbol=symbol,
                side=side,
                notional=notional,
                price=close,
                status="signal",
                reason=reason,
                payload=signal,
            ))

            if strategy_name == "dynamic_dca":
                self._last_dca_ts[symbol] = now_ms

            if not trading_live:
                store.record_brain_decision_trace_sync(
                    symbol=symbol,
                    brain_mode=brain_mode,
                    decision=side,
                    final_score=float(signal.get("score", 0.0) or 0.0),
                    submitted=False,
                    block_reason="trading_mode_offline",
                    payload={"signal": signal, "meta": meta, "candidates": candidates[:6]},
                )
                _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                continue
            if self._halted:
                store.record_brain_decision_trace_sync(
                    symbol=symbol,
                    brain_mode=brain_mode,
                    decision=side,
                    final_score=float(signal.get("score", 0.0) or 0.0),
                    submitted=False,
                    block_reason="risk_halt",
                    payload={"signal": signal, "meta": meta, "candidates": candidates[:6]},
                )
                _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                continue
            if self._on_cooldown(symbol):
                store.record_brain_decision_trace_sync(
                    symbol=symbol,
                    brain_mode=brain_mode,
                    decision=side,
                    final_score=float(signal.get("score", 0.0) or 0.0),
                    submitted=False,
                    block_reason="cooldown",
                    payload={"signal": signal, "meta": meta, "candidates": candidates[:6]},
                )
                _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                continue

            current_positions = market_data.get_crypto_positions(mode=credential_mode)
            pos_map = {str(p.get("symbol", "")): p for p in current_positions}
            open_positions = [p for p in current_positions if abs(float(p.get("qty", 0.0) or 0.0)) > 0]
            pos = pos_map.get(symbol)

            max_open_positions = int(cfg.get("max_open_positions", 3) or 3)
            max_notional = float(cfg.get("max_notional_per_trade", DEFAULT_CRYPTO_CONFIG["max_notional_per_trade"]) or DEFAULT_CRYPTO_CONFIG["max_notional_per_trade"])
            max_exposure = float(cfg.get("max_total_exposure", DEFAULT_CRYPTO_CONFIG["max_total_exposure"]) or DEFAULT_CRYPTO_CONFIG["max_total_exposure"])
            exposure = sum(abs(float(p.get("market_value", 0.0) or 0.0)) for p in open_positions)

            order_payload: Dict[str, Any]
            try:
                if side == "buy":
                    if not pos and len(open_positions) >= max_open_positions:
                        store.record_brain_decision_trace_sync(
                            symbol=symbol,
                            brain_mode=brain_mode,
                            decision=side,
                            final_score=float(signal.get("score", 0.0) or 0.0),
                            submitted=False,
                            block_reason="max_open_positions",
                            payload={"signal": signal, "meta": meta, "candidates": candidates[:6]},
                        )
                        _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                        continue
                        
                    # AI Dynamic Metric Sizing
                    # Base score is 50. High conviction is > 50, reaching 100+.
                    score = float(signal.get("score", 50.0))
                    confidence_multiplier = 1.0
                    if score > 50.0:
                        # Exponential scaling: a score of 80 generates ~1.3x output, up to 2.8x at 100
                        confidence_multiplier = max(1.0, ((score - 50.0) / 25.0) ** 1.5)
                        
                    dynamic_notional = notional * confidence_multiplier
                    
                    desired_notional = max(min_order_notional, max(1.0, dynamic_notional))
                    allowed_notional = min(max_notional, desired_notional)
                    
                    if confidence_multiplier > 1.0 and allowed_notional > min_order_notional:
                        strategy_name += f" (AI Sized: {confidence_multiplier:.1f}x)"

                    if allowed_notional < min_order_notional:
                        _run_coro(store.record_action(
                            action_type="order_blocked",
                            symbol=symbol,
                            side=side,
                            notional=desired_notional,
                            price=close,
                            status="blocked",
                            reason=f"max_notional_per_trade ({max_notional:.2f}) is below min order notional ({min_order_notional:.2f}).",
                            payload={"strategy": strategy_name},
                        ))
                        store.record_brain_decision_trace_sync(
                            symbol=symbol,
                            brain_mode=brain_mode,
                            decision=side,
                            final_score=float(signal.get("score", 0.0) or 0.0),
                            submitted=False,
                            block_reason="min_order_notional",
                            payload={"signal": signal, "meta": meta, "candidates": candidates[:6]},
                        )
                        _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                        continue
                    if exposure + allowed_notional > max_exposure:
                        store.record_brain_decision_trace_sync(
                            symbol=symbol,
                            brain_mode=brain_mode,
                            decision=side,
                            final_score=float(signal.get("score", 0.0) or 0.0),
                            submitted=False,
                            block_reason="max_total_exposure",
                            payload={"signal": signal, "meta": meta, "candidates": candidates[:6]},
                        )
                        _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                        continue
                    order_payload = execution.place_crypto_order(
                        symbol=symbol,
                        side="buy",
                        order_type="market",
                        notional=allowed_notional,
                        time_in_force="gtc",
                        mode=credential_mode,
                        min_notional_usd=min_order_notional,
                    )
                    self._set_cooldown(symbol, cooldown_sec)
                else:
                    qty = float((pos or {}).get("qty", 0.0) or 0.0)
                    if qty <= 0:
                        store.record_brain_decision_trace_sync(
                            symbol=symbol,
                            brain_mode=brain_mode,
                            decision=side,
                            final_score=float(signal.get("score", 0.0) or 0.0),
                            submitted=False,
                            block_reason="no_open_position",
                            payload={"signal": signal, "meta": meta, "candidates": candidates[:6]},
                        )
                        _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                        continue
                    order_payload = execution.close_crypto_position(symbol, mode=credential_mode)
                    self._set_cooldown(symbol, cooldown_sec)

                trace_id = store.record_brain_decision_trace_sync(
                    symbol=symbol,
                    brain_mode=brain_mode,
                    decision=side,
                    final_score=float(signal.get("score", 0.0) or 0.0),
                    submitted=True,
                    block_reason="",
                    payload={"signal": signal, "order": order_payload, "meta": meta, "candidates": candidates[:6]},
                )
                _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)

                _run_coro(store.record_action(
                    action_type="order_submitted",
                    symbol=symbol,
                    side=side,
                    qty=order_payload.get("qty"),
                    notional=order_payload.get("notional"),
                    price=close,
                    status="success",
                    reason=f"{strategy_name} execution",
                    payload=order_payload,
                ))
                if side == "buy":
                    fill_price = float(order_payload.get("filled_avg_price", 0.0) or close or 0.0)
                    fill_notional = float(order_payload.get("notional", 0.0) or allowed_notional or notional or 0.0)
                    store.record_live_experience_sync(
                        symbol=symbol,
                        side="buy",
                        entry_ts=now_ms,
                        entry_price=fill_price,
                        notional=fill_notional,
                        gemini_score=macro_risk_mult,
                        gemini_regime=gemini_regime,
                        strategy_used=opening_strategy,
                        narrative_label=str(meta.get("event_bias", "neutral") or "neutral"),
                        rsi_15m_at_entry=float(meta.get("rsi14", 50.0) or 50.0),
                        rsi_1m_at_entry=float(meta.get("rsi_1m", 50.0) or 50.0),
                        slm_confidence=float(meta.get("slm_confidence", 0.0) or 0.0),
                        brain_mode=brain_mode,
                        event_score_at_entry=float(meta.get("event_net_score", 0.0) or 0.0),
                        event_bias_at_entry=str(meta.get("event_bias", "neutral") or "neutral"),
                        top_event_type_at_entry=str(meta.get("top_event_type", "") or ""),
                        decision_trace_id=trace_id,
                        news_context_state_at_entry=str(meta.get("news_context_state", "unavailable") or "unavailable"),
                        news_context_complete_at_entry=bool(meta.get("news_context_complete", False)),
                        monitor_tier_at_entry=str(meta.get("monitor_tier", "eligible") or "eligible"),
                        active_reason_at_entry=str(meta.get("active_reason", "") or ""),
                    )
                else:
                    exit_price = float(order_payload.get("filled_avg_price", 0.0) or close or float((pos or {}).get("current_price", 0.0) or 0.0))
                    _close_open_live_experience(symbol, now_ms, exit_price, f"{strategy_name} execution")
                _send_crypto_toast(
                    "Crypto Order Submitted",
                    f"{symbol} {side.upper()} order submitted ({strategy_name.replace('_', ' ')}).",
                    "success",
                    duration=3200,
                    extra={"symbol": symbol, "order": order_payload},
                )
            except Exception as e:
                err = str(e)
                _run_coro(store.record_action(
                    action_type="order_rejected",
                    symbol=symbol,
                    side=side,
                    notional=notional,
                    price=close,
                    status="error",
                    reason=err,
                    payload={"signal": signal},
                ))
                store.record_brain_decision_trace_sync(
                    symbol=symbol,
                    brain_mode=brain_mode,
                    decision=side,
                    final_score=float(signal.get("score", 0.0) or 0.0),
                    submitted=False,
                    block_reason="order_rejected",
                    payload={"signal": signal, "error": err, "meta": meta, "candidates": candidates[:6]},
                )
                _touch_tracked_decision_state(symbol, credential_mode, now_ms, feedback_window_ms)
                _send_crypto_toast("Crypto Order Rejected", f"{symbol}: {err}", "error", duration=5200)

        # Synthetic exits managed by bot loop.
        if bool(cfg.get("synthetic_exits", {}).get("enabled", True)) and trading_live and not self._halted:
            tp_pct = float(cfg.get("synthetic_exits", {}).get("take_profit_pct", 3.0) or 3.0)
            sl_pct = float(cfg.get("synthetic_exits", {}).get("stop_loss_pct", 1.8) or 1.8)
            current_positions = market_data.get_crypto_positions(mode=credential_mode)
            for p in current_positions:
                symbol = str(p.get("symbol", "") or "")
                if not symbol:
                    continue
                if self._on_cooldown(symbol):
                    continue
                qty = float(p.get("qty", 0.0) or 0.0)
                entry = float(p.get("avg_entry_price", 0.0) or 0.0)
                current = float(p.get("current_price", 0.0) or 0.0)
                if qty <= 0 or entry <= 0 or current <= 0:
                    continue
                pnl_pct = ((current - entry) / entry) * 100.0
                exit_reason = None
                if pnl_pct >= tp_pct:
                    exit_reason = f"Synthetic take-profit hit ({pnl_pct:.2f}%)."
                elif pnl_pct <= -sl_pct:
                    exit_reason = f"Synthetic stop-loss hit ({pnl_pct:.2f}%)."
                if not exit_reason:
                    continue
                try:
                    order = execution.close_crypto_position(symbol, mode=credential_mode)
                    self._set_cooldown(symbol, cooldown_sec)
                    _run_coro(store.record_action(
                        action_type="synthetic_exit",
                        symbol=symbol,
                        side="sell",
                        qty=qty,
                        price=current,
                        status="success",
                        reason=exit_reason,
                        payload=order,
                    ))
                    _close_open_live_experience(symbol, now_ms, current, exit_reason)
                    _send_crypto_toast("Crypto Exit", f"{symbol}: {exit_reason}", "warning", duration=3800)
                except Exception as e:
                    _run_coro(store.record_action(
                        action_type="synthetic_exit_failed",
                        symbol=symbol,
                        side="sell",
                        qty=qty,
                        price=current,
                        status="error",
                        reason=str(e),
                    ))

        feedback_cfg = dict(cfg.get("decision_feedback", {}) or {})
        feedback_interval_sec = max(60, int(feedback_cfg.get("eval_interval_sec", 600) or 600))
        if (now_ms - int(self._last_feedback_eval_ms or 0)) >= (feedback_interval_sec * 1000):
            feedback_result = evaluate_pending_decision_outcomes(
                mode=credential_mode,
                feedback_cfg=feedback_cfg,
                now_ms=now_ms,
            )
            self._last_feedback_eval_ms = now_ms
            if int(feedback_result.get("recorded", 0) or 0) > 0:
                refresh_symbol_policy_overlays(force=True)
                ensure_brain_self_score_snapshot(force=True)

        status_positions = market_data.get_cached_crypto_positions(max_age_sec=900) or current_positions
        open_positions_count = len(
            [p for p in status_positions if abs(float(p.get("qty", 0.0) or 0.0)) > 0]
        )

        with self._lock:
            self._iterations += 1
            self._last_cycle_ms = now_ms
            self._last_status = {
                "symbols": symbols,
                "active_symbols": active_symbols,
                "recent_symbols": recent_symbols,
                "eligible_slice": eligible_slice,
                "account_equity": float(account.get("equity", 0.0) or 0.0),
                "open_positions": open_positions_count,
                "trading_mode": "live" if trading_live else "offline",
                "macro_risk_mult": macro_risk_mult,
            }
        _run_coro(store.update_runtime_state(
            running=True,
            iterations=self._iterations,
            last_heartbeat=now_ms,
            halted=self._halted,
            halted_reason=self._halted_reason,
        ))

    def flatten_all(self) -> Dict[str, Any]:
        execution = _execution_module()
        result = execution.close_all_crypto_positions(mode=_current_credential_mode())
        _run_coro(store.record_action(
            action_type="emergency_flatten",
            status="success",
            reason="Manual flatten requested.",
            payload=result,
        ))
        _send_crypto_toast("Crypto Flatten", f"Closed {int(result.get('closed', 0))} crypto positions.", "warning", duration=4200)
        return result


_RUNTIME = CryptoBotRuntime()


def get_bot_status_snapshot() -> Dict[str, Any]:
    return {
        "runtime": _RUNTIME.status(),
        "persisted": store.get_runtime_state_cached_sync() or {
            "running": False,
            "started_at": None,
            "last_heartbeat": None,
            "iterations": 0,
            "last_error": None,
            "halted": False,
            "halted_reason": None,
            "risk_state": "normal",
            "risk_source": "",
            "risk_reason": "",
            "effective_risk_profile": {},
            "behavior_state": {},
            "component_calibration": {},
            "day_start_equity_paper": None,
            "day_start_equity_live": None,
            "day_start_ts": None,
            "updated_at": None,
        },
    }


async def get_bot_status() -> Dict[str, Any]:
    snapshot = get_bot_status_snapshot()
    persisted = snapshot["persisted"]
    if not persisted:
        try:
            persisted = await asyncio.wait_for(store.get_runtime_state(), timeout=0.35)
        except Exception:
            persisted = snapshot["persisted"]
    snapshot["persisted"] = persisted
    return snapshot


def start_bot() -> Dict[str, Any]:
    return _RUNTIME.start()


def stop_bot() -> Dict[str, Any]:
    return _RUNTIME.stop()


def flatten_all_positions() -> Dict[str, Any]:
    return _RUNTIME.flatten_all()


async def list_recent_actions(limit: int = 200) -> List[Dict[str, Any]]:
    return await store.list_actions(limit=limit)


async def clear_recent_actions() -> int:
    return await store.clear_actions()


def current_crypto_config(resolve_symbols: bool = True) -> Dict[str, Any]:
    return _get_crypto_cfg(resolve_symbols=resolve_symbols)


def save_crypto_config(cfg_updates: Dict[str, Any]) -> Dict[str, Any]:
    current = get_config().get("stocks", {}).get("crypto", {})
    if not isinstance(current, dict):
        current = {}
    merged = _deep_merge(_deep_merge(DEFAULT_CRYPTO_CONFIG, current), cfg_updates or {})
    saved_symbols = [
        _normalize_discovery_symbol(symbol)
        for symbol in (merged.get("scanner_watchlist", []) or [])
        if _normalize_discovery_symbol(symbol)
    ]
    try:
        store.replace_saved_tracked_symbols_sync(_normalize_account_mode(merged.get("account_mode", "paper")), saved_symbols)
    except Exception:
        pass
    merged["scanner_watchlist"] = saved_symbols[:30]
    return _persist_crypto_cfg(merged)


def list_assets(limit: int = 60) -> List[Dict[str, Any]]:
    market_data = _market_data_module()
    return market_data.list_crypto_assets(limit=limit, mode=_current_credential_mode())


def get_positions() -> List[Dict[str, Any]]:
    market_data = _market_data_module()
    return market_data.get_crypto_positions(mode=_current_credential_mode())


def get_account() -> Dict[str, Any]:
    market_data = _market_data_module()
    return market_data.get_account_summary(mode=_current_credential_mode())
