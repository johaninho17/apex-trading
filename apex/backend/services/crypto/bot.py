import asyncio
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config_manager import get_config, update_config
from core.state import state as global_state
from services import notification_manager

from . import execution, market_data, store
from .strategy import evaluate_symbol

DEFAULT_CRYPTO_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "trading_mode": "offline",  # live | offline
    "account_mode": "paper",  # paper | live
    "poll_interval_sec": 30,
    "timeframe": "1Min",
    "symbols": ["BTC/USD", "ETH/USD"],
    "auto_discover_pairs": False,
    "auto_discover_limit": 20,
    "auto_discover_quote": "USD",
    "auto_discover_tradable_only": True,
    "min_order_notional_usd": 10.0,
    "max_open_positions": 3,
    "max_notional_per_trade": 15.0,
    "max_total_exposure": 250.0,
    "max_daily_drawdown_pct": 4.0,
    "cooldown_sec": 90,
    "anti_spam_sec": 30,
    "short_term": {
        "mean_reversion_enabled": True,
        "breakout_enabled": True,
        "base_notional": 6.0,
        "breakout_notional": 7.5,
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
        "crossover_notional": 8.0,
        "dca_enabled": True,
        "dca_notional": 4.0,
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


def _get_crypto_cfg() -> Dict[str, Any]:
    cfg = get_config().get("stocks", {}).get("crypto", {})
    if not isinstance(cfg, dict):
        cfg = {}
    merged = _deep_merge(DEFAULT_CRYPTO_CONFIG, cfg)
    manual_symbols = [str(s).strip().upper() for s in merged.get("symbols", []) if str(s).strip()]
    merged["trading_mode"] = "live" if str(merged.get("trading_mode", "offline")).lower() == "live" else "offline"
    merged["account_mode"] = _normalize_account_mode(merged.get("account_mode", "paper"))
    credential_mode = merged["account_mode"]
    merged["auto_discover_pairs"] = bool(merged.get("auto_discover_pairs", False))
    merged["auto_discover_limit"] = max(1, min(300, int(merged.get("auto_discover_limit", 20) or 20)))
    merged["auto_discover_quote"] = str(merged.get("auto_discover_quote", "USD") or "USD").strip().upper() or "USD"
    merged["auto_discover_tradable_only"] = bool(merged.get("auto_discover_tradable_only", True))
    merged["min_order_notional_usd"] = max(1.0, float(merged.get("min_order_notional_usd", 10.0) or 10.0))

    discovered_symbols: List[str] = []
    if merged["auto_discover_pairs"]:
        discovered_symbols = _discover_symbols(
            quote=merged["auto_discover_quote"],
            limit=merged["auto_discover_limit"],
            tradable_only=merged["auto_discover_tradable_only"],
            mode=credential_mode,
        )

    merged["symbols"] = discovered_symbols or manual_symbols or list(DEFAULT_CRYPTO_CONFIG["symbols"])
    merged["poll_interval_sec"] = max(5, int(merged.get("poll_interval_sec", 30)))
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

    def _cycle(self, cfg: Dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        trading_live = str(cfg.get("trading_mode", "offline")).lower() == "live"
        credential_mode = _normalize_account_mode(cfg.get("account_mode", "paper"))
        account = market_data.get_account_summary(mode=credential_mode)
        positions = market_data.get_crypto_positions(mode=credential_mode)
        pos_map = {str(p.get("symbol", "")): p for p in positions}

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

        symbols = [str(s).strip().upper() for s in cfg.get("symbols", []) if str(s).strip()]
        for symbol in symbols:
            bars = market_data.fetch_bars(
                symbol,
                timeframe=str(cfg.get("timeframe", "1Min")),
                limit=360,
                mode=credential_mode,
            )
            signal = evaluate_symbol(
                symbol=symbol,
                bars_df=bars,
                cfg=cfg,
                now_ms=now_ms,
                last_dca_ts=self._last_dca_ts.get(symbol),
            )
            if not signal:
                continue

            strategy_name = str(signal.get("strategy", ""))
            side = str(signal.get("side", "buy"))
            reason = str(signal.get("reason", ""))
            notional = float(signal.get("notional", 0.0) or 0.0)
            close = float(signal.get("close", 0.0) or 0.0)

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

            # Signal-only mode still emits alerts, but no execution.
            if not trading_live:
                continue
            if self._halted:
                continue
            if self._on_cooldown(symbol):
                continue

            current_positions = market_data.get_crypto_positions(mode=credential_mode)
            pos_map = {str(p.get("symbol", "")): p for p in current_positions}
            open_positions = [p for p in current_positions if abs(float(p.get("qty", 0.0) or 0.0)) > 0]
            pos = pos_map.get(symbol)

            max_open_positions = int(cfg.get("max_open_positions", 3) or 3)
            max_notional = float(cfg.get("max_notional_per_trade", 15.0) or 15.0)
            max_exposure = float(cfg.get("max_total_exposure", 250.0) or 250.0)
            exposure = sum(abs(float(p.get("market_value", 0.0) or 0.0)) for p in open_positions)

            order_payload: Dict[str, Any]
            try:
                if side == "buy":
                    if not pos and len(open_positions) >= max_open_positions:
                        continue
                    desired_notional = max(min_order_notional, max(1.0, notional))
                    allowed_notional = min(max_notional, desired_notional)
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
                        continue
                    if exposure + allowed_notional > max_exposure:
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
                        continue
                    order_payload = execution.close_crypto_position(symbol, mode=credential_mode)
                    self._set_cooldown(symbol, cooldown_sec)

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

        with self._lock:
            self._iterations += 1
            self._last_cycle_ms = now_ms
            self._last_status = {
                "symbols": symbols,
                "account_equity": float(account.get("equity", 0.0) or 0.0),
                "open_positions": len([p for p in market_data.get_crypto_positions(mode=credential_mode) if abs(float(p.get("qty", 0.0) or 0.0)) > 0]),
                "trading_mode": "live" if trading_live else "offline",
            }
        _run_coro(store.update_runtime_state(
            running=True,
            iterations=self._iterations,
            last_heartbeat=now_ms,
            halted=self._halted,
            halted_reason=self._halted_reason,
        ))

    def flatten_all(self) -> Dict[str, Any]:
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


async def get_bot_status() -> Dict[str, Any]:
    runtime = _RUNTIME.status()
    persisted = await store.get_runtime_state()
    return {"runtime": runtime, "persisted": persisted}


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


def current_crypto_config() -> Dict[str, Any]:
    return _get_crypto_cfg()


def save_crypto_config(cfg_updates: Dict[str, Any]) -> Dict[str, Any]:
    current = _get_crypto_cfg()
    merged = _deep_merge(current, cfg_updates or {})
    return _persist_crypto_cfg(merged)


def list_assets(limit: int = 60) -> List[Dict[str, Any]]:
    return market_data.list_crypto_assets(limit=limit, mode=_current_credential_mode())


def get_positions() -> List[Dict[str, Any]]:
    return market_data.get_crypto_positions(mode=_current_credential_mode())


def get_account() -> Dict[str, Any]:
    return market_data.get_account_summary(mode=_current_credential_mode())
