"""
Kalshi Router — Wraps existing Kalshi API client and bot strategies.
Provides web endpoints for market browsing, order placement, bot control, and scalping.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import asyncio
import logging
import os
import sys
import re
import time
from datetime import datetime, timezone
from core.config_manager import get_config
from services.notification_manager import broadcast

router = APIRouter()
logger = logging.getLogger("apex.kalshi")

# ── Path to vendored Kalshi modules (force-local imports) ──
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KALSHI_ROOT = os.path.join(BACKEND_ROOT, "integrations", "kalshi")
if KALSHI_ROOT not in sys.path:
    sys.path.insert(0, KALSHI_ROOT)

# ── State ──
_bot_task: Optional[asyncio.Task] = None
_bot_status: Dict[str, Any] = {"running": False, "strategy": None, "iterations": 0}
_price_cache: Dict[str, Any] = {"price": None, "ts": 0.0}
_activity_log: List[Dict[str, Any]] = []
_ACTIVITY_LOG_MAX = 100
_ALERT_COOLDOWN_SEC = 60
_candidate_alert_last_sent: Dict[str, float] = {}


def _get_trading_mode() -> str:
    cfg = get_config()
    mode = (
        cfg.get("events", {})
        .get("kalshi", {})
        .get("trading_mode", "live")
    )
    if isinstance(mode, str) and mode.lower() in {"live", "offline"}:
        return mode.lower()
    return "live"


def _trading_enabled() -> bool:
    return _get_trading_mode() == "live"


def _get_kalshi_cfg() -> Dict[str, Any]:
    return get_config().get("events", {}).get("kalshi", {})


def _get_copy_follow_accounts() -> List[str]:
    raw = _get_kalshi_cfg().get("copy_follow_accounts", [])
    if isinstance(raw, list):
        accounts = [str(v).strip() for v in raw if str(v).strip()]
        return accounts
    return []


def _get_copy_ratio() -> float:
    raw = _get_kalshi_cfg().get("copy_trade_ratio", 0.1)
    try:
        ratio = float(raw)
    except (TypeError, ValueError):
        ratio = 0.1
    return max(0.0, min(1.0, ratio))


def _candidate_alert_allowed(key: str, now_ts: float) -> bool:
    prev = _candidate_alert_last_sent.get(key, 0.0)
    if now_ts - prev < _ALERT_COOLDOWN_SEC:
        return False
    _candidate_alert_last_sent[key] = now_ts
    return True


async def _maybe_alert_candidate(result: Dict[str, Any]) -> None:
    ticker = str(result.get("ticker", "")).strip()
    if not ticker:
        return
    side = str(result.get("side", "yes")).strip().lower() or "yes"
    key = f"{ticker}:{side}"
    now_ts = time.time()
    if not _candidate_alert_allowed(key, now_ts):
        return
    expected_profit = result.get("expected_profit")
    profit_msg = ""
    if isinstance(expected_profit, (int, float)):
        profit_msg = f" | Edge ${expected_profit:.4f}"
    await broadcast(
        "kalshi",
        "toast",
        {
            "title": f"Kalshi Candidate Found: {ticker}",
            "message": f"{side.upper()} setup detected{profit_msg}",
            "type": "signal",
            "domain": "Events",
        },
    )


def _log_activity(event_type: str, message: str, details: Optional[Dict[str, Any]] = None):
    """Append an event to the rolling activity log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,  # scan | opportunity | trade | error | info
        "message": message,
        "details": details or {},
    }
    _activity_log.insert(0, entry)
    # Cap the log
    while len(_activity_log) > _ACTIVITY_LOG_MAX:
        _activity_log.pop()


# ── Request Models ──
class OrderRequest(BaseModel):
    ticker: str
    side: str  # 'yes' or 'no'
    quantity: int
    order_type: str = "limit"  # 'limit' or 'market'
    price: Optional[int] = None  # cents

class BotStartRequest(BaseModel):
    strategy: str  # 'arbitrage', 'copy', 'market-maker'
    dry_run: bool = True
    interval: int = 60
    max_position: Optional[float] = None
    follow_accounts: Optional[List[str]] = None
    copy_ratio: Optional[float] = None

class ScalperOrderRequest(BaseModel):
    ticker: str
    side: str  # 'yes' or 'no'
    quantity: int
    price: Optional[int] = None  # If None, use market order


class ErrorResponse(BaseModel):
    status: str = "error"
    error: str


class KalshiHealthResponse(BaseModel):
    status: str
    balance: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class KalshiMarketsResponse(BaseModel):
    markets: List[Dict[str, Any]] = Field(default_factory=list)
    count: int
    status: str = "ok"
    error: Optional[str] = None


class KalshiPositionsResponse(BaseModel):
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "ok"
    error: Optional[str] = None


class KalshiBalanceResponse(BaseModel):
    balance: Optional[float] = None
    status: str = "ok"
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class BotStatusResponse(BaseModel):
    running: bool = False
    strategy: Optional[str] = None
    iterations: int = 0
    dry_run: Optional[bool] = None
    trading_mode: Optional[str] = None
    copy_follow_accounts: Optional[List[str]] = None
    copy_ratio: Optional[float] = None
    error: Optional[str] = None
    last_results: Optional[List[Dict[str, Any]]] = None


class ActivityEntry(BaseModel):
    ts: str
    type: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ActivityLogResponse(BaseModel):
    entries: List[ActivityEntry] = Field(default_factory=list)
    count: int = 0


class ScalperDashboardResponse(BaseModel):
    current_price: Optional[float] = None
    momentum: float = 0.0
    momentum_direction: str = "neutral"
    volatility: float = 0.0
    price_count: int = 0
    contracts: List[Dict[str, Any]] = Field(default_factory=list)
    last_signal: Optional[Dict[str, Any]] = None
    stats: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class ScalperTickResponse(BaseModel):
    dashboard: Dict[str, Any]
    tick_price: Optional[float] = None
    contracts_loaded: int = 0
    signals: List[Dict[str, Any]] = Field(default_factory=list)


class KalshiOrderbookResponse(BaseModel):
    status: str = "ok"
    error: Optional[str] = None
    bids: List[Dict[str, Any]] = Field(default_factory=list)
    asks: List[Dict[str, Any]] = Field(default_factory=list)


class KalshiTradesResponse(BaseModel):
    trades: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "ok"
    error: Optional[str] = None


class KalshiPortfolioResponse(BaseModel):
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    orders: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "ok"
    error: Optional[str] = None


class OrderActionResponse(BaseModel):
    success: bool
    order: Optional[Dict[str, Any]] = None


class CancelOrderResponse(BaseModel):
    success: bool


class BotStartResponse(BaseModel):
    message: str
    status: BotStatusResponse


class MessageResponse(BaseModel):
    message: str


class FeedPriceResponse(BaseModel):
    price_recorded: bool
    signals: List[Dict[str, Any]] = Field(default_factory=list)


class SetContractsResponse(BaseModel):
    message: str


class WhalesResponse(BaseModel):
    account_score: float
    classification: str
    indicators: Dict[str, Any] = Field(default_factory=dict)
    trade_stats: Dict[str, Any] = Field(default_factory=dict)
    active_positions: List[Dict[str, Any]] = Field(default_factory=list)
    position_summary: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


# ── Helpers ──
def _get_api():
    """Lazy-load Kalshi API client."""
    from api_client import KalshiAPI
    return KalshiAPI()


def _api_is_authenticated(api: Any) -> bool:
    """Check whether Kalshi client has loaded credentials + RSA key."""
    return bool(getattr(api, "api_key_id", "")) and getattr(api, "private_key_obj", None) is not None


def _extract_strike_level(market: Dict[str, Any]) -> Optional[float]:
    """Best-effort strike extraction from market payload/title/ticker."""
    for key in ("strike_level", "strike", "strike_price", "close_level"):
        val = market.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)

    title = str(market.get("title", ""))
    ticker = str(market.get("ticker", ""))
    for text in (title, ticker):
        # Prefer 4-5 digit levels for index markets.
        matches = re.findall(r"(\d{4,5}(?:\.\d+)?)", text)
        if matches:
            try:
                return float(matches[-1])
            except ValueError:
                pass
    return None


def _auto_load_scalper_contracts(scalper) -> int:
    """Load likely S&P contracts when scalper has no active contracts."""
    if scalper.active_contracts:
        return 0

    try:
        api = _get_api()
        markets = api.get_markets(limit=200, status="open")
    except Exception:
        return 0

    contracts: List[Dict[str, Any]] = []
    for m in markets:
        title = str(m.get("title", "")).lower()
        ticker = str(m.get("ticker", ""))
        if not any(k in title for k in ("s&p", "spx", "s&p 500", "500")):
            continue
        strike = _extract_strike_level(m)
        if not strike:
            continue
        yes_price = int(m.get("yes_price", 50) or 50)
        no_price = int(m.get("no_price", max(0, 100 - yes_price)) or max(0, 100 - yes_price))
        contracts.append(
            {
                "ticker": ticker,
                "strike_level": strike,
                "yes_price": yes_price,
                "no_price": no_price,
            }
        )

    if contracts:
        contracts.sort(key=lambda c: abs(c["yes_price"] - 50))
        scalper.set_contracts(contracts[:25])
        return min(len(contracts), 25)
    return 0


def _get_spx_price_cached(ttl_seconds: float = 2.0) -> Optional[float]:
    """Fetch latest S&P 500 proxy price with short TTL cache."""
    now = time.time()
    cached_price = _price_cache.get("price")
    cached_ts = float(_price_cache.get("ts", 0.0))
    if cached_price is not None and (now - cached_ts) < ttl_seconds:
        return float(cached_price)

    try:
        import yfinance as yf
        ticker = yf.Ticker("^GSPC")
        fast = getattr(ticker, "fast_info", None) or {}
        price = fast.get("lastPrice") or fast.get("regularMarketPrice")
        if price is not None:
            _price_cache["price"] = float(price)
            _price_cache["ts"] = now
            return float(price)
    except Exception:
        return float(cached_price) if cached_price is not None else None

    return float(cached_price) if cached_price is not None else None


# ── Market Data ──
@router.get("/health", response_model=KalshiHealthResponse)
async def kalshi_health():
    try:
        api = await asyncio.to_thread(_get_api)
        balance = await asyncio.to_thread(api.get_balance)
        return {"status": "connected", "balance": balance}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/markets", response_model=KalshiMarketsResponse)
async def list_markets(limit: int = 50, status: str = "open"):
    try:
        api = await asyncio.to_thread(_get_api)
        markets = await asyncio.to_thread(api.get_markets, limit, None, status)
        return {"markets": markets, "count": len(markets), "status": "ok"}
    except Exception as e:
        return {"markets": [], "count": 0, "status": "error", "error": str(e)}


@router.get("/markets/{ticker}", response_model=Dict[str, Any])
async def get_market(ticker: str):
    try:
        api = await asyncio.to_thread(_get_api)
        market = await asyncio.to_thread(api.get_market, ticker)
        if not market:
            raise HTTPException(status_code=404, detail=f"Market {ticker} not found")
        return market
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orderbook/{ticker}", response_model=KalshiOrderbookResponse)
async def get_orderbook(ticker: str):
    try:
        api = await asyncio.to_thread(_get_api)
        book = await asyncio.to_thread(api.get_orderbook, ticker)
        return {
            "status": "ok",
            "bids": book.get("bids", []) if isinstance(book, dict) else [],
            "asks": book.get("asks", []) if isinstance(book, dict) else [],
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "bids": [], "asks": []}


@router.get("/trades/{ticker}", response_model=KalshiTradesResponse)
async def get_trades(ticker: str, limit: int = 50):
    try:
        api = await asyncio.to_thread(_get_api)
        trades = await asyncio.to_thread(api.get_trades, ticker, limit)
        return {"trades": trades, "status": "ok"}
    except Exception as e:
        return {"trades": [], "status": "error", "error": str(e)}


# ── Portfolio ──
@router.get("/portfolio", response_model=KalshiPortfolioResponse)
async def get_portfolio():
    try:
        api = await asyncio.to_thread(_get_api)
        portfolio = await asyncio.to_thread(api.get_portfolio)
        return portfolio
    except Exception as e:
        return {"status": "error", "error": str(e), "positions": [], "orders": []}


@router.get("/positions", response_model=KalshiPositionsResponse)
async def get_positions():
    try:
        api = await asyncio.to_thread(_get_api)
        positions = await asyncio.to_thread(api.get_positions)
        return {"positions": positions, "status": "ok"}
    except Exception as e:
        return {"positions": [], "status": "error", "error": str(e)}


@router.get("/balance", response_model=KalshiBalanceResponse)
async def get_balance():
    try:
        api = await asyncio.to_thread(_get_api)
        if not _api_is_authenticated(api):
            return {"balance": None, "status": "not_configured", "raw": None}
        balance_data = await asyncio.to_thread(api.get_balance)
        if not balance_data:
            return {"balance": None, "status": "not_configured", "raw": None}

        parsed_balance: Optional[float] = None
        if isinstance(balance_data, dict):
            for key in ("balance", "available_balance", "cash_balance"):
                value = balance_data.get(key)
                if isinstance(value, (int, float)):
                    parsed_balance = float(value)
                    break
                if isinstance(value, str):
                    try:
                        parsed_balance = float(value)
                        break
                    except ValueError:
                        pass
        return {"balance": parsed_balance, "status": "ok", "raw": balance_data}
    except Exception as e:
        # Return graceful response instead of 500 when keys not configured
        return {"balance": None, "status": "not_configured", "error": str(e), "raw": None}


# ── Order Execution (Grey Box) ──
@router.post("/order", response_model=OrderActionResponse)
async def place_order(request: OrderRequest):
    """Place an order on Kalshi. User confirms via UI before this is called."""
    if not _trading_enabled():
        raise HTTPException(status_code=403, detail="Trading mode is Offline")
    try:
        api = await asyncio.to_thread(_get_api)
        result = await asyncio.to_thread(
            api.place_order,
            request.ticker,
            request.side,
            request.quantity,
            request.order_type,
            request.price,
        )
        if result:
            return {"success": True, "order": result}
        else:
            raise HTTPException(status_code=400, detail="Order failed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/order/{order_id}", response_model=CancelOrderResponse)
async def cancel_order(order_id: str):
    try:
        api = await asyncio.to_thread(_get_api)
        success = await asyncio.to_thread(api.cancel_order, order_id)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Scalper (Quick Buy/Sell) ──
@router.post("/scalp", response_model=OrderActionResponse)
async def scalp_order(request: ScalperOrderRequest):
    """Fast execution endpoint for the scalper view. Minimal validation for speed."""
    if not _trading_enabled():
        raise HTTPException(status_code=403, detail="Trading mode is Offline")
    try:
        api = await asyncio.to_thread(_get_api)
        order_type = "market" if request.price is None else "limit"
        result = await asyncio.to_thread(
            api.place_order,
            request.ticker,
            request.side,
            request.quantity,
            order_type,
            request.price,
        )
        return {"success": bool(result), "order": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Bot Control ──
@router.post("/bot/start", response_model=BotStartResponse)
async def start_bot(request: BotStartRequest):
    global _bot_task, _bot_status

    if _bot_task and not _bot_task.done():
        raise HTTPException(status_code=409, detail="Bot already running")

    trading_mode = _get_trading_mode()
    effective_dry_run = not _trading_enabled()
    copy_follow_accounts: List[str] = []
    copy_ratio: Optional[float] = None

    if request.strategy == "copy":
        copy_follow_accounts = (
            [a.strip() for a in (request.follow_accounts or []) if isinstance(a, str) and a.strip()]
            or _get_copy_follow_accounts()
        )
        if not copy_follow_accounts:
            raise HTTPException(
                status_code=400,
                detail="Copy strategy requires follow accounts (set events.kalshi.copy_follow_accounts)",
            )
        copy_ratio = (
            max(0.0, min(1.0, float(request.copy_ratio)))
            if request.copy_ratio is not None
            else _get_copy_ratio()
        )

    _bot_status = {
        "running": True,
        "strategy": request.strategy,
        "iterations": 0,
        "dry_run": effective_dry_run,
        "trading_mode": trading_mode,
        "copy_follow_accounts": copy_follow_accounts,
        "copy_ratio": copy_ratio,
    }

    async def _run_bot():
        global _bot_status
        try:
            api = await asyncio.to_thread(_get_api)
            from risk_manager import RiskManager

            risk_mgr = RiskManager()

            if request.strategy == "arbitrage":
                from strategies.arbitrage import ArbitrageStrategy
                strat = ArbitrageStrategy(api, risk_mgr)
            elif request.strategy == "copy":
                from strategies.copy_trader import CopyTradingStrategy
                strat = CopyTradingStrategy(api, risk_mgr)
                if copy_ratio is not None:
                    strat.copy_ratio = copy_ratio
            elif request.strategy == "market-maker":
                from strategies.market_maker import MarketMakerStrategy
                strat = MarketMakerStrategy(api, risk_mgr)
            else:
                _bot_status["running"] = False
                _log_activity("error", f"Unknown strategy: {request.strategy}")
                return

            _log_activity("info", f"Bot started: {request.strategy}", {
                "strategy": request.strategy,
                "dry_run": effective_dry_run,
                "trading_mode": trading_mode,
                "interval": request.interval,
                "copy_follow_accounts": copy_follow_accounts if request.strategy == "copy" else None,
                "copy_ratio": copy_ratio if request.strategy == "copy" else None,
            })

            while _bot_status["running"]:
                # Check for global sleep mode
                from core.state import state
                if state.paused:
                    _log_activity("info", "Bot sleeping (system paused)...")
                    while state.paused and _bot_status["running"]:
                        await asyncio.sleep(5)
                    if not _bot_status["running"]:
                        break
                    _log_activity("info", "Bot resuming...")

                _bot_status["iterations"] += 1
                iteration = _bot_status["iterations"]

                _log_activity("scan", f"Scan #{iteration} started", {"strategy": request.strategy})

                if request.strategy == "arbitrage":
                    results = await asyncio.to_thread(strat.run, 5, effective_dry_run)
                elif request.strategy == "copy":
                    results = await asyncio.to_thread(
                        strat.run,
                        copy_follow_accounts,
                        request.interval,
                        1,
                        effective_dry_run,
                    )
                elif request.strategy == "market-maker":
                    # Market Maker needs a list of ticker strings
                    try:
                        raw_markets = await asyncio.to_thread(api.get_markets, 20)
                        tickers = [m.get("ticker") for m in (raw_markets or []) if m.get("ticker")]
                    except Exception:
                        tickers = []
                    if not tickers:
                        _log_activity("error", "No markets available for market making")
                        await asyncio.sleep(request.interval)
                        continue
                    results = await asyncio.to_thread(
                        strat.run, tickers[:10], request.interval, 1, effective_dry_run
                    )
                else:
                    results = []
                _bot_status["last_results"] = results or []

                # Log each result
                if results:
                    for r in results:
                        status = r.get("status", "unknown")
                        ticker = r.get("ticker", "")
                        if status == "dry_run":
                            profit = r.get("expected_profit", 0)
                            _log_activity("opportunity", f"Opportunity found: {ticker}", {
                                "ticker": ticker,
                                "profit": f"${profit:.4f}" if profit else "—",
                                "status": "dry_run",
                                **{k: v for k, v in r.items() if k not in ("status",)},
                            })
                            await _maybe_alert_candidate(r)
                        elif status == "success":
                            _log_activity("trade", f"Trade executed: {ticker}", r)
                            await _maybe_alert_candidate(r)
                        elif status == "error":
                            _log_activity("error", f"Trade failed: {r.get('reason', 'unknown')}", r)
                else:
                    _log_activity("scan", f"Scan #{iteration} complete — no opportunities")

                await asyncio.sleep(request.interval)

        except asyncio.CancelledError:
            _log_activity("info", "Bot stopped by user")
        except Exception as e:
            _bot_status["error"] = str(e)
            _log_activity("error", f"Bot crashed: {e}")
        finally:
            _bot_status["running"] = False

    _bot_task = asyncio.create_task(_run_bot())
    return {"message": f"Bot started: {request.strategy}", "status": _bot_status}


@router.post("/bot/stop", response_model=MessageResponse)
async def stop_bot():
    global _bot_task, _bot_status
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
        _bot_status["running"] = False
        return {"message": "Bot stopped"}
    return {"message": "No bot running"}


@router.get("/bot/status", response_model=BotStatusResponse)
async def get_bot_status():
    return {
        "running": bool(_bot_status.get("running", False)),
        "strategy": _bot_status.get("strategy"),
        "iterations": int(_bot_status.get("iterations", 0)),
        "dry_run": _bot_status.get("dry_run"),
        "trading_mode": _bot_status.get("trading_mode", _get_trading_mode()),
        "copy_follow_accounts": _bot_status.get("copy_follow_accounts"),
        "copy_ratio": _bot_status.get("copy_ratio"),
        "error": _bot_status.get("error"),
        "last_results": _bot_status.get("last_results"),
    }


@router.get("/bot/activity", response_model=ActivityLogResponse)
async def get_bot_activity(limit: int = 50):
    """Return the most recent bot activity log entries."""
    entries = _activity_log[:limit]
    return {"entries": entries, "count": len(entries)}


# ── Scalper Service ──
@router.get("/scalper/dashboard", response_model=ScalperDashboardResponse)
async def scalper_dashboard():
    """Get live scalper data: price, momentum, contracts, signals."""
    try:
        from services.kalshi_scalper import get_scalper
        scalper = get_scalper()
        return scalper.get_dashboard_data()
    except Exception as e:
        return {
            "current_price": None,
            "momentum": 0,
            "momentum_direction": "neutral",
            "volatility": 0,
            "price_count": 0,
            "contracts": [],
            "last_signal": None,
            "stats": {"signals_emitted": 0, "prices_processed": 0},
            "error": str(e),
        }


@router.post("/scalper/tick", response_model=ScalperTickResponse)
async def scalper_tick():
    """
    Ingest one live price tick and return refreshed scalper state.
    This is safe to call every ~1s from the frontend.
    """
    try:
        from services.kalshi_scalper import get_scalper
        scalper = get_scalper()

        contracts_loaded = await asyncio.to_thread(_auto_load_scalper_contracts, scalper)
        price = await asyncio.to_thread(_get_spx_price_cached)
        if price is not None:
            scalper.add_price(price)

        signals = scalper.generate_signals()
        return {
            "dashboard": scalper.get_dashboard_data(),
            "tick_price": price,
            "contracts_loaded": contracts_loaded,
            "signals": [
                {
                    "direction": s.direction,
                    "confidence": s.confidence,
                    "contract_ticker": s.contract_ticker,
                    "strike_level": s.strike_level,
                    "current_price": s.current_price,
                    "momentum": s.momentum,
                    "reasoning": s.reasoning,
                }
                for s in signals
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scalper/quick-order", response_model=OrderActionResponse)
async def scalper_quick_order(request: ScalperOrderRequest):
    """Ultra-fast order for the scalper view. Minimal latency path."""
    if not _trading_enabled():
        raise HTTPException(status_code=403, detail="Trading mode is Offline")
    try:
        api = await asyncio.to_thread(_get_api)
        order_type = "market" if request.price is None else "limit"
        result = await asyncio.to_thread(
            api.place_order,
            request.ticker,
            request.side,
            request.quantity,
            order_type,
            request.price,
        )
        return {"success": bool(result), "order": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scalper/feed-price", response_model=FeedPriceResponse)
async def feed_price(price: float):
    """Feed a live S&P 500 price to the scalper engine."""
    from services.kalshi_scalper import get_scalper
    scalper = get_scalper()
    scalper.add_price(price)

    # Generate and return any new signals
    signals = scalper.generate_signals()
    return {
        "price_recorded": True,
        "signals": [
            {
                "direction": s.direction,
                "confidence": s.confidence,
                "contract": s.contract_ticker,
                "reasoning": s.reasoning,
            }
            for s in signals
        ],
    }


@router.post("/scalper/set-contracts", response_model=SetContractsResponse)
async def set_contracts(contracts: List[Dict[str, Any]]):
    """Set the active contracts for the scalper to monitor."""
    from services.kalshi_scalper import get_scalper
    scalper = get_scalper()
    scalper.set_contracts(contracts)
    return {"message": f"Loaded {len(contracts)} contracts"}


# ═══════════════════════════════
# Phase 4: Whale Tracker
# ═══════════════════════════════

@router.get("/whales", response_model=WhalesResponse)
async def get_whales():
    """
    Detect whale-like trading patterns.
    Uses the legacy AccountScanner + BotDetector to identify
    high-volume, high-win-rate accounts and surface their positions.
    """
    try:
        api = await asyncio.to_thread(_get_api)

        # Get your own account data and analyze it
        from account_scanner import AccountScanner
        scanner = AccountScanner(api=api)
        analysis = await asyncio.to_thread(scanner.scan_account)

        bot_analysis = analysis.get("bot_analysis", {})
        trade_stats = analysis.get("trade_stats", {})
        position_stats = analysis.get("position_stats", {})

        # Get current positions to identify what "smart money" is holding
        positions = []
        try:
            raw_positions = await asyncio.to_thread(api.get_positions)
            for pos in raw_positions:
                positions.append({
                    "ticker": pos.get("ticker", ""),
                    "side": "yes" if pos.get("position", 0) > 0 else "no",
                    "quantity": abs(pos.get("position", 0)),
                    "market_price": pos.get("market_price", 0),
                    "unrealized_pnl": pos.get("unrealized_pnl", 0),
                })
        except Exception:
            pass

        result = {
            "account_score": bot_analysis.get("bot_score", 0),
            "classification": bot_analysis.get("classification", "unknown"),
            "indicators": bot_analysis.get("indicators", {}),
            "trade_stats": {
                "avg_size": trade_stats.get("avg_size", 0),
            },
            "active_positions": positions,
            "position_summary": {
                "total": position_stats.get("total_positions", 0),
                "value": position_stats.get("total_value", 0),
                "pnl": position_stats.get("total_pnl", 0),
            },
        }

        # Notify if whale detected
        score = bot_analysis.get("bot_score", 0)
        if score > 80:
            from services.notification_manager import send_toast
            await send_toast(
                title="Whale Detected 🐋",
                message=f"High-activity account found! Score: {score}/100",
                type="warning"
            )

        return result

    except Exception as e:
        logger.error(f"Whale tracker failed: {e}")
        # Return demo data if the scanner isn't connected
        return {
            "account_score": 0.0,
            "classification": "unavailable",
            "indicators": {},
            "trade_stats": {"total_trades": 0, "unique_markets": 0, "total_volume": 0, "avg_size": 0},
            "active_positions": [],
            "position_summary": {"total": 0, "value": 0, "pnl": 0},
            "message": "Connect Kalshi API to enable whale tracking",
        }
