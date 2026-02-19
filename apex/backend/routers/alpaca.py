"""
Alpaca Router — Full feature port from alpaca/ modules.
Imports MarketScanner, TechnicalAnalyst, TradePredictor, ExecutionEngine
directly from the alpaca project via sys.path.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import asyncio
import json
import os
import sys
import threading
import time
from contextlib import contextmanager

from core import job_store
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
        return await asyncio.to_thread(get_account)
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
        items = await asyncio.to_thread(get_positions)
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


@router.get("/crypto/actions")
async def crypto_actions(limit: int = 200):
    try:
        from services.crypto import list_recent_actions
        items = await list_recent_actions(limit=limit)
        return {"items": items, "count": len(items)}
    except Exception as e:
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


@router.post("/crypto/bot/start")
async def crypto_bot_start():
    try:
        from services.crypto import start_bot
        status = await asyncio.to_thread(start_bot)
        return {"success": True, "status": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crypto/bot/stop")
async def crypto_bot_stop():
    try:
        from services.crypto import stop_bot
        status = await asyncio.to_thread(stop_bot)
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
async def crypto_flatten_all():
    try:
        from services.crypto import flatten_all_positions
        result = await asyncio.to_thread(flatten_all_positions)
        return {"success": True, "result": result}
    except Exception as e:
        if _is_auth_error(e):
            raise HTTPException(status_code=401, detail=str(e))
        if _is_network_error(e):
            raise HTTPException(status_code=503, detail=str(e))
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
