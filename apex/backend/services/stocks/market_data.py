from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from types import MethodType

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from integrations.alpaca.runtime_config import get_alpaca_credentials


_HTTP_CONNECT_TIMEOUT_SEC = 10.0
_HTTP_READ_TIMEOUT_SEC = 30.0


def _apply_http_timeouts(client: Any) -> Any:
    """
    Alpaca SDK does not set request timeouts by default.
    Patch the underlying session to enforce sane connect/read timeouts.
    """
    session = getattr(client, "_session", None)
    if session is None:
        return client
    original_request = getattr(session, "_apex_original_request", None)
    if original_request is None:
        original_request = session.request

    def _request_with_timeout(self, method, url, **kwargs):
        kwargs["timeout"] = (_HTTP_CONNECT_TIMEOUT_SEC, _HTTP_READ_TIMEOUT_SEC)
        return original_request(method, url, **kwargs)

    session._apex_original_request = original_request  # type: ignore[attr-defined]
    session.request = MethodType(_request_with_timeout, session)
    return client


def get_trading_client(mode: Optional[str] = None) -> TradingClient:
    api_key, secret_key, paper = get_alpaca_credentials(mode=mode)
    if not api_key or not secret_key:
        raise RuntimeError("Alpaca API keys not configured for selected trading mode.")
    return _apply_http_timeouts(TradingClient(api_key, secret_key, paper=paper))


def get_stock_data_client(mode: Optional[str] = None) -> StockHistoricalDataClient:
    api_key, secret_key, _ = get_alpaca_credentials(mode=mode)
    if not api_key or not secret_key:
        raise RuntimeError("Alpaca API keys not configured for selected trading mode.")
    return _apply_http_timeouts(StockHistoricalDataClient(api_key=api_key, secret_key=secret_key))


def _canonical_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    return raw


def _timeframe_from_string(raw: str) -> TimeFrame:
    value = str(raw or "15Min").strip().lower()
    if value in {"15m", "15min"}:
        return TimeFrame(15, TimeFrameUnit.Minute)
    if value in {"1h", "1hour", "60m", "60min"}:
        return TimeFrame(1, TimeFrameUnit.Hour)
    if value in {"4h", "4hour"}:
        return TimeFrame(4, TimeFrameUnit.Hour)
    if value in {"1d", "1day", "day"}:
        return TimeFrame(1, TimeFrameUnit.Day)
    # Default to 15m for Swing Strategy
    return TimeFrame(15, TimeFrameUnit.Minute)


def list_stock_assets(limit: int = 60, mode: Optional[str] = None) -> List[Dict[str, Any]]:
    client = get_trading_client(mode=mode)
    req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    assets = client.get_all_assets(req) or []
    out: List[Dict[str, Any]] = []
    for a in assets:
        symbol = _canonical_symbol(str(getattr(a, "symbol", "") or ""))
        if not symbol:
            continue
        status_raw = getattr(a, "status", "active")
        status = str(getattr(status_raw, "value", status_raw) or "active").lower()
        out.append(
            {
                "symbol": symbol,
                "name": str(getattr(a, "name", "") or symbol),
                "tradable": bool(getattr(a, "tradable", True)),
                "status": status,
                "marginable": bool(getattr(a, "marginable", False)),
                "shortable": bool(getattr(a, "shortable", False)),
                "fractionable": bool(getattr(a, "fractionable", True)),
            }
        )
    out.sort(key=lambda x: x["symbol"])
    return out[: max(1, min(int(limit), 1000))]


def get_stock_positions(mode: Optional[str] = None) -> List[Dict[str, Any]]:
    client = get_trading_client(mode=mode)
    rows = client.get_all_positions() or []
    out: List[Dict[str, Any]] = []
    for p in rows:
        asset_class = str(getattr(p, "asset_class", "") or "").lower()
        symbol_raw = str(getattr(p, "symbol", "") or "")
        symbol = _canonical_symbol(symbol_raw)
        
        # We only want Stocks, not Crypto
        if asset_class and "crypto" in asset_class:
            continue
            
        out.append(
            {
                "symbol": symbol,
                "qty": float(getattr(p, "qty", 0.0) or 0.0),
                "avg_entry_price": float(getattr(p, "avg_entry_price", 0.0) or 0.0),
                "current_price": float(getattr(p, "current_price", 0.0) or 0.0),
                "market_value": float(getattr(p, "market_value", 0.0) or 0.0),
                "unrealized_pl": float(getattr(p, "unrealized_pl", 0.0) or 0.0),
                "unrealized_plpc": float(getattr(p, "unrealized_plpc", 0.0) or 0.0),
                "side": str(getattr(p, "side", "long") or "long"),
                "asset_class": asset_class or "us_equity",
            }
        )
    out.sort(key=lambda x: x["symbol"])
    return out


def get_latest_quote(symbol: str, mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    sym = _canonical_symbol(symbol)
    if not sym:
        return None
    client = get_stock_data_client(mode=mode)
    req = StockLatestQuoteRequest(symbol_or_symbols=sym)
    resp = client.get_stock_latest_quote(req)
    data = getattr(resp, "data", None)
    quote_obj = None
    if isinstance(data, dict):
        quote_obj = data.get(sym) or next(iter(data.values()), None)
    elif isinstance(resp, dict):
        quote_obj = resp.get(sym) or next(iter(resp.values()), None)
    if quote_obj is None:
        return None
    ask = float(getattr(quote_obj, "ask_price", 0.0) or 0.0)
    bid = float(getattr(quote_obj, "bid_price", 0.0) or 0.0)
    return {
        "symbol": sym,
        "ask_price": ask,
        "bid_price": bid,
        "mid_price": (ask + bid) / 2.0 if ask and bid else ask or bid or 0.0,
        "timestamp": getattr(quote_obj, "timestamp", None),
    }


def fetch_bars(symbol: str, timeframe: str = "15Min", limit: int = 300, mode: Optional[str] = None) -> pd.DataFrame:
    sym = _canonical_symbol(symbol)
    if not sym:
        return pd.DataFrame()

    tf = _timeframe_from_string(timeframe)
    capped = max(20, min(int(limit), 2000))

    now = datetime.now(timezone.utc)
    if tf.unit_value == TimeFrameUnit.Day:
        lookback = timedelta(days=max(3, int(capped * 1.6)))
    elif tf.unit_value == TimeFrameUnit.Hour:
        lookback = timedelta(hours=max(12, int(capped * tf.amount_value * 1.5)))
    else:
        lookback = timedelta(minutes=max(60, int(capped * tf.amount_value * 2.0))) # Extra buffer for weekend gaps

    req = StockBarsRequest(
        symbol_or_symbols=sym,
        timeframe=tf,
        start=now - lookback,
        end=now,
        limit=capped,
    )
    client = get_stock_data_client(mode=mode)
    resp = client.get_stock_bars(req)

    rows: List[Dict[str, Any]] = []
    data = getattr(resp, "data", None)
    bars = None
    if isinstance(data, dict):
        bars = data.get(sym) or next(iter(data.values()), None)
    elif isinstance(resp, dict):
        bars = resp.get(sym) or next(iter(resp.values()), None)
    else:
        bars = getattr(resp, sym, None)
    if bars is None:
        bars = []

    for b in bars:
        ts = getattr(b, "timestamp", None)
        rows.append(
            {
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "open": float(getattr(b, "open", 0.0) or 0.0),
                "high": float(getattr(b, "high", 0.0) or 0.0),
                "low": float(getattr(b, "low", 0.0) or 0.0),
                "close": float(getattr(b, "close", 0.0) or 0.0),
                "volume": float(getattr(b, "volume", 0.0) or 0.0),
                "trade_count": int(getattr(b, "trade_count", 0) or 0),
                "vwap": float(getattr(b, "vwap", 0.0) or 0.0),
                "symbol": sym,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df
