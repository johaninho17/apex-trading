import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from types import MethodType


import pandas as pd
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, CryptoLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from integrations.alpaca.runtime_config import get_alpaca_credentials
from integrations.kraken import kraken_client
from . import store


_HTTP_CONNECT_TIMEOUT_SEC = 15.0   # 15s connect timeout — trading API is slower than data API
_HTTP_READ_TIMEOUT_SEC = 20.0

# Position cache — survives transient connect timeouts so the bot cycle doesn't crash
_position_cache: List[Dict[str, Any]] = []
_position_cache_ts: float = 0.0
_position_cache_ttl: float = 300.0  # Use stale positions for up to 5 minutes
_position_cache_lock = threading.Lock()

def get_cached_crypto_positions(max_age_sec: Optional[float] = None) -> List[Dict[str, Any]]:
    with _position_cache_lock:
        age = time.time() - _position_cache_ts
        ttl = _position_cache_ttl if max_age_sec is None else float(max_age_sec)
        if _position_cache and age <= ttl:
            return list(_position_cache)
    return []


def get_cached_latest_quote(symbol: str, mode: Optional[str] = None, *, allow_stale: bool = True) -> Optional[Dict[str, Any]]:
    sym = _canonical_symbol(symbol)
    if not sym:
        return None
    cached = _get_cached_quote(sym, mode, allow_stale=allow_stale)
    return dict(cached) if cached else None


def get_cached_bars_frame(
    symbol: str,
    timeframe: str = "1Min",
    limit: int = 300,
    mode: Optional[str] = None,
    *,
    allow_stale: bool = True,
) -> Optional[pd.DataFrame]:
    sym = _canonical_symbol(symbol)
    if not sym:
        return None
    _req, capped = _bars_request_and_lookback(timeframe, limit)
    return _get_cached_bars(sym, timeframe, capped, mode, allow_stale=allow_stale)

# Account cache to avoid UI stalls during transient Alpaca trading API issues
_account_cache: Dict[str, Any] = {}
_account_cache_ts: float = 0.0
_account_cache_ttl: float = 300.0
_account_cache_lock = threading.Lock()

_quote_cache: Dict[str, Dict[str, Any]] = {}
_quote_cache_lock = threading.Lock()
_quote_cache_ttl: float = 8.0

_bar_cache: Dict[str, Dict[str, Any]] = {}
_bar_cache_lock = threading.Lock()
_BAR_CACHE_TTL_SEC = {
    "1MIN": 20.0,
    "5MIN": 30.0,
    "15MIN": 120.0,
    "1H": 600.0,
    "1D": 3600.0,
}
_BAR_CACHE_STALE_MULT = 6.0


def _bar_cache_key(symbol: str, timeframe: str, limit: int, mode: Optional[str]) -> str:
    return f"{_canonical_symbol(symbol)}|{str(timeframe or '1Min').upper()}|{int(limit)}|{str(mode or '').lower()}"


def _bar_cache_ttl_sec(timeframe: str) -> float:
    return float(_BAR_CACHE_TTL_SEC.get(str(timeframe or '1Min').strip().upper(), 60.0))


def _get_cached_bars(symbol: str, timeframe: str, limit: int, mode: Optional[str], *, allow_stale: bool = False) -> Optional[pd.DataFrame]:
    key = _bar_cache_key(symbol, timeframe, limit, mode)
    ttl = _bar_cache_ttl_sec(timeframe)
    max_age = ttl * (_BAR_CACHE_STALE_MULT if allow_stale else 1.0)
    with _bar_cache_lock:
        entry = _bar_cache.get(key)
        if not entry:
            return None
        age = time.time() - float(entry.get("ts_sec", 0.0) or 0.0)
        if age > max_age:
            return None
        df = entry.get("df")
    if df is None:
        return None
    try:
        return df.copy()
    except Exception:
        return df


def _set_cached_bars(symbol: str, timeframe: str, limit: int, mode: Optional[str], df: pd.DataFrame) -> None:
    key = _bar_cache_key(symbol, timeframe, limit, mode)
    try:
        stored = df.copy()
    except Exception:
        stored = df
    with _bar_cache_lock:
        _bar_cache[key] = {"ts_sec": time.time(), "df": stored}


def _quote_cache_key(symbol: str, mode: Optional[str]) -> str:
    return f"{_canonical_symbol(symbol)}|{str(mode or '').lower()}"


def _get_cached_quote(symbol: str, mode: Optional[str], *, allow_stale: bool = False) -> Optional[Dict[str, Any]]:
    key = _quote_cache_key(symbol, mode)
    max_age = _quote_cache_ttl * (6.0 if allow_stale else 1.0)
    with _quote_cache_lock:
        entry = _quote_cache.get(key)
        if not entry:
            return None
        age = time.time() - float(entry.get("ts_sec", 0.0) or 0.0)
        if age > max_age:
            return None
        return dict(entry.get("data") or {})


def _set_cached_quote(symbol: str, mode: Optional[str], data: Dict[str, Any]) -> None:
    key = _quote_cache_key(symbol, mode)
    with _quote_cache_lock:
        _quote_cache[key] = {"ts_sec": time.time(), "data": dict(data or {})}


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


def get_crypto_data_client(mode: Optional[str] = None) -> CryptoHistoricalDataClient:
    api_key, secret_key, _ = get_alpaca_credentials(mode=mode)
    if not api_key or not secret_key:
        raise RuntimeError("Alpaca API keys not configured for selected trading mode.")
    return _apply_http_timeouts(CryptoHistoricalDataClient(api_key=api_key, secret_key=secret_key))


def _canonical_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    if "/" in raw:
        return raw
    for quote in ("USD", "USDT", "USDC"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}/{quote}"
    return raw


def _timeframe_from_string(raw: str) -> TimeFrame:
    value = str(raw or "1Min").strip().lower()
    if value in {"1m", "1min", "1minute"}:
        return TimeFrame(1, TimeFrameUnit.Minute)
    if value in {"5m", "5min"}:
        return TimeFrame(5, TimeFrameUnit.Minute)
    if value in {"15m", "15min"}:
        return TimeFrame(15, TimeFrameUnit.Minute)
    if value in {"1h", "1hour"}:
        return TimeFrame(1, TimeFrameUnit.Hour)
    if value in {"4h", "4hour"}:
        return TimeFrame(4, TimeFrameUnit.Hour)
    if value in {"1d", "1day", "day"}:
        return TimeFrame(1, TimeFrameUnit.Day)
    return TimeFrame(1, TimeFrameUnit.Minute)


def list_crypto_assets(limit: int = 60, mode: Optional[str] = None) -> List[Dict[str, Any]]:
    client = get_trading_client(mode=mode)
    req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.CRYPTO)
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
    return out[: max(1, min(int(limit), 300))]


def get_crypto_positions(mode: Optional[str] = None) -> List[Dict[str, Any]]:
    global _position_cache, _position_cache_ts
    try:
        from .bot import current_crypto_config
        cfg = current_crypto_config(resolve_symbols=False)
        if str(cfg.get("active_exchange", "alpaca")).lower() == "kraken":
            positions = kraken_client.get_crypto_positions()
            with _position_cache_lock:
                _position_cache = positions
                _position_cache_ts = time.time()
            return positions
    except Exception:
        pass

    try:
        client = get_trading_client(mode=mode)
        rows = client.get_all_positions() or []
        out: List[Dict[str, Any]] = []
        for p in rows:
            asset_class = str(getattr(p, "asset_class", "") or "").lower()
            symbol_raw = str(getattr(p, "symbol", "") or "")
            symbol = _canonical_symbol(symbol_raw)
            if asset_class and "crypto" not in asset_class and "/" not in symbol:
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
                    "asset_class": asset_class or "crypto",
                }
            )
        out.sort(key=lambda x: x["symbol"])
        # Update the cache on success
        with _position_cache_lock:
            _position_cache = out
            _position_cache_ts = time.time()
        return out
    except Exception as e:
        import logging as _logging
        import time as _time
        _log = _logging.getLogger(__name__)
        with _position_cache_lock:
            age = _time.time() - _position_cache_ts
            if _position_cache and age <= _position_cache_ttl:
                _log.warning(
                    f"[positions] Alpaca API error ({e}). "
                    f"Returning {len(_position_cache)} cached positions ({age:.0f}s old)."
                )
                return list(_position_cache)
        _log.error(f"[positions] Alpaca API error and no usable cache: {e}")
        return []


def get_cached_account_summary(max_age_sec: Optional[float] = None) -> Dict[str, Any]:
    with _account_cache_lock:
        age = time.time() - _account_cache_ts
        ttl = _account_cache_ttl if max_age_sec is None else float(max_age_sec)
        if _account_cache and age <= ttl:
            cached = dict(_account_cache)
            cached.setdefault("status", "cached")
            cached["cached"] = True
            cached["cache_age_sec"] = round(age, 1)
            return cached
    return {}


def get_account_summary(mode: Optional[str] = None) -> Dict[str, Any]:
    global _account_cache, _account_cache_ts
    try:
        from .bot import current_crypto_config
        cfg = current_crypto_config(resolve_symbols=False)
        if str(cfg.get("active_exchange", "alpaca")).lower() == "kraken":
            summary = kraken_client.get_account_summary()
            with _account_cache_lock:
                _account_cache = dict(summary or {})
                _account_cache_ts = time.time()
            return summary
    except Exception:
        pass

    try:
        client = get_trading_client(mode=mode)
        acct = client.get_account()
        summary = {
            "cash": float(getattr(acct, "non_marginable_buying_power", getattr(acct, "cash", 0.0)) or 0.0),
            "equity": float(getattr(acct, "equity", 0.0) or 0.0),
            "buying_power": float(getattr(acct, "buying_power", 0.0) or 0.0),
            "portfolio_value": float(getattr(acct, "portfolio_value", 0.0) or 0.0),
            "account_number": str(getattr(acct, "account_number", "") or ""),
            "status": str(getattr(acct, "status", "") or ""),
        }
        with _account_cache_lock:
            _account_cache = dict(summary)
            _account_cache_ts = time.time()
        return summary
    except Exception as e:
        import logging as _logging
        import time as _time
        _log = _logging.getLogger(__name__)
        with _account_cache_lock:
            age = _time.time() - _account_cache_ts
            if _account_cache and age <= _account_cache_ttl:
                cached = dict(_account_cache)
                cached.setdefault("status", "cached")
                cached["cached"] = True
                cached["cache_age_sec"] = round(age, 1)
                _log.warning(
                    f"[account] Alpaca API error ({e}). Returning cached account summary ({age:.0f}s old)."
                )
                return cached
        raise


def get_latest_quote(symbol: str, mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    sym = _canonical_symbol(symbol)
    if not sym:
        return None
    cached = _get_cached_quote(sym, mode)
    if cached is not None:
        return cached
    client = get_crypto_data_client(mode=mode)
    req = CryptoLatestQuoteRequest(symbol_or_symbols=sym)
    try:
        resp = client.get_crypto_latest_quote(req)
    except Exception:
        stale = _get_cached_quote(sym, mode, allow_stale=True)
        if stale is not None:
            stale.setdefault("cached", True)
            return stale
        raise
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
    payload = {
        "symbol": sym,
        "ask_price": ask,
        "bid_price": bid,
        "mid_price": (ask + bid) / 2.0 if ask and bid else ask or bid or 0.0,
        "timestamp": getattr(quote_obj, "timestamp", None),
    }
    _set_cached_quote(sym, mode, payload)
    return payload


def _bars_request_and_lookback(timeframe: str, limit: int):
    tf = _timeframe_from_string(timeframe)
    capped = max(20, min(int(limit), 2000))
    now = datetime.now(timezone.utc)
    if tf.unit_value == TimeFrameUnit.Day:
        lookback = timedelta(days=max(3, int(capped * 1.6)))
    elif tf.unit_value == TimeFrameUnit.Hour:
        lookback = timedelta(hours=max(12, int(capped * tf.amount_value * 1.5)))
    else:
        lookback = timedelta(minutes=max(60, int(capped * tf.amount_value * 1.7)))
    req = CryptoBarsRequest(
        symbol_or_symbols=[],
        timeframe=tf,
        start=now - lookback,
        end=now,
        limit=capped,
    )
    return req, capped


def _bars_to_frame(sym: str, bars: Any) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for b in bars or []:
        ts = getattr(b, "timestamp", None)
        rows.append({
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "open": float(getattr(b, "open", 0.0) or 0.0),
            "high": float(getattr(b, "high", 0.0) or 0.0),
            "low": float(getattr(b, "low", 0.0) or 0.0),
            "close": float(getattr(b, "close", 0.0) or 0.0),
            "volume": float(getattr(b, "volume", 0.0) or 0.0),
            "trade_count": int(getattr(b, "trade_count", 0) or 0),
            "vwap": float(getattr(b, "vwap", 0.0) or 0.0),
            "symbol": sym,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("timestamp").reset_index(drop=True)


def warm_bars_cache(symbols: List[str], timeframe: str = "1Min", limit: int = 300, mode: Optional[str] = None, chunk_size: int = 8) -> Dict[str, int]:
    sym_list = []
    seen = set()
    capped = max(20, min(int(limit), 2000))
    for raw in symbols or []:
        sym = _canonical_symbol(raw)
        if not sym or sym in seen:
            continue
        seen.add(sym)
        if _get_cached_bars(sym, timeframe, capped, mode) is not None:
            continue
        sym_list.append(sym)
    if not sym_list:
        return {"requested": 0, "warmed": 0, "failed": 0}

    req_template, capped = _bars_request_and_lookback(timeframe, capped)
    client = get_crypto_data_client(mode=mode)
    import logging as _logging
    _log = _logging.getLogger(__name__)
    warmed = 0
    failed = 0
    chunk_size = max(1, min(int(chunk_size or 8), 25))
    for i in range(0, len(sym_list), chunk_size):
        chunk = sym_list[i:i + chunk_size]
        req = CryptoBarsRequest(
            symbol_or_symbols=chunk,
            timeframe=req_template.timeframe,
            start=req_template.start,
            end=req_template.end,
            limit=capped,
        )
        try:
            resp = client.get_crypto_bars(req)
        except Exception as exc:
            failed += len(chunk)
            _log.warning(f"[market_data] batch {timeframe} prefetch failed for {len(chunk)} symbols: {exc}")
            continue
        data = getattr(resp, "data", None)
        if not isinstance(data, dict):
            data = resp if isinstance(resp, dict) else {}
        for sym in chunk:
            bars = data.get(sym) or data.get(sym.replace("/", "")) or []
            df = _bars_to_frame(sym, bars)
            if df.empty:
                failed += 1
                continue
            _set_cached_bars(sym, timeframe, capped, mode, df)
            warmed += 1
    return {"requested": len(sym_list), "warmed": warmed, "failed": failed}


def fetch_bars(symbol: str, timeframe: str = "1Min", limit: int = 300, mode: Optional[str] = None) -> pd.DataFrame:
    sym = _canonical_symbol(symbol)
    if not sym:
        return pd.DataFrame()

    req_template, capped = _bars_request_and_lookback(timeframe, limit)
    cached = _get_cached_bars(sym, timeframe, capped, mode)
    if cached is not None:
        return cached

    req = CryptoBarsRequest(
        symbol_or_symbols=sym,
        timeframe=req_template.timeframe,
        start=req_template.start,
        end=req_template.end,
        limit=capped,
    )
    client = get_crypto_data_client(mode=mode)

    import logging as _logging
    _log = _logging.getLogger(__name__)
    last_exc: Exception = RuntimeError("fetch_bars: no attempts made")
    for attempt in range(3):
        try:
            resp = client.get_crypto_bars(req)
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                wait = 2 ** attempt
                _log.warning(f"[market_data] {sym} bar fetch attempt {attempt + 1}/3 failed, retrying in {wait}s: {exc}")
                time.sleep(wait)
    else:
        stale = _get_cached_bars(sym, timeframe, capped, mode, allow_stale=True)
        if stale is not None:
            _log.warning(f"[market_data] {sym} {timeframe} bar fetch failed; using cached bars: {last_exc}")
            return stale
        raise last_exc

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

    df = _bars_to_frame(sym, bars)
    if df.empty:
        return df
    _set_cached_bars(sym, timeframe, capped, mode, df)
    return df


def fetch_price_near_ts(symbol: str, target_ts_ms: int, timeframe: str = "1Min", mode: Optional[str] = None, window_min: int = 180) -> Optional[float]:
    sym = _canonical_symbol(symbol)
    if not sym or int(target_ts_ms or 0) <= 0:
        return None
    target_dt = datetime.fromtimestamp(int(target_ts_ms) / 1000.0, tz=timezone.utc)
    half_window_min = max(15, int(window_min or 180) // 2)
    start_dt = target_dt - timedelta(minutes=half_window_min)
    end_dt = target_dt + timedelta(minutes=half_window_min)
    try:
        client = get_crypto_data_client(mode=mode)
        tf = _timeframe_from_string(timeframe)
        estimated = max(30, min(800, int(window_min) * (60 if tf.unit_value == TimeFrameUnit.Minute else 2)))
        req = CryptoBarsRequest(
            symbol_or_symbols=sym,
            timeframe=tf,
            start=start_dt,
            end=end_dt,
            limit=estimated,
        )
        resp = client.get_crypto_bars(req)
        data = getattr(resp, "data", None)
        bars = None
        if isinstance(data, dict):
            bars = data.get(sym) or next(iter(data.values()), None)
        elif isinstance(resp, dict):
            bars = resp.get(sym) or next(iter(resp.values()), None)
        else:
            bars = getattr(resp, sym, None)
        df = _bars_to_frame(sym, bars)
        if not df.empty:
            try:
                ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
                valid = df.loc[ts.notna()].copy()
                if not valid.empty:
                    valid["_delta"] = (pd.to_datetime(valid["timestamp"], utc=True, errors="coerce") - target_dt).abs()
                    row = valid.sort_values("_delta").iloc[0]
                    price = float(row.get("close", 0.0) or 0.0)
                    if price > 0:
                        return price
            except Exception:
                try:
                    price = float(df.iloc[-1]["close"] or 0.0)
                    if price > 0:
                        return price
                except Exception:
                    pass
    except Exception:
        pass

    fallback = store.get_nearest_universe_mid_price_sync(
        sym,
        int(target_ts_ms),
        window_min=window_min,
    )
    price = float(fallback.get("price", 0.0) or 0.0)
    return price if price > 0 else None
