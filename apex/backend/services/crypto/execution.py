from typing import Any, Dict, Optional
from uuid import uuid4

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopLimitOrderRequest

from .market_data import get_latest_quote, get_trading_client, get_crypto_positions

_TIF_MAP = {
    "gtc": TimeInForce.GTC,
    "ioc": TimeInForce.IOC,
    "day": TimeInForce.DAY,
}


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

    min_notional = max(0.0, float(min_notional_usd or 0.0))
    est_notional: Optional[float] = None
    if notional is not None:
        est_notional = float(notional)
    elif qty is not None:
        try:
            q = get_latest_quote(sym, mode=mode)
            mid = float((q or {}).get("mid_price", 0.0) or 0.0)
            if mid > 0:
                est_notional = float(qty) * mid
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
        base_kwargs["notional"] = float(notional)

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
    client = get_trading_client(mode=mode)
    result = client.close_position(sym)
    return {
        "symbol": sym,
        "status": str(getattr(result, "status", "accepted")),
    }


def close_all_crypto_positions(mode: Optional[str] = None) -> Dict[str, Any]:
    client = get_trading_client(mode=mode)
    positions = get_crypto_positions(mode=mode)
    closed = 0
    failures = []
    for p in positions:
        sym = str(p.get("symbol", "") or "")
        if not sym:
            continue
        try:
            client.close_position(sym)
            closed += 1
        except Exception as e:
            failures.append({"symbol": sym, "error": str(e)})
    return {"closed": closed, "failures": failures}
