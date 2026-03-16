from typing import Any, Dict, Optional
from uuid import uuid4

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopLimitOrderRequest

from .market_data import get_latest_quote, get_trading_client, get_crypto_positions
from integrations.kraken import kraken_client

_TIF_MAP = {
    "gtc": TimeInForce.GTC,
    "ioc": TimeInForce.IOC,
    "day": TimeInForce.DAY,
}


def _alpaca_pos_sym(symbol: str) -> str:
    """Alpaca position/close API requires BTCUSD format (no slash)."""
    return str(symbol or "").strip().upper().replace("/", "")


def await_crypto_fill(order_id: str, mode: Optional[str] = None, max_wait_sec: int = 4) -> tuple[bool, str, float]:
    """Poll broker for up to max_wait_sec to confirm order filled."""
    import time
    from alpaca.trading.enums import OrderStatus
    try:
        from .bot import current_crypto_config
        cfg = current_crypto_config()
        if str(cfg.get("active_exchange", "alpaca")).lower() == "kraken":
            return True, "filled", 0.0  # Kraken mocks immediate completion for now
    except Exception:
        pass
        
    client = get_trading_client(mode=mode)
    start = time.time()
    while time.time() - start < max_wait_sec:
        try:
            o = client.get_order_by_id(order_id)
            os_raw = str(getattr(o, "status", "")).lower()
            if os_raw == "filled":
                return True, os_raw, float(getattr(o, "filled_avg_price", 0.0) or 0.0)
            if os_raw in ("canceled", "rejected", "expired", "replaced"):
                return False, os_raw, 0.0
        except Exception:
            pass
        time.sleep(1.0)
    return False, "timeout", 0.0


def _side_enum(side: str) -> OrderSide:
    raw = str(side or "").strip().lower()
    if raw == "sell":
        return OrderSide.SELL
    return OrderSide.BUY


def place_crypto_order(
    symbol: str,
    side: str,
    order_type: str = "market",
    qty: Optional[float] = None,
    notional: Optional[float] = None,
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    time_in_force: str = "gtc",
    client_order_id: Optional[str] = None,
    mode: Optional[str] = None,
    min_notional_usd: float = 10.0,
) -> Dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    if "/" not in sym:
        raise ValueError("symbol must be a crypto pair like BTC/USD")

    side_raw = str(side or "").strip().lower()
    if side_raw not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'")

    otype = str(order_type or "market").strip().lower()
    if otype not in {"market", "limit", "stop_limit"}:
        raise ValueError("order_type must be market, limit, or stop_limit")

    if (qty is None and notional is None) or (qty is not None and notional is not None):
        raise ValueError("Provide exactly one of qty or notional")
    if qty is not None and float(qty) <= 0:
        raise ValueError("qty must be > 0")
    if notional is not None and float(notional) <= 0:
        raise ValueError("notional must be > 0")

    try:
        from .bot import current_crypto_config
        cfg = current_crypto_config()
        if str(cfg.get("active_exchange", "alpaca")).lower() == "kraken":
            return kraken_client.place_crypto_order(
                symbol=sym,
                side=side_raw,
                notional=notional,
                qty=qty,
                limit_price=limit_price
            )
    except Exception as e:
        print(f"Kraken order redirect failed: {e}")
        pass

    min_notional = max(0.0, float(min_notional_usd or 0.0))
    est_notional: Optional[float] = None
    if notional is not None:
        est_notional = round(float(notional), 2)
    elif qty is not None:
        try:
            q = get_latest_quote(sym, mode=mode)
            mid = float((q or {}).get("mid_price", 0.0) or 0.0)
            if mid > 0:
                est_notional = round(float(qty) * mid, 2)
        except Exception:
            est_notional = None
    if est_notional is not None and est_notional < min_notional:
        raise ValueError(
            f"Order notional ${est_notional:.2f} is below minimum ${min_notional:.2f}. "
            f"Increase size or use at least ${min_notional:.2f} notional."
        )

    tif = _TIF_MAP.get(str(time_in_force or "gtc").strip().lower(), TimeInForce.GTC)
    cid = client_order_id or f"apex-crypto-{uuid4().hex[:20]}"
    side_enum = _side_enum(side_raw)
    trading = get_trading_client(mode=mode)

    base_kwargs = {
        "symbol": sym,
        "side": side_enum,
        "time_in_force": tif,
        "client_order_id": cid,
    }
    if qty is not None:
        base_kwargs["qty"] = float(qty)
    if notional is not None:
        base_kwargs["notional"] = round(float(notional), 2)

    if otype == "market":
        req = MarketOrderRequest(**base_kwargs)
    elif otype == "limit":
        if limit_price is None or float(limit_price) <= 0:
            raise ValueError("limit_price must be > 0 for limit orders")
        req = LimitOrderRequest(limit_price=float(limit_price), **base_kwargs)
    else:
        if limit_price is None or float(limit_price) <= 0:
            raise ValueError("limit_price must be > 0 for stop_limit orders")
        if stop_price is None or float(stop_price) <= 0:
            raise ValueError("stop_price must be > 0 for stop_limit orders")
        req = StopLimitOrderRequest(
            limit_price=float(limit_price),
            stop_price=float(stop_price),
            **base_kwargs,
        )

    order = trading.submit_order(req)
    
    # Phase 0.5: Reset blacklist strikes on a successful new order submission
    try:
        from . import store
        strike_key = f"blacklist_strikes:{mode or 'paper'}:{sym}"
        store.save_signal_state_sync(strike_key, 0)
    except Exception:
        pass
        
    return {
        "id": str(getattr(order, "id", "")),
        "client_order_id": str(getattr(order, "client_order_id", cid)),
        "symbol": str(getattr(order, "symbol", sym)),
        "side": str(getattr(order, "side", side_raw)),
        "status": str(getattr(order, "status", "")),
        "order_type": str(getattr(order, "order_type", otype)),
        "qty": float(getattr(order, "qty", qty or 0.0) or 0.0),
        "notional": float(getattr(order, "notional", notional or 0.0) or 0.0),
        "filled_qty": float(getattr(order, "filled_qty", 0.0) or 0.0),
        "filled_avg_price": float(getattr(order, "filled_avg_price", 0.0) or 0.0),
    }


def close_crypto_position(symbol: str, mode: Optional[str] = None) -> Dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol is required")
        
    try:
        from .bot import current_crypto_config
        cfg = current_crypto_config()
        if str(cfg.get("active_exchange", "alpaca")).lower() == "kraken":
            # Kraken doesn't have a single "close position" API, it must be synthesized by querying positions
            positions = kraken_client.get_crypto_positions()
            for p in positions:
                if p.get("symbol") == sym:
                    qty = p.get("qty", 0.0)
                    side = "sell" if p.get("side", "long") == "long" else "buy"
                    kraken_client.place_crypto_order(symbol=sym, side=side, qty=qty)
                    return {"symbol": sym, "status": "accepted"}
            return {"symbol": sym, "status": "not_found"}
    except Exception:
        pass
        
    client = get_trading_client(mode=mode)
    result = client.close_position(_alpaca_pos_sym(sym))
    return {
        "symbol": sym,
        "status": str(getattr(result, "status", "accepted")),
    }


def close_all_crypto_positions(mode: Optional[str] = None) -> Dict[str, Any]:
    try:
        from .bot import current_crypto_config
        cfg = current_crypto_config()
        if str(cfg.get("active_exchange", "alpaca")).lower() == "kraken":
            positions = kraken_client.get_crypto_positions()
            closed = 0
            failures = []
            for p in positions:
                sym = str(p.get("symbol", "") or "")
                if not sym:
                    continue
                try:
                    qty = p.get("qty", 0.0)
                    side = "sell" if p.get("side", "long") == "long" else "buy"
                    kraken_client.place_crypto_order(symbol=sym, side=side, qty=qty)
                    closed += 1
                except Exception as e:
                    failures.append({"symbol": sym, "error": str(e)})
            return {"closed": closed, "failures": failures}
    except Exception:
        pass

    client = get_trading_client(mode=mode)
    positions = get_crypto_positions(mode=mode)
    closed = 0
    failures = []
    for p in positions:
        sym = str(p.get("symbol", "") or "")
        if not sym:
            continue
        try:
            client.close_position(_alpaca_pos_sym(sym))
            closed += 1
        except Exception as e:
            failures.append({"symbol": sym, "error": str(e)})
    return {"closed": closed, "failures": failures}


def cancel_stale_crypto_orders(ttl_seconds: int = 60, mode: Optional[str] = None) -> Dict[str, Any]:
    """
    Sweeps for PENDING_NEW or OPEN crypto orders and cancels them if they
    have been sitting in the queue longer than `ttl_seconds`.
    """
    try:
        from .bot import current_crypto_config
        cfg = current_crypto_config()
        if str(cfg.get("active_exchange", "alpaca")).lower() == "kraken":
            # Kraken does not currently have order gc implemented
            return {"cancelled": 0, "failures": []}
    except Exception:
        pass

    import time
    from datetime import datetime, timezone
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    client = get_trading_client(mode=mode)
    
    req = GetOrdersRequest(
        status=QueryOrderStatus.OPEN,
        limit=100,
        nested=False
    )
    
    open_orders = client.get_orders(req) or []
    
    now = datetime.now(timezone.utc)
    cancelled = 0
    failures = []
    
    for order in open_orders:
        asset_class = str(getattr(order, "asset_class", "") or "").lower()
        if asset_class and "crypto" not in asset_class:
            continue
            
        created_at = getattr(order, "created_at", None)
        if not created_at:
            continue
            
        elapsed = (now - created_at).total_seconds()
        
        if elapsed > ttl_seconds:
            order_id = str(getattr(order, "id", ""))
            if not order_id:
                continue
                
            try:
                client.cancel_order_by_id(order_id)
                cancelled += 1
                
                # Phase 0.5: Track strikes for stale order cancellations
                try:
                    from . import store
                    sym_key = asset_class.replace("crypto", "").strip() or "UNKNOWN"
                    # Try to extra symbol from the order object more reliably if possible
                    order_sym = str(getattr(order, "symbol", sym_key))
                    strike_key = f"blacklist_strikes:{mode}:{order_sym}"
                    
                    state = store.get_signal_state_sync(strike_key)
                    current_strikes = state.get(strike_key, 0)
                    new_strikes = current_strikes + 1
                    store.save_signal_state_sync(strike_key, new_strikes)
                    
                    if new_strikes >= 3:
                        ban_key = f"blacklist_until:{mode}:{order_sym}"
                        ban_until_ms = int(time.time() * 1000) + (24 * 60 * 60 * 1000) # 24 hours
                        store.save_signal_state_sync(ban_key, ban_until_ms)
                        from .bot import _send_crypto_toast
                        _send_crypto_toast("Blacklist Enforced", f"{order_sym} hit 3 stale order strikes. Banned for 24 hours.", "warning")
                        # Reset strikes after banning so it starts fresh after the 24 hours
                        store.save_signal_state_sync(strike_key, 0)
                except Exception as e:
                    pass # Ignore tracking errors to not disrupt GC
                    
            except Exception as e:
                failures.append({"order_id": order_id, "error": str(e)})

    return {"cancelled": cancelled, "failures": failures}

