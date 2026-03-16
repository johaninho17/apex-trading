"""
Alpaca Router — Full feature port from alpaca/ modules.
Imports MarketScanner, TechnicalAnalyst, TradePredictor, ExecutionEngine
directly from the alpaca project via sys.path.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import asyncio
import json
import os
import sys
import threading
import time
import subprocess
from contextlib import contextmanager

from core import job_store, background_services
from core.config_manager import get_config

router = APIRouter()

# ── Path to vendored Alpaca modules & sys.path injection ──
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALPACA_ROOT = os.path.join(BACKEND_ROOT, "integrations", "alpaca")

# Inject alpaca/ into Python path so we can import its modules directly
# CRITICAL: use append (not insert) so alpaca-py pip package resolves first,
# before the project's alpaca/ directory
if ALPACA_ROOT not in sys.path:
    sys.path.append(ALPACA_ROOT)

# Load alpaca .env for API keys
from dotenv import load_dotenv
load_dotenv(os.path.join(ALPACA_ROOT, ".env"))
from runtime_config import (
    get_alpaca_credentials,
    get_trading_mode as _runtime_get_trading_mode,
    save_trading_mode,
)

# ── Request Models ──
class AnalysisRequest(BaseModel):
    ticker: str

class TradeRequest(BaseModel):
    symbol: str
    qty: float
    side: str = "buy"
    entry: float
    stop_loss: float
    target: float
    trailing_stop: bool = False
    setup_type: str = "Conservative (Pullback)"
    use_kelly: bool = False

class ScannerRequest(BaseModel):
    strategy: str = "both"  # "atr", "ma", or "both"

class ClosePositionRequest(BaseModel):
    symbol: str

class SimpleOrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str = "buy"          # buy / sell
    order_type: str = "market"  # market / limit
    limit_price: Optional[float] = None
    time_in_force: str = "day"  # day / gtc / ioc

class BracketOrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str = "buy"
    limit_price: Optional[float] = None  # None = market entry
    stop_loss: float
    take_profit: float
    time_in_force: str = "day"


class CryptoOrderRequest(BaseModel):
    symbol: str
    side: str = "buy"  # buy | sell
    order_type: str = "market"  # market | limit | stop_limit
    qty: Optional[float] = None
    notional: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "gtc"  # gtc | ioc | day


class CryptoConfigUpdateRequest(BaseModel):
    updates: Dict[str, Any] = Field(default_factory=dict)


class AlpacaHealthResponse(BaseModel):
    status: str
    trading_mode: str


class TradingModeResponse(BaseModel):
    trading_mode: str


class TradingModeUpdateResponse(BaseModel):
    message: str
    mode: str


class SearchResult(BaseModel):
    symbol: str
    name: str


class SearchResponse(BaseModel):
    results: List[SearchResult] = Field(default_factory=list)


class ScannerStartResponse(BaseModel):
    message: str
    strategy: Optional[str] = None
    status: Optional[str] = None
    job_id: Optional[str] = None


class ScannerStopResponse(BaseModel):
    message: str
    stopping: bool = False


class ScannerStatusResponse(BaseModel):
    status: str = "idle"
    progress: int = 0
    total: Optional[int] = None
    message: Optional[str] = None
    last_match: Optional[str] = None
    is_running: bool = False
    stop_requested: bool = False
    job_id: Optional[str] = None
    job_status: Optional[str] = None
    error: Optional[str] = None


class ScannerResultsResponse(BaseModel):
    atr: List[Dict[str, Any]] = Field(default_factory=list)
    ma: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: Optional[str] = None


class AnalysisResponse(BaseModel):
    ticker: str
    analysis: Dict[str, Any]
    ai_scores: Dict[str, Any]
    setups: List[Dict[str, Any]] = Field(default_factory=list)


class PredictResponse(BaseModel):
    ticker: str
    clean_win: float
    eventual_win: float
    composite: float
    signal: str
    model_loaded: bool
    error: Optional[str] = None


class PortfolioPosition(BaseModel):
    symbol: str
    qty: float
    avg_entry: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    side: str


class PortfolioResponse(BaseModel):
    cash: float = 0.0
    portfolio_value: float = 0.0
    buying_power: float = 0.0
    equity: float = 0.0
    trading_mode: str = "paper"
    positions: List[PortfolioPosition] = Field(default_factory=list)
    error: Optional[str] = None


class ClosePositionResponse(BaseModel):
    success: bool
    message: str


class PortfolioHistoryResponse(BaseModel):
    timestamps: List[Any] = Field(default_factory=list)
    equity: List[float] = Field(default_factory=list)
    profit_loss: List[float] = Field(default_factory=list)
    total_return_pct: float = 0.0
    total_return_dollar: float = 0.0
    timeframe: Optional[str] = None
    error: Optional[str] = None


class TopMoversResponse(BaseModel):
    movers: List[Dict[str, Any]] = Field(default_factory=list)


class TradeExecuteResponse(BaseModel):
    success: bool
    result: Optional[Dict[str, Any]] = None


class OrderResponse(BaseModel):
    success: bool
    order: Dict[str, Any]


class ChartDataResponse(BaseModel):
    ticker: str
    candles: List[Dict[str, Any]] = Field(default_factory=list)
    volumes: List[Dict[str, Any]] = Field(default_factory=list)
    sma20: List[Dict[str, Any]] = Field(default_factory=list)
    sma50: List[Dict[str, Any]] = Field(default_factory=list)
    rsi: List[Dict[str, Any]] = Field(default_factory=list)
    bb_upper: List[Dict[str, Any]] = Field(default_factory=list)
    bb_lower: List[Dict[str, Any]] = Field(default_factory=list)


class BacktestResponse(BaseModel):
    total_trades: Optional[int] = None
    win_rate: Optional[float] = None
    profit_factor: Optional[float] = None
    total_return: Optional[float] = None
    trades: List[Dict[str, Any]] = Field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None


class RiskCalculatorResponse(BaseModel):
    sizing: Dict[str, Any]
    validation: Dict[str, Any]


class EarningsResponse(BaseModel):
    safe: Optional[bool] = None
    message: Optional[str] = None
    days_until: Optional[int] = None
    next_earnings: Optional[str] = None
    error: Optional[str] = None


class QuoteResponse(BaseModel):
    ticker: str
    price: float = 0.0
    change_pct: float = 0.0
    prev_close: float = 0.0
    error: Optional[str] = None

# ── Settings ──
def get_trading_mode():
    return _runtime_get_trading_mode(default="paper")

def set_trading_mode(mode: str):
    return save_trading_mode(mode)


def _is_auth_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "unauthorized" in text or "authentication" in text or "401" in text


def _is_network_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return (
        "timed out" in text
        or "max retries exceeded" in text
        or "name resolution" in text
        or "failed to establish a new connection" in text
        or "connection aborted" in text
        or "temporary failure in name resolution" in text
    )


def _crypto_credential_mode() -> str:
    raw = get_config().get("stocks", {}).get("crypto", {}).get("account_mode", "paper")
    return "live" if str(raw or "").strip().lower() == "live" else "paper"


def _crypto_min_order_notional() -> float:
    raw = get_config().get("stocks", {}).get("crypto", {}).get("min_order_notional_usd", 10.0)
    try:
        return max(1.0, float(raw))
    except Exception:
        return 10.0


def _normalize_crypto_symbol(symbol: str) -> str:
    from urllib.parse import unquote
    raw = unquote(str(symbol or "")).strip().upper()
    if not raw:
        return ""
    if "/" in raw:
        return raw
    if raw.endswith("USD") and len(raw) > 3:
        return raw[:-3] + "/USD"
    return raw + "/USD"


def _timeframe_limit_from_range(range_key: str) -> tuple[str, int]:
    key = str(range_key or "1D").upper()
    if key == "7D":
        return "1H", 168
    if key == "30D":
        return "4H", 180
    if key == "90D":
        return "1D", 90
    if key == "ALL":
        return "1D", 365
    return "15Min", 96

# ── Scanner state ──
_scanner_running = False
_scanner_stop_requested = False
_scanner_job_id: Optional[str] = None
_alpaca_cwd_lock = threading.RLock()
_portfolio_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_portfolio_cache_ttl_sec = 8.0
_top_movers_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_top_movers_cache_ttl_sec = 600.0


@contextmanager
def _alpaca_cwd():
    """
    Serialize temporary CWD changes for legacy modules that rely on relative files.
    This avoids process-wide cwd races under concurrent requests.
    """
    with _alpaca_cwd_lock:
        original_cwd = os.getcwd()
        os.chdir(ALPACA_ROOT)
        try:
            yield
        finally:
            os.chdir(original_cwd)

# Initialize Alpaca keys from saved mode on startup
set_trading_mode(get_trading_mode())

def _run_scanner_background(job_id: str):
    """Run the full background scanner in a thread."""
    global _scanner_running, _scanner_stop_requested, _scanner_job_id
    _scanner_running = True
    _scanner_stop_requested = False
    _scanner_job_id = job_id
    job_store.mark_running(job_id, message="Scanner thread started")
    _last_touch = time.monotonic()
    try:
        # Run in locked alpaca cwd so scanner writes outputs consistently.
        with _alpaca_cwd():
            from scanner_worker import BackgroundScanner
            
            # Inject pause callback
            from core.state import state
            import time
            
            def pause_check():
                nonlocal _last_touch
                now = time.monotonic()
                if now - _last_touch >= 5:
                    job_store.touch(job_id, message="Scanner running")
                    _last_touch = now
                if _scanner_stop_requested:
                    raise RuntimeError("Scan cancelled by user")
                while state.paused and not _scanner_stop_requested:
                    now = time.monotonic()
                    if now - _last_touch >= 5:
                        job_store.touch(job_id, message="Scanner paused by system")
                        _last_touch = now
                    time.sleep(1)
                if _scanner_stop_requested:
                    raise RuntimeError("Scan cancelled by user")
            
            scanner = BackgroundScanner()
            scanner.pause_check_callback = pause_check
            
            scanner.run_full_scan()

            # After scan completes, also write scan_results.json for the API
            _save_results_as_json()
            job_store.mark_completed(job_id, message="Scanner completed")
    except Exception as e:
        print(f"Scanner error: {e}")
        import traceback
        traceback.print_exc()
        text = str(e).lower()
        if "cancelled" in text:
            job_store.mark_cancelled(job_id, message="Scanner cancelled by user")
        else:
            job_store.mark_failed(job_id, str(e))
    finally:
        _scanner_running = False
        _scanner_stop_requested = False
        _scanner_job_id = None

def _save_results_as_json():
    """Convert CSV results to JSON for API consumption."""
    import pandas as pd
    results = {"atr": [], "ma": [], "timestamp": None}
    
    atr_csv = os.path.join(ALPACA_ROOT, "scanner_results_atr.csv")
    ma_csv = os.path.join(ALPACA_ROOT, "scanner_results_ma.csv")
    
    if os.path.exists(atr_csv):
        try:
            df = pd.read_csv(atr_csv)
            results["atr"] = df.to_dict("records")
        except Exception:
            pass
    
    if os.path.exists(ma_csv):
        try:
            df = pd.read_csv(ma_csv)
            results["ma"] = df.to_dict("records")
        except Exception:
            pass
    
    from datetime import datetime
    results["timestamp"] = datetime.now().isoformat()
    
    with open(os.path.join(ALPACA_ROOT, "scan_results.json"), "w") as f:
        json.dump(results, f)

# ═══════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════

@router.get("/health", response_model=AlpacaHealthResponse)
async def alpaca_health():
    return {"status": "healthy", "trading_mode": get_trading_mode()}

@router.get("/settings", response_model=TradingModeResponse)
async def get_settings():
    return {"trading_mode": get_trading_mode()}

@router.post("/settings/trading-mode", response_model=TradingModeUpdateResponse)
async def update_trading_mode(mode: str):
    if mode not in ["paper", "live"]:
        raise HTTPException(status_code=400, detail="Mode must be 'paper' or 'live'")
    normalized = set_trading_mode(mode)
    return {"message": f"Trading mode set to {normalized}", "mode": normalized}

# ── Popular US stocks for search typeahead ──
_STOCK_LIST = [
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"), ("AMZN", "Amazon"),
    ("NVDA", "NVIDIA"), ("META", "Meta Platforms"), ("TSLA", "Tesla"), ("BRK.B", "Berkshire Hathaway"),
    ("JPM", "JPMorgan Chase"), ("V", "Visa"), ("JNJ", "Johnson & Johnson"), ("WMT", "Walmart"),
    ("PG", "Procter & Gamble"), ("MA", "Mastercard"), ("UNH", "UnitedHealth"), ("HD", "Home Depot"),
    ("DIS", "Walt Disney"), ("BAC", "Bank of America"), ("XOM", "Exxon Mobil"), ("PFE", "Pfizer"),
    ("CSCO", "Cisco"), ("VZ", "Verizon"), ("INTC", "Intel"), ("CMCSA", "Comcast"),
    ("KO", "Coca-Cola"), ("PEP", "PepsiCo"), ("ABT", "Abbott Labs"), ("MRK", "Merck"),
    ("AVGO", "Broadcom"), ("COST", "Costco"), ("TMO", "Thermo Fisher"), ("NKE", "Nike"),
    ("ORCL", "Oracle"), ("ACN", "Accenture"), ("MCD", "McDonald's"), ("LLY", "Eli Lilly"),
    ("AMD", "AMD"), ("ADBE", "Adobe"), ("CRM", "Salesforce"), ("QCOM", "Qualcomm"),
    ("TXN", "Texas Instruments"), ("NFLX", "Netflix"), ("TMUS", "T-Mobile"), ("AMGN", "Amgen"),
    ("HON", "Honeywell"), ("IBM", "IBM"), ("CAT", "Caterpillar"), ("BA", "Boeing"),
    ("GE", "GE"), ("LOW", "Lowe's"), ("INTU", "Intuit"), ("SBUX", "Starbucks"),
    ("GS", "Goldman Sachs"), ("BLK", "BlackRock"), ("GILD", "Gilead"), ("MMM", "3M"),
    ("ISRG", "Intuitive Surgical"), ("MDLZ", "Mondelez"), ("ADP", "ADP"), ("BKNG", "Booking"),
    ("SYK", "Stryker"), ("VRTX", "Vertex Pharma"), ("REGN", "Regeneron"), ("PANW", "Palo Alto"),
    ("LRCX", "Lam Research"), ("KLAC", "KLA Corp"), ("SNPS", "Synopsys"), ("CDNS", "Cadence"),
    ("ABNB", "Airbnb"), ("CRWD", "CrowdStrike"), ("FTNT", "Fortinet"), ("DDOG", "Datadog"),
    ("ZS", "Zscaler"), ("SNOW", "Snowflake"), ("NET", "Cloudflare"), ("BILL", "Bill.com"),
    ("COIN", "Coinbase"), ("HOOD", "Robinhood"), ("SOFI", "SoFi"), ("PLTR", "Palantir"),
    ("RIVN", "Rivian"), ("LCID", "Lucid"), ("NIO", "NIO"), ("XPEV", "XPeng"),
    ("SQ", "Block"), ("PYPL", "PayPal"), ("SHOP", "Shopify"), ("MELI", "MercadoLibre"),
    ("SE", "Sea Limited"), ("UBER", "Uber"), ("LYFT", "Lyft"), ("DASH", "DoorDash"),
    ("RBLX", "Roblox"), ("U", "Unity Software"), ("TTWO", "Take-Two"), ("EA", "Electronic Arts"),
    ("ATVI", "Activision"), ("SPOT", "Spotify"), ("ROKU", "Roku"), ("PINS", "Pinterest"),
    ("SNAP", "Snap"), ("TTD", "Trade Desk"), ("DKNG", "DraftKings"), ("PENN", "Penn Entertainment"),
    ("MGM", "MGM Resorts"), ("LVS", "Las Vegas Sands"), ("WYNN", "Wynn Resorts"),
    ("F", "Ford"), ("GM", "General Motors"), ("TM", "Toyota"), ("STLA", "Stellantis"),
    ("AAL", "American Airlines"), ("DAL", "Delta Air Lines"), ("UAL", "United Airlines"),
    ("LUV", "Southwest Airlines"), ("CCL", "Carnival"), ("RCL", "Royal Caribbean"),
    ("SPY", "S&P 500 ETF"), ("QQQ", "Nasdaq 100 ETF"), ("IWM", "Russell 2000 ETF"),
    ("DIA", "Dow Jones ETF"), ("ARKK", "ARK Innovation ETF"), ("XLF", "Financial ETF"),
    ("XLE", "Energy ETF"), ("XLK", "Technology ETF"), ("XLV", "Healthcare ETF"),
    ("GLD", "Gold ETF"), ("SLV", "Silver ETF"), ("USO", "Oil ETF"),
    ("VTI", "Total Stock Market ETF"), ("VOO", "Vanguard S&P 500"), ("SCHD", "Schwab Dividend ETF"),
    ("ARM", "Arm Holdings"), ("SMCI", "Super Micro"), ("MSTR", "MicroStrategy"),
    ("TSM", "Taiwan Semi"), ("ASML", "ASML"), ("MU", "Micron"), ("ON", "ON Semiconductor"),
    ("AMAT", "Applied Materials"), ("MRVL", "Marvell"), ("DELL", "Dell Technologies"),
    ("HPQ", "HP Inc"), ("WBD", "Warner Bros"), ("PARA", "Paramount"), ("NCLH", "Norwegian Cruise"),
    ("CVX", "Chevron"), ("COP", "ConocoPhillips"), ("OXY", "Occidental"), ("DVN", "Devon Energy"),
    ("SLB", "Schlumberger"), ("HAL", "Halliburton"), ("EOG", "EOG Resources"),
    ("C", "Citigroup"), ("WFC", "Wells Fargo"), ("MS", "Morgan Stanley"), ("SCHW", "Schwab"),
    ("USB", "US Bancorp"), ("AXP", "American Express"), ("COF", "Capital One"),
    ("T", "AT&T"), ("CHTR", "Charter Comm"), ("AMT", "American Tower"),
    ("CCI", "Crown Castle"), ("EQIX", "Equinix"), ("PLD", "Prologis"),
    ("O", "Realty Income"), ("PSA", "Public Storage"), ("WELL", "Welltower"),
    ("UPS", "UPS"), ("FDX", "FedEx"), ("DE", "Deere"), ("RTX", "RTX Corp"),
    ("LMT", "Lockheed Martin"), ("NOC", "Northrop"), ("GD", "General Dynamics"),
]

def _search_local(q_upper: str) -> list:
    """Search the local hardcoded stock list (fallback)."""
    matches = []
    for sym, name in _STOCK_LIST:
        if sym.startswith(q_upper):
            matches.append({"symbol": sym, "name": name, "priority": 0})
        elif q_upper in name.upper():
            matches.append({"symbol": sym, "name": name, "priority": 1})
    matches.sort(key=lambda x: (x["priority"], x["symbol"]))
    return [{"symbol": m["symbol"], "name": m["name"]} for m in matches[:10]]

# Cache all Alpaca assets in memory for fast search (loaded lazily)
_alpaca_assets_cache: list = []
_alpaca_assets_loaded = False

async def _load_alpaca_assets():
    """Load all tradeable assets from Alpaca API into memory cache."""
    global _alpaca_assets_cache, _alpaca_assets_loaded
    import httpx
    api_key, api_secret, _ = get_alpaca_credentials()

    if not api_key or not api_secret:
        print("[Search] No Alpaca API keys found for current trading mode")
        return
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://paper-api.alpaca.markets/v2/assets",
                headers={
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": api_secret,
                },
                params={"status": "active", "asset_class": "us_equity"},
            )
            if resp.status_code == 200:
                assets = resp.json()
                _alpaca_assets_cache = [
                    (a["symbol"], a.get("name", a["symbol"]))
                    for a in assets if a.get("tradable", False)
                ]
                _alpaca_assets_loaded = True
                print(f"[Search] Loaded {len(_alpaca_assets_cache)} tradeable assets from Alpaca")
    except Exception as e:
        print(f"[Search] Failed to load Alpaca assets: {e}")

@router.get("/search", response_model=SearchResponse)
async def search_tickers(q: str = ""):
    """Search tickers by symbol or company name. Queries ALL tradeable US equities."""
    if not q or len(q) < 1:
        return {"results": []}
    
    q_upper = q.upper()
    
    # Lazy-load full asset list on first search
    if not _alpaca_assets_loaded:
        await _load_alpaca_assets()
    
    # Search the full asset cache if available
    if _alpaca_assets_loaded and _alpaca_assets_cache:
        matches = []
        for sym, name in _alpaca_assets_cache:
            if sym.upper().startswith(q_upper):
                matches.append({"symbol": sym, "name": name, "priority": 0})
            elif q_upper in name.upper():
                matches.append({"symbol": sym, "name": name, "priority": 1})
        matches.sort(key=lambda x: (x["priority"], len(x["symbol"]), x["symbol"]))
        return {"results": [{"symbol": m["symbol"], "name": m["name"]} for m in matches[:10]]}
    
    # Fallback to local list
    return {"results": _search_local(q_upper)}


# ── Scanner ──

@router.post("/scanner/start", response_model=ScannerStartResponse)
async def start_scanner(request: ScannerRequest):
    """Start the background market scanner using scanner_worker.py"""
    global _scanner_running, _scanner_stop_requested, _scanner_job_id
    job_store.fail_stale_jobs(max_age_seconds=3 * 60 * 60)
    active = job_store.get_active_job(domain="alpaca", kind="scanner")
    if active:
        _scanner_running = True
        _scanner_job_id = active.get("id")
        return {"message": "Scanner already running", "status": "running", "job_id": active.get("id")}
    if _scanner_running:
        return {"message": "Scanner already running", "status": "running", "job_id": _scanner_job_id}

    job = job_store.create_job(
        domain="alpaca",
        kind="scanner",
        metadata={"strategy": request.strategy},
    )
    _scanner_stop_requested = False
    thread = threading.Thread(target=_run_scanner_background, args=(job["id"],), daemon=True)
    thread.start()
    return {"message": "Scanner started", "strategy": request.strategy, "job_id": job["id"]}


@router.post("/scanner/stop", response_model=ScannerStopResponse)
async def stop_scanner():
    """Request graceful scanner cancellation."""
    global _scanner_running, _scanner_stop_requested
    if not _scanner_running:
        return {"message": "Scanner is not running", "stopping": False}
    _scanner_stop_requested = True
    if _scanner_job_id:
        job_store.touch(_scanner_job_id, message="Stop requested by user")
    return {"message": "Scanner stop requested", "stopping": True}

@router.get("/scanner/status", response_model=ScannerStatusResponse)
async def get_scanner_status():
    """Read scan_status.json written by scanner_worker."""
    try:
        status_file = os.path.join(ALPACA_ROOT, "scan_status.json")
        if os.path.exists(status_file):
            with open(status_file, "r") as f:
                data = json.load(f)
                data["is_running"] = _scanner_running
                data["stop_requested"] = _scanner_stop_requested
                if _scanner_job_id:
                    job = job_store.get_job(_scanner_job_id)
                    if job:
                        data["job_id"] = job.get("id")
                        data["job_status"] = job.get("status")
                return data
        fallback = {"status": "idle", "progress": 0, "is_running": _scanner_running, "stop_requested": _scanner_stop_requested}
        if _scanner_job_id:
            job = job_store.get_job(_scanner_job_id)
            if job:
                fallback["job_id"] = job.get("id")
                fallback["job_status"] = job.get("status")
        return fallback
    except Exception as e:
        return {"status": "error", "error": str(e), "is_running": False, "stop_requested": _scanner_stop_requested, "job_id": _scanner_job_id}

@router.get("/scanner/results", response_model=ScannerResultsResponse)
async def get_scanner_results():
    """Return scanner results from JSON or CSV files."""
    try:
        # Try JSON first
        json_file = os.path.join(ALPACA_ROOT, "scan_results.json")
        if os.path.exists(json_file):
            with open(json_file, "r") as f:
                return json.load(f)
        
        # Fallback: read CSVs directly
        _save_results_as_json()
        if os.path.exists(json_file):
            with open(json_file, "r") as f:
                return json.load(f)
        
        return {"atr": [], "ma": [], "timestamp": None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Analysis ──

@router.post("/analysis", response_model=AnalysisResponse)
async def analyze_ticker(request: AnalysisRequest):
    """Full technical analysis + ML prediction for a ticker."""
    try:
        with _alpaca_cwd():
            from technical_analyst import TechnicalAnalyst
            from ml_engine import TradePredictor

            analyst = TechnicalAnalyst()
            predictor = TradePredictor()

            # Get technical analysis
            analysis = analyst.analyze_stock(request.ticker)

            # Get AI confidence scores
            ai_scores = predictor.get_trade_confidence(request.ticker)

            # Generate trade setups
            setups = []
            try:
                setups_result = analyst.generate_trade_setups(analysis)
                if setups_result is not None:
                    if hasattr(setups_result, 'to_dict'):
                        setups = setups_result.to_dict("records")
                    elif isinstance(setups_result, list):
                        setups = setups_result
            except Exception as e:
                print(f"Setup generation error: {e}")
        
        # Serialize ALL objects (convert numpy/pandas types)
        clean_analysis = _clean_for_json(analysis)
        clean_scores = _clean_for_json(ai_scores)
        clean_setups = _clean_for_json(setups)
        
        return {
            "ticker": request.ticker,
            "analysis": clean_analysis,
            "ai_scores": clean_scores,
            "setups": clean_setups,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════
# Phase 5: ML Prediction Widget
# ═══════════════════════════════

class PredictRequest(BaseModel):
    ticker: str


@router.post("/predict", response_model=PredictResponse)
async def predict_ticker(request: PredictRequest):
    """Lightweight ML prediction: dual-brain XGBoost confidence scores."""
    try:
        with _alpaca_cwd():
            from ml_engine import TradePredictor
            predictor = TradePredictor()
            scores = predictor.get_trade_confidence(request.ticker)

        composite = scores.get("composite", 50.0)
        signal = "BULLISH" if composite >= 65 else "BEARISH" if composite <= 35 else "NEUTRAL"

        # Notify if high conviction
        if composite >= 75:
            from services.notification_manager import send_toast
            await send_toast(
                title=f"Strong Buy Signal: {request.ticker}",
                message=f"ML Confidence: {composite:.1f}%",
                type="success"
            )
        elif composite <= 25:
             from services.notification_manager import send_toast
             await send_toast(
                title=f"Strong Sell Signal: {request.ticker}",
                message=f"ML Confidence: {composite:.1f}%",
                type="error"
            )

        return {
            "ticker": request.ticker,
            "clean_win": scores.get("clean", 50.0),
            "eventual_win": scores.get("eventual", 50.0),
            "composite": composite,
            "signal": signal,
            "model_loaded": predictor.is_trained,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ticker": request.ticker,
            "clean_win": 50.0,
            "eventual_win": 50.0,
            "composite": 50.0,
            "signal": "UNAVAILABLE",
            "model_loaded": False,
            "error": str(e),
        }


# ── Trade Execution ──

@router.post("/trade/execute", response_model=TradeExecuteResponse)
async def execute_trade(request: TradeRequest):
    """Execute a trade via Alpaca (bracket order with stop loss + take profit)."""
    try:
        with _alpaca_cwd():
            from execution_engine import ExecutionEngine

            engine = ExecutionEngine()
            trade_details = {
                "Symbol": request.symbol,
                "Entry": request.entry,
                "Stop_Loss": request.stop_loss,
                "Target": request.target,
                "Qty": request.qty,
                "Type": request.setup_type,
                "Trailing_Stop": request.trailing_stop,
                "Use_Kelly": request.use_kelly,
            }
            result = engine.execute_trade(trade_details=trade_details)
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/order/simple", response_model=OrderResponse)
async def place_simple_order(request: SimpleOrderRequest):
    """Place a simple market or limit order."""
    try:
        with _alpaca_cwd():
            from execution_engine import ExecutionEngine
            engine = ExecutionEngine()

            if not engine.client:
                raise HTTPException(status_code=400, detail="Alpaca API not configured")

            from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            side = OrderSide.BUY if request.side.lower() == "buy" else OrderSide.SELL
            tif_map = {"day": TimeInForce.DAY, "gtc": TimeInForce.GTC, "ioc": TimeInForce.IOC}
            tif = tif_map.get(request.time_in_force.lower(), TimeInForce.DAY)

            if request.order_type.lower() == "limit" and request.limit_price:
                order_data = LimitOrderRequest(
                    symbol=request.symbol, qty=request.qty,
                    side=side, time_in_force=tif, limit_price=request.limit_price
                )
            else:
                order_data = MarketOrderRequest(
                    symbol=request.symbol, qty=request.qty,
                    side=side, time_in_force=tif
                )

            order = engine.client.submit_order(order_data)
        return {
            "success": True,
            "order": {"id": str(order.id), "status": str(order.status),
                      "symbol": order.symbol, "qty": str(order.qty),
                      "side": str(order.side), "type": str(order.order_type)}
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/order/bracket", response_model=OrderResponse)
async def place_bracket_order(request: BracketOrderRequest):
    """Place a bracket order with stop loss and take profit."""
    try:
        with _alpaca_cwd():
            from execution_engine import ExecutionEngine
            engine = ExecutionEngine()

            if not engine.client:
                raise HTTPException(status_code=400, detail="Alpaca API not configured")

            from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

            side = OrderSide.BUY if request.side.lower() == "buy" else OrderSide.SELL
            tif_map = {"day": TimeInForce.DAY, "gtc": TimeInForce.GTC}
            tif = tif_map.get(request.time_in_force.lower(), TimeInForce.DAY)

            order_params = dict(
                symbol=request.symbol, qty=request.qty,
                side=side, time_in_force=tif, order_class=OrderClass.BRACKET,
                take_profit={"limit_price": request.take_profit},
                stop_loss={"stop_price": request.stop_loss}
            )

            if request.limit_price:
                order_data = LimitOrderRequest(limit_price=request.limit_price, **order_params)
            else:
                order_data = MarketOrderRequest(**order_params)

            order = engine.client.submit_order(order_data)
        return {
            "success": True,
            "order": {"id": str(order.id), "status": str(order.status),
                      "symbol": order.symbol, "qty": str(order.qty),
                      "side": str(order.side), "type": "bracket",
                      "stop_loss": request.stop_loss,
                      "take_profit": request.take_profit}
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/portfolio", response_model=PortfolioResponse)
async def get_portfolio():
    """Get current Alpaca account + positions."""
    now = time.time()
    cached = _portfolio_cache.get("data")
    if cached and now - float(_portfolio_cache.get("ts", 0.0)) < _portfolio_cache_ttl_sec:
        return cached

    def _fetch_sync():
        try:
            from services.crypto.bot import current_crypto_config
            cfg = current_crypto_config()
            if str(cfg.get("active_exchange", "")).lower() == "kraken":
                from services.crypto import market_data
                acct = market_data.get_account_summary()
                pos_list = market_data.get_crypto_positions() or []
                
                mapped_pos = []
                for p in pos_list:
                    mapped_pos.append({
                        "symbol": p.get("symbol", ""),
                        "qty": p.get("qty", 0.0),
                        "avg_entry": p.get("avg_entry_price", 0.0),
                        "current_price": p.get("current_price", 0.0),
                        "market_value": p.get("market_value", 0.0),
                        "unrealized_pl": p.get("unrealized_pl", 0.0),
                        "unrealized_plpc": p.get("unrealized_plpc", 0.0) * 100.0,
                        "side": p.get("side", "long")
                    })
                
                return {
                    "cash": float(acct.get("cash", 0.0)),
                    "portfolio_value": float(acct.get("portfolio_value", 0.0)),
                    "buying_power": float(acct.get("buying_power", 0.0)),
                    "equity": float(acct.get("equity", 0.0)),
                    "trading_mode": "live",
                    "positions": mapped_pos,
                }
        except Exception as e:
            print(f"Kraken Portfolio Fetch Failed: {e}")
            pass

        with _alpaca_cwd():
            from execution_engine import ExecutionEngine
            engine = ExecutionEngine()

            if not engine.client:
                return {
                    "cash": 0, "portfolio_value": 0, "buying_power": 0, "equity": 0,
                    "positions": [], "trading_mode": get_trading_mode(),
                    "error": "API keys not configured"
                }

            account = engine.client.get_account()
            positions = engine.client.get_all_positions()

        return {
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "buying_power": float(account.buying_power),
            "equity": float(account.equity),
            "trading_mode": get_trading_mode(),
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "avg_entry": float(p.avg_entry_price),
                    "current_price": float(p.current_price),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc) * 100,
                    "side": str(p.side),
                }
                for p in positions
            ],
        }

    try:
        payload = await asyncio.to_thread(_fetch_sync)
        _portfolio_cache["data"] = payload
        _portfolio_cache["ts"] = now
        return payload
    except Exception as e:
        if not _is_auth_error(e):
            import traceback
            traceback.print_exc()
        mode = get_trading_mode()
        detail = str(e)
        if _is_auth_error(e):
            detail = f"Alpaca authentication failed for {mode} mode. Check the {mode.upper()} API key/secret pair."
        return {
            "cash": 0, "portfolio_value": 0, "buying_power": 0, "equity": 0,
            "positions": [], "trading_mode": mode,
            "error": detail
        }

@router.post("/portfolio/close", response_model=ClosePositionResponse)
async def close_position(request: ClosePositionRequest):
    """Close a position by symbol."""
    try:
        with _alpaca_cwd():
            from execution_engine import ExecutionEngine
            engine = ExecutionEngine()

            if not engine.client:
                raise HTTPException(status_code=500, detail="API keys not configured")

            engine.client.close_position(request.symbol)
        
        return {"success": True, "message": f"Closed position: {request.symbol}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/portfolio/history", response_model=PortfolioHistoryResponse)
async def get_portfolio_history(timeframe: str = "1M"):
    """Return portfolio equity history for charting."""
    try:
        with _alpaca_cwd():
            from execution_engine import ExecutionEngine
            engine = ExecutionEngine()

            if not engine.client:
                return {"timestamps": [], "equity": [], "profit_loss": [], "total_return_pct": 0, "total_return_dollar": 0}

            # Map user-facing timeframe to Alpaca API params
            tf_map = {
                "1W": ("1W", "1D"),
                "1M": ("1M", "1D"),
                "3M": ("3M", "1D"),
                "6M": ("6M", "1D"),
                "1Y": ("1A", "1D"),
                "ALL": ("all", "1D"),
            }
            period, interval = tf_map.get(timeframe.upper(), ("1M", "1D"))

            from alpaca.trading.requests import GetPortfolioHistoryRequest
            req = GetPortfolioHistoryRequest(period=period, timeframe=interval)
            history = engine.client.get_portfolio_history(req)
        
        timestamps = [t for t in (history.timestamp or [])]
        equity = [float(e) for e in (history.equity or [])]
        pl = [float(p) for p in (history.profit_loss or [])]
        
        total_return_dollar = sum(pl) if pl else 0
        start_equity = equity[0] if equity else 0
        total_return_pct = ((equity[-1] - start_equity) / start_equity * 100) if len(equity) > 1 and start_equity else 0
        
        return {
            "timestamps": timestamps,
            "equity": equity,
            "profit_loss": pl,
            "total_return_pct": round(total_return_pct, 2),
            "total_return_dollar": round(total_return_dollar, 2),
            "timeframe": timeframe,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"timestamps": [], "equity": [], "profit_loss": [], "total_return_pct": 0, "total_return_dollar": 0, "error": str(e)}


# ══════════════════════════════════════════════════════
# Phase 1A: Chart Data
# ══════════════════════════════════════════════════════

@router.get("/chart-data", response_model=ChartDataResponse)
async def get_chart_data(ticker: str, period: str = "3mo", interval: str = "1d"):
    """Return OHLCV + technical indicators for charting."""
    def _build_chart_data(_ticker: str, _period: str, _interval: str) -> Dict[str, Any]:
        import yfinance as yf
        import pandas as pd
        import pandas_ta as ta

        df = yf.download(_ticker.upper(), period=_period, interval=_interval, progress=False)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for {_ticker}")

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

        # Calculate indicators
        df["SMA_20"] = ta.sma(df["Close"], length=20)
        df["SMA_50"] = ta.sma(df["Close"], length=50)
        df["RSI_14"] = ta.rsi(df["Close"], length=14)

        bb = ta.bbands(df["Close"], length=20)
        if bb is not None:
            df["BB_Upper"] = bb.iloc[:, 0]
            df["BB_Mid"] = bb.iloc[:, 1]
            df["BB_Lower"] = bb.iloc[:, 2]

        # ATR
        df["H-L"] = df["High"] - df["Low"]
        df["H-PC"] = abs(df["High"] - df["Close"].shift(1))
        df["L-PC"] = abs(df["Low"] - df["Close"].shift(1))
        df["TR"] = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
        df["ATR_14"] = df["TR"].rolling(window=14).mean()

        # Build response
        candles = []
        volumes = []
        sma20 = []
        sma50 = []
        rsi = []
        bb_upper = []
        bb_lower = []

        for idx, row in df.iterrows():
            ts = int(idx.timestamp())
            candles.append({
                "time": ts,
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
            })
            volumes.append({"time": ts, "value": int(row["Volume"]),
                            "color": "rgba(38,166,154,0.5)" if row["Close"] >= row["Open"] else "rgba(239,83,80,0.5)"})

            if pd.notna(row.get("SMA_20")):
                sma20.append({"time": ts, "value": round(float(row["SMA_20"]), 2)})
            if pd.notna(row.get("SMA_50")):
                sma50.append({"time": ts, "value": round(float(row["SMA_50"]), 2)})
            if pd.notna(row.get("RSI_14")):
                rsi.append({"time": ts, "value": round(float(row["RSI_14"]), 2)})
            if pd.notna(row.get("BB_Upper")):
                bb_upper.append({"time": ts, "value": round(float(row["BB_Upper"]), 2)})
            if pd.notna(row.get("BB_Lower")):
                bb_lower.append({"time": ts, "value": round(float(row["BB_Lower"]), 2)})

        return {
            "ticker": _ticker.upper(),
            "candles": candles,
            "volumes": volumes,
            "sma20": sma20,
            "sma50": sma50,
            "rsi": rsi,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
        }

    try:
        return await asyncio.to_thread(_build_chart_data, ticker, period, interval)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════
# Phase 2A: Backtester
# ══════════════════════════════════════════════════════

class BacktestRequest(BaseModel):
    ticker: str
    strategy: str = "Aggressive (Momentum)"
    investment: float = 10000.0

@router.post("/backtest", response_model=BacktestResponse)
async def run_backtest(request: BacktestRequest):
    """Run backtest and return trades + equity curve."""
    try:
        with _alpaca_cwd():
            from backtest_engine import Backtester
            bt = Backtester(request.ticker.upper(), request.strategy, request.investment)
            results = bt.run()

        if results is None:
            return {"error": "No data available for backtesting"}

        return _clean_for_json({
            "total_trades": results["Total Trades"],
            "win_rate": results["Win Rate"],
            "profit_factor": results["Profit Factor"],
            "total_return": results["Total Return"],
            "trades": results["Trades"].to_dict("records") if not results["Trades"].empty else [],
            "equity_curve": results["Equity"].to_dict("records") if not results["Equity"].empty else [],
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════
# Phase 2B: Risk Calculator
# ══════════════════════════════════════════════════════

class RiskCalcRequest(BaseModel):
    account_balance: float
    risk_percent: float = 0.01
    entry_price: float
    stop_price: float

@router.post("/risk-calculator", response_model=RiskCalculatorResponse)
async def risk_calculator(request: RiskCalcRequest):
    """Calculate position size using the 1% rule."""
    try:
        with _alpaca_cwd():
            from risk_calculator import calculate_position_size, validate_position_size

            sizing = calculate_position_size(
                request.account_balance, request.risk_percent,
                request.entry_price, request.stop_price
            )
            validation = validate_position_size(
                sizing["shares"], request.entry_price, request.account_balance
            )
        return {"sizing": sizing, "validation": validation}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════
# Phase 2C: Earnings Monitor
# ══════════════════════════════════════════════════════

@router.get("/earnings", response_model=EarningsResponse)
async def check_earnings(ticker: str):
    """Check earnings proximity for a stock."""
    try:
        with _alpaca_cwd():
            from earnings_monitor import check_earnings_risk
            result = check_earnings_risk(ticker.upper())
        # Convert datetime to string for JSON
        if result.get("next_earnings"):
            result["next_earnings"] = result["next_earnings"].strftime("%Y-%m-%d")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════
# Quote (lightweight price + change)
# ══════════════════════════════════════════════════════

@router.get("/quote", response_model=QuoteResponse)
async def get_quote(ticker: str):
    """Fast current price and daily change % for a ticker."""
    def _fetch(t: str):
        import yfinance as yf
        tk = yf.Ticker(t.upper())
        info = tk.fast_info
        price = float(info.get("lastPrice", 0) or info.get("last_price", 0))
        prev = float(info.get("previousClose", 0) or info.get("previous_close", 0))
        change = ((price - prev) / prev * 100) if prev else 0.0
        return {"ticker": t.upper(), "price": round(price, 2), "change_pct": round(change, 2), "prev_close": round(prev, 2)}
    try:
        return await asyncio.to_thread(_fetch, ticker)
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}


# ══════════════════════════════════════════════════════
# Phase 3A: Top Movers
# ══════════════════════════════════════════════════════

@router.get("/top-movers", response_model=TopMoversResponse)
async def top_movers(force: bool = False):
    """Get top movers with AI scores."""
    now = time.time()
    cached = _top_movers_cache.get("data")
    if not force and cached and now - float(_top_movers_cache.get("ts", 0.0)) < _top_movers_cache_ttl_sec:
        return cached

    def _fetch_sync():
        with _alpaca_cwd():
            from top_movers import get_top_movers, analyze_top_movers
            tickers = get_top_movers()
            results_df = analyze_top_movers(tickers[:10])  # Limit to 10 for speed
        if results_df.empty:
            return {"movers": []}
        return {"movers": _clean_for_json(results_df.to_dict("records"))}

    try:
        payload = await asyncio.to_thread(_fetch_sync)
        _top_movers_cache["data"] = payload
        _top_movers_cache["ts"] = now
        return payload
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════
# Crypto Trading + Bot
# ══════════════════════════════════════════════════════

@router.get("/crypto/account")
async def crypto_account():
    try:
        from services.crypto import get_account
        return await asyncio.wait_for(asyncio.to_thread(get_account), timeout=8.0)
    except asyncio.TimeoutError:
        from services.crypto import market_data
        cached = market_data.get_cached_account_summary()
        if cached:
            return cached
        return {
            "cash": 0.0,
            "equity": 0.0,
            "buying_power": 0.0,
            "portfolio_value": 0.0,
            "status": "timeout",
            "mode": _crypto_credential_mode(),
            "error": "Account request timed out.",
        }
    except Exception as e:
        if _is_auth_error(e):
            return {
                "cash": 0.0,
                "equity": 0.0,
                "buying_power": 0.0,
                "portfolio_value": 0.0,
                "status": "unauthorized",
                "mode": _crypto_credential_mode(),
                "error": str(e),
            }
        if _is_network_error(e):
            return {
                "cash": 0.0,
                "equity": 0.0,
                "buying_power": 0.0,
                "portfolio_value": 0.0,
                "status": "unreachable",
                "mode": _crypto_credential_mode(),
                "error": str(e),
            }
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/assets")
async def crypto_assets(limit: int = 60):
    try:
        from services.crypto import list_assets
        items = await asyncio.to_thread(list_assets, limit)
        return {"items": items, "count": len(items)}
    except Exception as e:
        if _is_auth_error(e):
            return {
                "items": [],
                "count": 0,
                "status": "unauthorized",
                "mode": _crypto_credential_mode(),
                "error": str(e),
            }
        if _is_network_error(e):
            return {
                "items": [],
                "count": 0,
                "status": "unreachable",
                "mode": _crypto_credential_mode(),
                "error": str(e),
            }
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/positions")
async def crypto_positions():
    try:
        from services.crypto import get_positions
        items = await asyncio.wait_for(asyncio.to_thread(get_positions), timeout=8.0)
        return {"items": items, "count": len(items)}
    except asyncio.TimeoutError:
        from services.crypto import market_data
        items = market_data.get_cached_crypto_positions()
        if items:
            return {"items": items, "count": len(items), "status": "cached"}
        return {
            "items": [],
            "count": 0,
            "status": "timeout",
            "mode": _crypto_credential_mode(),
            "error": "Positions request timed out.",
        }
    except Exception as e:
        if _is_auth_error(e):
            return {
                "items": [],
                "count": 0,
                "status": "unauthorized",
                "mode": _crypto_credential_mode(),
                "error": str(e),
            }
        if _is_network_error(e):
            return {
                "items": [],
                "count": 0,
                "status": "unreachable",
                "mode": _crypto_credential_mode(),
                "error": str(e),
            }
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/actions")
async def crypto_actions(
    limit: int = 200,
    before_ts: Optional[int] = None,
    status: str = "",
    symbol: str = "",
    search: str = "",
):
    try:
        from services.crypto import store as crypto_store
        items = await crypto_store.list_actions_page(
            limit=limit,
            before_ts=before_ts,
            status=status,
            symbol=symbol,
            search=search,
        )
        next_before_ts = items[-1]["ts"] if items else None
        return {"items": items, "count": len(items), "next_before_ts": next_before_ts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/crypto/hub")
async def crypto_hub_data():
    """
    Aggregates data for the Top 30 Crypto Hub.
    Combines: Active ML scores, Narratives, Suppressed/Blocked counts, and Edge Weights.
    """
    try:
        from services.crypto.bot import store as crypto_store
        from services.crypto.market_data import get_trading_client
        import time
        from collections import defaultdict
        
        # 1. Get recent decision traces (block tracking) over last 24h
        now_ms = int(time.time() * 1000)
        day_ago = now_ms - (24 * 3600 * 1000)
        
        traces = []
        try:
            # Manually query just the block stats to avoid heavy ORM
            con = crypto_store._connect()
            rows = con.execute(
                "SELECT symbol, submitted, block_reason FROM brain_decision_trace WHERE ts >= ?",
                (day_ago,)
            ).fetchall()
            traces = rows
            con.close()
        except Exception as e:
            pass

        block_counts = defaultdict(int)
        for r in traces:
            if not r["submitted"]:
                sym = str(r["symbol"] or "").replace("/USD", "").replace("/", "")
                block_counts[sym] += 1

        # 2. Get active ML universe rankings and edge scores
        rankings = []
        try:
            con = crypto_store._connect()
            latest = con.execute("SELECT id FROM universe_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
            if latest:
                r_rows = con.execute(
                    "SELECT symbol, bucket, historical_edge_score, event_score FROM universe_rankings WHERE snapshot_id = ?",
                    (latest["id"],)
                ).fetchall()
                rankings = r_rows
            con.close()
        except:
            pass
            
        # 3. Get symbol event states (Narratives & Bias)
        event_states = {}
        try:
            con = crypto_store._connect()
            e_rows = con.execute("SELECT symbol, net_event_score, event_bias, top_event_type, payload_json FROM symbol_event_state").fetchall()
            for r in e_rows:
                sym = str(r["symbol"] or "").replace("/USD", "").replace("/", "")
                event_states[sym] = {
                    "bias": r["event_bias"] or "neutral",
                    "score": float(r["net_event_score"] or 0.0),
                    "top_event": r["top_event_type"] or ""
                }
            con.close()
        except:
            pass

        # Build aggregated symbol cards
        top_coins = []
        base_symbols = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "XRP", "ADA", "DOT", "MATIC"]
        
        # Extract dynamic symbols from rankings
        for r in rankings:
            sym = str(r["symbol"] or "").replace("/USD", "").replace("/", "")
            if sym not in base_symbols:
                base_symbols.append(sym)
                
        # Limit to top 30 mapped coins
        for sym in base_symbols[:30]:
            edge = 0.0
            technical_score = 50.0
            
            for r in rankings:
                r_sym = str(r["symbol"] or "").replace("/USD", "").replace("/", "")
                if r_sym == sym:
                    edge = float(r["historical_edge_score"] or 0.0)
                    technical_score = float(r["event_score"] or 50.0)
                    break
                    
            e_state = event_states.get(sym, {"bias": "neutral", "score": 0.0, "top_event": ""})
            
            top_coins.append({
                "symbol": sym,
                "technical_score": technical_score,
                "conviction_score": technical_score + (edge * 10), # Blend edge into conviction
                "bias": e_state["bias"],
                "edge_weight": edge,
                "blocked_trades_24h": block_counts.get(sym, 0),
                "active_narrative": e_state["top_event"]
            })

        # Sort by conviction score
        top_coins.sort(key=lambda x: x["conviction_score"], reverse=True)

        return {"top_coins": top_coins}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/actions/clear")
async def crypto_actions_clear_all():
    try:
        from services.crypto import clear_recent_actions
        from services.notification_manager import broadcast

        removed = await clear_recent_actions()
        await broadcast(
            "crypto",
            "toast",
            {
                "title": "Crypto Actions Cleared",
                "message": f"Removed {int(removed)} action entries.",
                "type": "warning",
                "duration": 2600,
            },
        )
        return {"success": True, "removed": int(removed)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/bot/status")
async def crypto_bot_status():
    try:
        from services.crypto import get_bot_status
        return await get_bot_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/dashboard/summary")
async def crypto_dashboard_summary(prefer_cached: bool = True):
    try:
        from services.crypto import get_account, get_positions, get_bot_status
        from services.crypto import market_data, store
        from services.crypto.bot import current_crypto_config
        from services.crypto.oracle import get_oracle_status, get_current_oracle_state, get_narrative_status
        from services.crypto.report_generator import get_cached_report

        cached_account = market_data.get_cached_account_summary() if prefer_cached else None
        cached_positions = market_data.get_cached_crypto_positions() if prefer_cached else None
        fast_timeout = 1.5 if prefer_cached else 3.0

        async def _load_account():
            try:
                return await asyncio.wait_for(asyncio.to_thread(get_account), timeout=fast_timeout)
            except Exception:
                return cached_account or market_data.get_cached_account_summary() or {
                    "cash": 0.0,
                    "equity": 0.0,
                    "buying_power": 0.0,
                    "portfolio_value": 0.0,
                    "status": "unavailable",
                }

        async def _load_positions():
            try:
                return await asyncio.wait_for(asyncio.to_thread(get_positions), timeout=fast_timeout)
            except Exception:
                return cached_positions or market_data.get_cached_crypto_positions() or []

        account_data, position_items, bot_status = await asyncio.gather(
            _load_account(),
            _load_positions(),
            get_bot_status(),
        )

        cfg = current_crypto_config() or {}
        recent_actions = store.list_actions_page_sync(limit=120)
        blocked_error_count = sum(1 for row in recent_actions if str(row.get("status", "")).lower() in {"blocked", "error"})
        duplicate_suppression_count = sum(
            1
            for row in recent_actions
            if "duplicate" in str(row.get("reason", "")).lower() or "duplicate" in str(row.get("action_type", "")).lower()
        )
        cache_fallback_count = sum(
            1
            for row in recent_actions
            if any(
                token in str(row.get("reason", "")).lower() or token in str(row.get("action_type", "")).lower()
                for token in ("cache", "fallback", "stale", "fetch_error")
            )
        )
        latest_action = recent_actions[0] if recent_actions else None
        net_unrealized = sum(float(row.get("unrealized_pl", 0.0) or 0.0) for row in position_items)
        total_exposure = sum(abs(float(row.get("market_value", 0.0) or 0.0)) for row in position_items)
        best_position = max(position_items, key=lambda row: float(row.get("unrealized_plpc", 0.0) or 0.0), default=None)
        worst_position = min(position_items, key=lambda row: float(row.get("unrealized_plpc", 0.0) or 0.0), default=None)
        hourly_report = get_cached_report("hourly") or None
        oracle_status = get_oracle_status()
        oracle_state = get_current_oracle_state()
        narrative_status = get_narrative_status()
        runtime = bot_status.get("runtime", {}) if isinstance(bot_status, dict) else {}
        persisted = bot_status.get("persisted", {}) if isinstance(bot_status, dict) else {}
        latest_action_compact = None
        if latest_action:
            latest_action_compact = {
                "id": latest_action.get("id"),
                "ts": latest_action.get("ts"),
                "action_type": latest_action.get("action_type"),
                "symbol": latest_action.get("symbol"),
                "side": latest_action.get("side"),
                "status": latest_action.get("status"),
                "reason": str(latest_action.get("reason", "") or "")[:160],
            }
        hourly_report_compact = None
        if hourly_report:
            hourly_report_compact = {
                "ts": hourly_report.get("ts"),
                "report_type": hourly_report.get("report_type", "hourly"),
                "grade": hourly_report.get("grade"),
                "content": str(hourly_report.get("content", "") or "")[:260],
            }
        bot_status_compact = {
            "runtime": {
                "running": bool(runtime.get("running", False)),
                "halted": bool(runtime.get("halted", False)),
                "last_cycle_at": runtime.get("last_cycle_at"),
                "last_error": runtime.get("last_error"),
                "risk_state": dict((runtime.get("last_status") or {}).get("risk_state") or {}),
                "activity_governor": {
                    "mode": (runtime.get("activity_governor") or {}).get("mode"),
                    "pressure_score": (runtime.get("activity_governor") or {}).get("pressure_score"),
                    "reason_codes": list(((runtime.get("activity_governor") or {}).get("reason_codes") or [])[:3]),
                    "metrics": dict((runtime.get("activity_governor") or {}).get("metrics") or {}),
                    "updated_at_ms": (runtime.get("activity_governor") or {}).get("updated_at_ms"),
                },
                "mode_effectiveness": {
                    "effectiveness_score": (runtime.get("mode_effectiveness") or {}).get("effectiveness_score"),
                    "confidence_score": (runtime.get("mode_effectiveness") or {}).get("confidence_score"),
                    "verdict": (runtime.get("mode_effectiveness") or {}).get("verdict"),
                },
                "effective_risk_profile": dict(runtime.get("effective_risk_profile") or {}),
                "component_calibration": {
                    "weights": dict(((runtime.get("component_calibration") or {}).get("weights") or {})),
                },
                "behavior_state": dict(runtime.get("behavior_state") or {}),
                "last_status": {
                    "effective_thresholds": dict(((runtime.get("last_status") or {}).get("effective_thresholds") or {})),
                    "effective_risk_profile": dict(((runtime.get("last_status") or {}).get("effective_risk_profile") or {})),
                },
            },
            "persisted": {
                "running": bool(persisted.get("running", False)),
                "halted": bool(persisted.get("halted", False)),
                "last_heartbeat": persisted.get("last_heartbeat"),
                "oracle_cached_at_ms": persisted.get("oracle_cached_at_ms"),
                "last_report_ms": persisted.get("last_report_ms"),
                "last_drl_retrain_ms": persisted.get("last_drl_retrain_ms"),
                "is_training": persisted.get("is_training"),
            },
        }

        return {
            "account": account_data,
            "bot_status": bot_status_compact,
            "config_summary": {
                "active_brain": cfg.get("active_brain", "drl_event_fusion"),
                "trading_mode": cfg.get("trading_mode", "offline"),
                "account_mode": cfg.get("account_mode", "paper"),
                "enabled": bool(cfg.get("enabled", True)),
            },
            "positions_summary": {
                "count": len(position_items),
                "net_unrealized": net_unrealized,
                "total_exposure": total_exposure,
                "best_position": best_position,
                "worst_position": worst_position,
            },
            "feed_summary": {
                "count": len(recent_actions),
                "latest": latest_action_compact,
                "blocked_error_count": blocked_error_count,
                "duplicate_suppression_count": duplicate_suppression_count,
                "cache_fallback_count": cache_fallback_count,
            },
            "report_summary": {
                "hourly": hourly_report_compact,
            },
            "oracle_summary": {
                "score": oracle_status.get("score", 1.0),
                "market_regime": oracle_state.get("market_regime", "normal"),
                "summary": str(oracle_status.get("summary", "") or "")[:220],
                "cached_at_ts": oracle_status.get("cached_at_ts", 0),
                "narratives": narrative_status.get("leaders", {}),
            },
            "service_status": background_services.service_status(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/crypto/equity-history")
async def crypto_equity_history(since_ms: int = 0):
    """Fetch historical total equity readings. Default returns everything."""
    try:
        from services.crypto.store import get_equity_history
        history = await get_equity_history(since_ms)
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/crypto/activity-history")
async def crypto_activity_history(since_ms: int = 0, limit: int = 1000):
    """Fetch persisted activity-governor history for charts, reports, and analysis."""
    try:
        from services.crypto.store import get_activity_history
        history = await get_activity_history(since_ms=since_ms, limit=limit)
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/crypto/performance")
async def crypto_performance():
    """Fetch 1h, 24h, 7d performance deltas based on historically snapshot equity."""
    try:
        from services.crypto.store import get_equity_performance
        perf = await get_equity_performance()
        return {"performance": perf}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/crypto/oracle/status")
async def crypto_oracle_status():
    """Returns the current Gemini oracle cache state — score, regime, summary, last error, age."""
    try:
        from services.crypto.oracle import get_oracle_status, get_current_oracle_state, _narrative_leaders, _narrative_cached_at
        from services.crypto.gemini_client import get_gemini_usage
        from services.crypto.bot import current_crypto_config
        import time as _time

        status = get_oracle_status()
        state = get_current_oracle_state()
        usage = get_gemini_usage()

        # Active brain config — read from runtime config, fall back to bot defaults
        try:
            from core.config_manager import get_config
            from services.crypto.bot import DEFAULT_CRYPTO_CONFIG
            cfg = get_config("crypto") or DEFAULT_CRYPTO_CONFIG
        except Exception:
            from services.crypto.bot import DEFAULT_CRYPTO_CONFIG
            cfg = DEFAULT_CRYPTO_CONFIG
        brain_info = {
            "active_brain": cfg.get("active_brain", "drl"),
            "ollama_model": cfg.get("ollama_model", "apexbot"),
            "ollama_think": cfg.get("ollama_think", False),
        }

        # Narrative leaders with cache age
        narrative_age_sec = int(_time.time() - _narrative_cached_at) if _narrative_cached_at else None

        return {
            "oracle": status,
            "derived_state": state,
            "gemini_usage": usage,
            "narratives": _narrative_leaders,
            "narrative_age_sec": narrative_age_sec,
            "brain": brain_info,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/oracle/force-refresh")
async def crypto_oracle_force_refresh():
    """Force an immediate oracle refresh from Gemini, bypassing the 30-min TTL. Use for testing."""
    try:
        from services.crypto.oracle import refresh_oracle_score, get_oracle_status
        import asyncio
        score = await asyncio.to_thread(refresh_oracle_score)
        status = get_oracle_status()
        return {"success": True, "score": score, "oracle": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/report/generate")
async def crypto_report_generate(report_type: str = "daily", dry_run: bool = False):
    """
    Force-generate a bot report on demand. For testing/debugging.
    ?report_type=daily | hourly | weekly | monthly
    ?dry_run=true — Returns raw snapshot+metrics without calling Gemini (tests the data pipeline only).
    """
    try:
        if dry_run:
            from services.crypto.report_generator import (
                _gather_snapshot, _build_metrics, _gather_coin_prices
            )
            snapshot = await asyncio.to_thread(_gather_snapshot)
            metrics = await asyncio.to_thread(_build_metrics, report_type, snapshot)
            coin_prices = await asyncio.to_thread(_gather_coin_prices)
            return {
                "dry_run": True,
                "report_type": report_type,
                "snapshot": snapshot,
                "metrics": metrics,
                "coin_prices": coin_prices,
            }

        from services.crypto.report_generator import generate_report
        result = await asyncio.to_thread(generate_report, report_type)
        if result is None:
            raise HTTPException(status_code=500, detail="Report generation returned None — check logs")
        return {"success": True, "report_type": report_type, "report": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/bot/reports")
async def crypto_bot_reports(report_type: Optional[str] = None):
    """Return latest AI-generated bot reports. Optional ?report_type=hourly|daily|weekly|monthly filter."""
    try:
        from services.crypto.report_generator import get_all_latest_reports, get_cached_report
        if report_type:
            report = get_cached_report(report_type)
            if not report:
                from services.crypto import store
                db = store.get_latest_reports_sync(report_type=report_type, limit=1)
                report = db[0] if db else None
            return {"report": report}
        return {"reports": get_all_latest_reports()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/bot/reports/history")
async def crypto_bot_reports_history(limit: int = 50):
    """Return historical AI-generated bot reports, grouped by type."""
    try:
        from services.crypto.report_generator import get_historical_reports
        history = await asyncio.to_thread(get_historical_reports, limit)
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/crypto/bot/start")
async def crypto_bot_start():
    try:
        from services.crypto import start_bot
        status = await asyncio.to_thread(start_bot)
        services = await background_services.ensure_crypto_runtime_services()
        return {"success": True, "status": status, "services": services}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/bot/stop")
async def crypto_bot_stop():
    try:
        from services.crypto import stop_bot
        status = await asyncio.to_thread(stop_bot)
        await background_services.stop_crypto_runtime_services()
        return {"success": True, "status": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/bot/config")
async def crypto_bot_config():
    try:
        from services.crypto import current_crypto_config
        config = await asyncio.to_thread(current_crypto_config)
        return {"config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/bot/config")
async def crypto_bot_config_update(request: CryptoConfigUpdateRequest):
    try:
        from services.crypto import save_crypto_config
        config = await asyncio.to_thread(save_crypto_config, request.updates or {})
        return {"config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/order")
async def crypto_order(request: CryptoOrderRequest):
    try:
        from services.crypto import place_crypto_order
        from services.crypto import store as crypto_store
        from services.notification_manager import broadcast

        credential_mode = _crypto_credential_mode()
        min_notional = _crypto_min_order_notional()

        order = await asyncio.to_thread(
            place_crypto_order,
            request.symbol,
            request.side,
            request.order_type,
            request.qty,
            request.notional,
            request.limit_price,
            request.stop_price,
            request.time_in_force,
            None,
            credential_mode,
            min_notional,
        )
        await crypto_store.record_action(
            action_type="manual_order",
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            notional=request.notional,
            price=order.get("filled_avg_price") or request.limit_price,
            status="success",
            reason="Manual crypto order submitted from UI.",
            payload=order,
        )
        await broadcast(
            "crypto",
            "toast",
            {
                "title": "Crypto Order Submitted",
                "message": f"{request.symbol.upper()} {request.side.upper()} order submitted.",
                "type": "success",
                "duration": 3200,
                "symbol": request.symbol.upper(),
                "order": order,
            },
        )
        return {"success": True, "order": order}
    except Exception as e:
        if _is_auth_error(e):
            raise HTTPException(status_code=401, detail=str(e))
        if _is_network_error(e):
            raise HTTPException(status_code=503, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/flatten")
async def crypto_flatten_all(mode: str = Query("paper")):
    try:
        from services.crypto.market_data import get_crypto_positions
        from services.crypto.execution import place_crypto_order
        import asyncio
        
        positions = get_crypto_positions(mode=mode)
        results = {"closed": 0, "failures": []}
        
        for p in positions:
            symbol = p.get("symbol", "")
            qty = float(p.get("qty_available", p.get("qty", 0.0)))
            
            if not symbol or qty <= 0:
                continue
                
            try:
                # Issue explicit manual SELL order instead of relying on close_position intent
                order = place_crypto_order(
                    symbol=symbol,
                    side="sell",
                    order_type="market",
                    qty=qty,
                    mode=mode
                )
                results["closed"] += 1
                
                await store.record_action(
                    action_type="manual_liquidation",
                    symbol=symbol,
                    side="sell",
                    qty=qty,
                    status="success",
                    reason="Manual Account Flatten",
                    payload=order
                )
            except Exception as inner_e:
                results["failures"].append({"symbol": symbol, "error": str(inner_e)})
                
        return {"success": True, "result": results}
    except Exception as e:
        if _is_auth_error(e):
            raise HTTPException(status_code=401, detail=str(e))
        if _is_network_error(e):
            raise HTTPException(status_code=503, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/bot/train")
async def crypto_bot_train():
    """Trigger the asynchronous Master ML Training bash script."""
    try:
        script_path = os.path.join(os.path.dirname(BACKEND_ROOT), "train.sh")
        if not os.path.exists(script_path):
            raise HTTPException(status_code=404, detail="train.sh not found in apex root")
            
        print(f"🚀 API Triggering ML Sequence: {script_path}")
        
        # 1. Immediately update the timestamp so the UI Banner disappears instantly
        state_file = os.path.join(BACKEND_ROOT, "data", "runtime_state.json")
        try:
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    state = json.load(f)
            else:
                state = {}
            state["last_ai_training_ms"] = int(time.time() * 1000)
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            with open(state_file, "w") as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            print(f"⚠️ Non-fatal error saving early timestamp: {e}")

        # 2. Run isolated in the background so it doesn't block the API thread
        subprocess.Popen(
            ["bash", script_path], 
            cwd=os.path.dirname(BACKEND_ROOT),
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        return {"success": True, "message": "ML Training Pipeline initiated in the background"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/bot/finetune")
async def crypto_bot_finetune():
    """Trigger a fast fine-tune: loads existing model weights, runs 30k timesteps per coin.
    Skips data collection and feature engineering. Target time: 2-5 minutes."""
    try:
        script_path = os.path.join(os.path.dirname(BACKEND_ROOT), "finetune.sh")
        if not os.path.exists(script_path):
            raise HTTPException(status_code=404, detail="finetune.sh not found in apex root")

        print(f"⚡ API Triggering Fast Fine-Tune: {script_path}")

        # Immediately update timestamp so UI banner resets
        state_file = os.path.join(BACKEND_ROOT, "data", "runtime_state.json")
        try:
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    state = json.load(f)
            else:
                state = {}
            state["last_ai_training_ms"] = int(time.time() * 1000)
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            with open(state_file, "w") as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            print(f"⚠️ Non-fatal error saving fine-tune timestamp: {e}")

        # Run in background — does NOT block the API
        subprocess.Popen(
            ["bash", script_path],
            cwd=os.path.dirname(BACKEND_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"success": True, "message": "Fast Fine-Tune initiated (30k steps, ~2-5 min). Check backend logs."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/bot/reports/generate")
async def crypto_bot_generate_report(report_type: str = "hourly"):
    """Manually trigger a report generation (for testing)."""
    try:
        from services.crypto.report_generator import generate_report
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, generate_report, report_type)
        if result:
            return {"success": True, "report": result}
        raise HTTPException(status_code=500, detail="Report generation returned None — check server logs for traceback")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Report generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/crypto/detail/symbol/overview")
async def crypto_symbol_detail_overview(symbol: str = Query(...)):
    try:
        from services.crypto import market_data, get_bot_status
        from services.crypto import store
        sym = _normalize_crypto_symbol(symbol)
        if not sym:
            raise HTTPException(status_code=400, detail="symbol is required")
        runtime = await get_bot_status()
        positions = market_data.get_crypto_positions() or []
        position = next((p for p in positions if str(p.get("symbol", "")).upper() == sym), None)
        event_state = store.get_symbol_event_state_sync(sym)
        macro = store.get_latest_macro_consensus_sync() or {}
        traces = store.list_brain_decision_traces_sync(limit=10, symbol=sym)
        candidates = store.list_candidate_traces_sync(limit=10, symbol=sym)
        outcomes = store.list_decision_outcomes_sync(limit=10, symbol=sym)
        recent_news = store.get_recent_news_events_sync(limit=10, symbol=sym, active_only=False)
        live_exp = [r for r in store.get_live_experience_sync(limit=50, closed_only=False) if str(r.get("symbol", "")).upper() == sym][:10]
        blocked_count = sum(1 for row in traces if not bool(row.get("submitted", False)))
        quote = market_data.get_latest_quote(sym) or {}
        return {
            "symbol": sym,
            "position": position,
            "quote": quote,
            "event_state": event_state,
            "macro_consensus": macro,
            "runtime": runtime.get("runtime", {}),
            "latest_trace": traces[0] if traces else None,
            "latest_candidate": candidates[0] if candidates else None,
            "latest_outcome": outcomes[0] if outcomes else None,
            "counts": {
                "trades": len(live_exp),
                "candidates": len(candidates),
                "blocked": blocked_count,
                "news": len(recent_news),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/detail/symbol/trades")
async def crypto_symbol_detail_trades(symbol: str = Query(...), limit: int = 25):
    try:
        from services.crypto import store
        sym = _normalize_crypto_symbol(symbol)
        rows = [r for r in store.get_live_experience_sync(limit=max(50, limit * 4), closed_only=False) if str(r.get("symbol", "")).upper() == sym][:max(1, min(int(limit), 100))]
        return {"symbol": sym, "items": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/detail/symbol/news")
async def crypto_symbol_detail_news(symbol: str = Query(...), limit: int = 20, since_ms: int = 0):
    try:
        import re
        from services.crypto import store
        from services.crypto.report_generator import get_all_latest_reports
        sym = _normalize_crypto_symbol(symbol)
        rows = store.get_recent_news_events_sync(limit=max(1, min(int(limit), 100)), symbol=sym, since_ms=int(since_ms or 0), active_only=False)
        event_state = store.get_symbol_event_state_sync(sym)
        base = str(sym or '').split('/')[0].upper()
        related_reports = []
        pattern = re.compile(rf"\b{re.escape(base)}\b", re.IGNORECASE)
        for rtype, report in (get_all_latest_reports() or {}).items():
            content = str((report or {}).get('content') or '')
            if not content or not pattern.search(content):
                continue
            idx = max(0, pattern.search(content).start() - 80)
            snippet = content[idx:idx + 180].replace('\n', ' ').strip()
            related_reports.append({
                'report_type': rtype,
                'ts': report.get('ts'),
                'snippet': snippet,
            })
        return {"symbol": sym, "items": rows, "count": len(rows), "event_state": event_state, "related_reports": related_reports[:4]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/detail/symbol/scores")
async def crypto_symbol_detail_scores(symbol: str = Query(...), limit: int = 25):
    try:
        from services.crypto import store
        sym = _normalize_crypto_symbol(symbol)
        capped = max(1, min(int(limit), 100))
        return {
            "symbol": sym,
            "decision_traces": store.list_brain_decision_traces_sync(limit=capped, symbol=sym),
            "candidate_traces": store.list_candidate_traces_sync(limit=capped, symbol=sym),
            "decision_outcomes": store.list_decision_outcomes_sync(limit=capped, symbol=sym),
            "component_calibration": store.get_latest_component_calibration_sync(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/detail/symbol/risk")
async def crypto_symbol_detail_risk(symbol: str = Query(...), limit: int = 50):
    try:
        from services.crypto import store, get_bot_status
        sym = _normalize_crypto_symbol(symbol)
        runtime = await get_bot_status()
        rows = store.list_risk_state_history_sync(limit=max(1, min(int(limit), 200)), symbol=sym)
        activity = store.get_activity_history_sync(limit=72)
        return {
            "symbol": sym,
            "risk_history": rows,
            "activity_history": activity[-24:],
            "runtime": runtime.get("runtime", {}),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/detail/symbol/history")
async def crypto_symbol_detail_history(symbol: str = Query(...), range_key: str = Query("1D")):
    try:
        from services.crypto import market_data
        sym = _normalize_crypto_symbol(symbol)
        timeframe, limit = _timeframe_limit_from_range(range_key)
        df = market_data.fetch_bars(sym, timeframe=timeframe, limit=limit)
        items = []
        if not df.empty:
            items = df.tail(limit).to_dict("records")
        return {"symbol": sym, "range_key": str(range_key or "1D").upper(), "timeframe": timeframe, "items": items, "count": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/detail/risk-governor")
async def crypto_risk_governor_detail(since_ms: int = 0, limit: int = 200):
    try:
        from services.crypto import store, get_bot_status
        runtime = await get_bot_status()
        capped = max(1, min(int(limit), 500))
        return {
            "runtime": runtime.get("runtime", {}),
            "activity_history": store.get_activity_history_sync(since_ms=int(since_ms or 0), limit=capped),
            "risk_history": store.list_risk_state_history_sync(limit=capped),
            "scratchpads": store.list_research_scratchpads_sync(limit=min(50, capped), channel="telegram"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/detail/report")
async def crypto_report_detail(report_type: str = Query("hourly"), ts: int = 0):
    try:
        from services.crypto.report_generator import get_historical_reports, get_all_latest_reports
        rtype = str(report_type or "hourly").lower()
        if int(ts or 0) > 0:
            history = get_historical_reports(100).get(rtype, [])
            report = next((row for row in history if int(row.get("ts", 0) or 0) == int(ts)), None)
        else:
            report = get_all_latest_reports().get(rtype)
        return {"report_type": rtype, "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crypto/detail/context-window")
async def crypto_context_window(ts: int = Query(...), window_ms: int = Query(3600000)):
    try:
        from services.crypto import store, list_recent_actions
        from services.crypto.report_generator import get_historical_reports
        center = int(ts or 0)
        radius = max(60000, int(window_ms or 3600000))
        start = center - radius
        end = center + radius
        actions = [row for row in await list_recent_actions(limit=300) if start <= int(row.get("ts", 0) or 0) <= end]
        reports = []
        for items in get_historical_reports(40).values():
            for row in items:
                row_ts = int(row.get("ts", 0) or 0)
                if start <= row_ts <= end:
                    reports.append(row)
        news = [row for row in store.get_recent_news_events_sync(limit=100, since_ms=max(0, start), active_only=False) if int(row.get("published_at", 0) or 0) <= end]
        risk = [row for row in store.list_risk_state_history_sync(limit=200) if start <= int(row.get("ts", 0) or 0) <= end]
        activity = [row for row in store.get_activity_history_sync(since_ms=max(0, start), limit=500) if int(row.get("ts", 0) or 0) <= end]
        return {
            "ts": center,
            "window_ms": radius,
            "actions": actions,
            "reports": reports,
            "news_events": news,
            "risk_history": risk,
            "activity_history": activity,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Analytics Endpoints ──


class BacktestRequest(BaseModel):
    symbol: str = "BTC/USD"
    initial_equity: float = 10000.0
    macro_score: float = 1.0
    start_date: Optional[str] = None  # defaults to 14 days ago in run_backtest
    end_date: Optional[str] = None    # optional upper bound (unused by backtest engine for now)


@router.get("/crypto/analytics/strategy-performance")
async def get_strategy_performance():
    """Return P&L attribution broken down by strategy from live_experience table."""
    try:
        from services.crypto import store as crypto_store
        rows = crypto_store.get_strategy_performance_sync()
        return {"strategies": rows, "ts": int(time.time() * 1000)}
    except Exception as e:
        return {"strategies": [], "error": str(e), "ts": int(time.time() * 1000)}


@router.get("/crypto/analytics/brain-status")
async def get_brain_status():
    """Return DRL model status, replay buffer size, decision feedback outcomes, last retrain."""
    try:
        import glob
        from services.crypto import store as crypto_store
        from services.crypto.ml.replay_buffer import EXPERIENCES_FILE

        data_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "data", "models"
        ))
        model_files = []
        if os.path.isdir(data_dir):
            for f in glob.glob(os.path.join(data_dir, "ppo_*.zip")):
                mtime = os.path.getmtime(f)
                model_files.append({
                    "name": os.path.basename(f),
                    "size_kb": round(os.path.getsize(f) / 1024, 1),
                    "last_modified_ms": int(mtime * 1000),
                })

        replay_count = 0
        try:
            if os.path.exists(EXPERIENCES_FILE):
                with open(EXPERIENCES_FILE, "r") as rf:
                    replay_count = sum(1 for line in rf if line.strip())
        except Exception:
            pass

        exp_stats = crypto_store.get_live_experience_stats_sync()
        outcome_stats = crypto_store.get_decision_outcome_stats_sync()

        training_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "data", "live_training"
        ))
        last_retrain_ms = None
        if os.path.isdir(training_dir):
            csvs = glob.glob(os.path.join(training_dir, "*.csv"))
            if csvs:
                last_retrain_ms = int(max(os.path.getmtime(f) for f in csvs) * 1000)

        return {
            "models": model_files,
            "replay_buffer_count": replay_count,
            "live_experience": exp_stats,
            "decision_outcomes": outcome_stats,
            "last_retrain_ms": last_retrain_ms,
            "ts": int(time.time() * 1000),
        }
    except Exception as e:
        return {"error": str(e), "ts": int(time.time() * 1000)}


@router.post("/crypto/analytics/backtest")
async def run_crypto_backtest(request: BacktestRequest):
    """Run a vectorized strategy backtest for a crypto symbol."""
    try:
        from services.crypto.backtest import run_backtest
        cfg = get_config().get("stocks", {}).get("crypto", {})
        result = await run_backtest(
            symbol=request.symbol,
            cfg=cfg,
            initial_equity=request.initial_equity,
            macro_score=request.macro_score,
            start_date=request.start_date,
        )
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e), "symbol": request.symbol}


@router.get("/crypto/analytics/backtest/auto/latest")
async def get_auto_backtest_latest():
    """Return the most recent auto-backtest result from bot_reports."""
    try:
        from services.crypto import store as crypto_store
        # Auto backtests are stored as actions with action_type=gemini_report, symbol="AUTO BACKTEST"
        actions = crypto_store._list_actions_sync(limit=500)
        backtest_actions = [
            a for a in actions
            if str(a.get("symbol") or "") == "AUTO BACKTEST"
        ]
        if not backtest_actions:
            return {"result": None, "message": "No auto backtest has run yet."}
        # Most recent = highest ts
        latest = max(backtest_actions, key=lambda a: float(a.get("ts") or 0))
        payload = latest.get("payload") or {}
        return {
            "result": payload,
            "ts": latest.get("ts"),
            "run_date": payload.get("run_date"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/analytics/backtest/auto/run")
async def trigger_auto_backtest():
    """Manually trigger an immediate auto backtest across all 7 coins."""
    try:
        from services.crypto.auto_backtest import run_auto_backtest
        # Run in background so this endpoint returns immediately
        asyncio.ensure_future(run_auto_backtest())
        return {"success": True, "message": "Auto backtest triggered. Results will appear in the Brain tab within ~60 seconds."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Helpers ──


def _clean_for_json(obj):
    """Recursively convert numpy/pandas types to JSON-serializable Python types."""
    import numpy as np
    import pandas as pd
    
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_for_json(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, pd.Series):
        return obj.tolist()
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict("records")
    elif isinstance(obj, (np.bool_,)):
        return bool(obj)
    elif pd.isna(obj) if isinstance(obj, float) else False:
        return None
    return obj


# ── ML Self-Learning Status Endpoint ─────────────────────────────────────────
@router.get("/crypto/ml-status")
def get_ml_status():
    """
    Returns the current state of the DRL self-learning pipeline:
    - Training tier status and next trigger countdown
    - Signal coverage: which of the 11 enrichment columns have real data
    - Model file info (size, last modified) per coin
    - Last 20 lines of ml_continuous_learning.log
    """
    import sqlite3 as _sql

    DATA_DIR = os.path.join(BACKEND_ROOT, "data")
    DB_FILE  = os.path.join(DATA_DIR, "crypto_bot.db")

    def _read_ts(path: str) -> float:
        try:
            with open(path) as f:
                return float(f.read().strip())
        except Exception:
            return 0.0

    now = time.time()

    # ── Training tier timestamps ──────────────────────────────────────────────
    last_finetune  = _read_ts(os.path.join(DATA_DIR, "last_ml_finetune.txt"))
    last_deep      = _read_ts(os.path.join(DATA_DIR, "last_ml_sync.txt"))
    last_backtest  = _read_ts(os.path.join(DATA_DIR, "last_auto_backtest.txt"))

    def _ago(ts: float) -> str:
        if ts <= 0: return "never"
        diff = now - ts
        if diff < 3600:   return f"{int(diff//60)}m ago"
        if diff < 86400:  return f"{int(diff//3600)}h ago"
        return f"{int(diff//86400)}d ago"

    # ── Closed trade count + decision_outcomes coverage ───────────────────────
    total_trades = 0
    decision_outcomes_count = 0
    oracle_count = 0
    signal_coverage: dict = {}

    try:
        con = _sql.connect(DB_FILE)
        con.row_factory = _sql.Row

        row = con.execute(
            "SELECT COUNT(*) AS cnt FROM live_experience WHERE exit_ts IS NOT NULL"
        ).fetchone()
        total_trades = int((row["cnt"] if row else 0) or 0)

        row2 = con.execute("SELECT COUNT(*) AS cnt FROM decision_outcomes").fetchone()
        decision_outcomes_count = int((row2["cnt"] if row2 else 0) or 0)

        row3 = con.execute("SELECT COUNT(*) AS cnt FROM oracle_history").fetchone()
        oracle_count = int((row3["cnt"] if row3 else 0) or 0)

        # Signal coverage: check if each enrichment column has non-zero values
        _signals = [
            ("gemini_macro",      "gemini_score",      "live_experience"),
            ("gemini_regime_enc", "gemini_regime",     "live_experience"),
            ("session_win_rate",  "session_win_rate",  "live_experience"),
            ("session_pnl_pct",   "session_pnl_pct",   "live_experience"),
            ("slm_confidence",    "slm_confidence",    "live_experience"),
            ("event_score",       None,                None),          # from news events
            ("oracle_momentum",   "score",             "oracle_history"),
            ("strategy_win_rate", "strategy_used",     "live_experience"),
            ("outcome_pnl_15m",   None,                "decision_outcomes"),
            ("outcome_pnl_30m",   None,                "decision_outcomes"),
            ("outcome_pnl_60m",   None,                "decision_outcomes"),
        ]
        for signal_name, col, table in _signals:
            try:
                if table is None:
                    signal_coverage[signal_name] = {"count": 0, "has_data": False}
                    continue
                if table == "decision_outcomes" and signal_name.startswith("outcome_pnl"):
                    hz = int(signal_name.split("_")[-1].replace("m", ""))
                    r = con.execute(
                        "SELECT COUNT(*) AS cnt FROM decision_outcomes WHERE horizon_min=? AND pnl_pct IS NOT NULL", (hz,)
                    ).fetchone()
                    cnt = int((r["cnt"] if r else 0) or 0)
                else:
                    r = con.execute(
                        f"SELECT COUNT(*) AS cnt FROM {table} WHERE {col} IS NOT NULL AND {col} != '' AND {col} != 0"
                    ).fetchone()
                    cnt = int((r["cnt"] if r else 0) or 0)
                signal_coverage[signal_name] = {"count": cnt, "has_data": cnt > 0}
            except Exception:
                signal_coverage[signal_name] = {"count": 0, "has_data": False, "error": True}

        con.close()
    except Exception as e:
        signal_coverage["_error"] = str(e)

    # ── Model files ───────────────────────────────────────────────────────────
    models_dir = os.path.join(DATA_DIR, "models")
    model_files = {}
    try:
        for fname in os.listdir(models_dir):
            if fname.endswith(".zip"):
                fp = os.path.join(models_dir, fname)
                stat = os.stat(fp)
                model_files[fname] = {
                    "size_kb": round(stat.st_size / 1024, 1),
                    "last_modified": _ago(stat.st_mtime),
                    "last_modified_ts": int(stat.st_mtime),
                }
    except Exception:
        pass

    # ── Training log tail ─────────────────────────────────────────────────────
    log_tail: list = []
    try:
        log_path = os.path.join(DATA_DIR, "ml_continuous_learning.log")
        if os.path.exists(log_path):
            with open(log_path, "r", errors="replace") as f:
                lines = f.readlines()
                log_tail = [l.rstrip() for l in lines[-20:]]
    except Exception:
        pass

    # ── Next trigger estimates ────────────────────────────────────────────────
    TRADE_THRESHOLD = 50
    WEEKLY_HOURS    = 7 * 24
    MONTHLY_HOURS   = 30 * 24

    hours_since_fine  = (now - last_finetune) / 3600.0
    hours_since_deep  = (now - last_deep) / 3600.0

    next_tier1_trades = max(0, TRADE_THRESHOLD - (total_trades % TRADE_THRESHOLD)) if total_trades > 0 else TRADE_THRESHOLD
    next_weekly_hours = max(0.0, WEEKLY_HOURS - hours_since_fine)
    next_monthly_hours = max(0.0, MONTHLY_HOURS - hours_since_deep)

    return {
        "auto_training_active": True,
        "tiers": {
            "tier1_trade_trigger": {
                "description": "Fast fine-tune when 50 new trades",
                "last_run": _ago(last_finetune),
                "hours_since": round(hours_since_fine, 1),
                "trades_until_next": next_tier1_trades,
                "total_closed_trades": total_trades,
            },
            "tier2_weekly": {
                "description": "Weekly fine-tune (25k steps)",
                "last_run": _ago(last_finetune),
                "hours_remaining": round(next_weekly_hours, 1),
            },
            "tier3_monthly_deep": {
                "description": "Monthly deep retrain (100k steps)",
                "last_run": _ago(last_deep),
                "hours_remaining": round(next_monthly_hours, 1),
            },
            "auto_backtest": {
                "last_run": _ago(last_backtest),
            },
        },
        "data_quality": {
            "total_closed_trades": total_trades,
            "decision_outcomes_logged": decision_outcomes_count,
            "oracle_snapshots": oracle_count,
            "signal_coverage": signal_coverage,
        },
        "models": model_files,
        "log_tail": log_tail,
    }
