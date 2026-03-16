"""
Polymarket Router — Read-only data fetcher for convergence analysis.
Fetches public market data from Polymarket CLOB API.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import asyncio
import httpx
import logging
import os
import time

from core import job_store

router = APIRouter()
logger = logging.getLogger("apex.polymarket")

# ── Config ──
CLOB_BASE = "https://clob.polymarket.com"
PROXY = os.getenv("POLYMARKET_PROXY", None)  # Optional: for geo-bypass

# ── Cache ──
_cache: Dict[str, Any] = {}
_cache_ttl = 30  # seconds


class PolymarketHealthResponse(BaseModel):
    status: str
    proxy: bool
    error: Optional[str] = None


class PolymarketToken(BaseModel):
    token_id: str
    outcome: str
    price: float


class PolymarketMarket(BaseModel):
    condition_id: str
    question: str
    tokens: List[PolymarketToken]
    active: bool
    closed: bool
    volume: float
    volume_24hr: float
    liquidity: float
    end_date: str
    image: str


class PolymarketMarketsResponse(BaseModel):
    markets: List[PolymarketMarket]
    count: int


class PolymarketBookResponse(BaseModel):
    token_id: str
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    implied_probability: float
    bids: List[Dict[str, Any]]
    asks: List[Dict[str, Any]]


class PolymarketConvergenceResponse(BaseModel):
    opportunities: List[Dict[str, Any]]
    count: int = 0
    polymarket_scanned: int = 0
    kalshi_scanned: int = 0
    message: Optional[str] = None
    job_id: Optional[str] = None


def _get_client():
    """Create httpx client, optionally with proxy."""
    kwargs = {"timeout": 10.0}
    if PROXY:
        kwargs["proxies"] = {"https://": PROXY}
    return httpx.Client(**kwargs)


def _cached_get(key: str, url: str, params: dict = None):
    """Fetch with simple TTL cache."""
    now = time.time()
    if key in _cache and (now - _cache[key]["ts"]) < _cache_ttl:
        return _cache[key]["data"]

    try:
        with _get_client() as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        _cache[key] = {"data": data, "ts": now}
        return data
    except Exception as e:
        logger.error(f"Polymarket fetch error: {e}")
        # Return stale cache if available
        if key in _cache:
            return _cache[key]["data"]
        raise


# ── Endpoints ──
@router.get("/health", response_model=PolymarketHealthResponse)
async def polymarket_health():
    try:
        def _health_check():
            with _get_client() as client:
                resp = client.get(f"{CLOB_BASE}/markets", params={"limit": 1})
                resp.raise_for_status()

        await asyncio.to_thread(_health_check)
        return {"status": "connected", "proxy": bool(PROXY)}
    except Exception as e:
        return {"status": "error", "error": str(e), "proxy": bool(PROXY)}


@router.get("/markets", response_model=PolymarketMarketsResponse)
async def list_markets(limit: int = 25, query: Optional[str] = None):
    """List active Polymarket markets via Gamma API (better filtering/sorting)."""
    try:
        gamma_url = "https://gamma-api.polymarket.com/markets"
        params = {
            "limit": limit,
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
        }
        if query:
            params["tag"] = query

        data = await asyncio.to_thread(_cached_get, f"gamma_markets_{limit}_{query}", gamma_url, params)

        markets = []
        items = data if isinstance(data, list) else []
        for m in items:
            # Extract tokens from outcomes
            tokens = []
            outcomes = m.get("outcomes", "")
            outcome_prices = m.get("outcomePrices", "")
            clob_ids = m.get("clobTokenIds", "")

            # Parse JSON strings if needed
            import json as _json
            if isinstance(outcomes, str):
                try: outcomes = _json.loads(outcomes)
                except: outcomes = []
            if isinstance(outcome_prices, str):
                try: outcome_prices = _json.loads(outcome_prices)
                except: outcome_prices = []
            if isinstance(clob_ids, str):
                try: clob_ids = _json.loads(clob_ids)
                except: clob_ids = []

            for j, out in enumerate(outcomes or []):
                tokens.append({
                    "token_id": clob_ids[j] if j < len(clob_ids) else "",
                    "outcome": out,
                    "price": float(outcome_prices[j]) if j < len(outcome_prices) else 0,
                })

            markets.append({
                "condition_id": m.get("conditionId", m.get("id", "")),
                "question": m.get("question", ""),
                "tokens": tokens,
                "active": m.get("active", False),
                "closed": m.get("closed", False),
                "volume": float(m.get("volume", 0) or 0),
                "volume_24hr": float(m.get("volume24hr", 0) or 0),
                "liquidity": float(m.get("liquidity", 0) or 0),
                "end_date": m.get("endDate", ""),
                "image": m.get("image", ""),
            })

        return {"markets": markets, "count": len(markets)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/book/{token_id}", response_model=PolymarketBookResponse)
async def get_order_book(token_id: str):
    """Get order book for a specific token. Returns bids, asks, and implied probability."""
    try:
        data = await asyncio.to_thread(_cached_get, f"book_{token_id}", f"{CLOB_BASE}/book", {"token_id": token_id})

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        best_bid = float(bids[0]["price"]) if bids else 0
        best_ask = float(asks[0]["price"]) if asks else 1
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid

        return {
            "token_id": token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": round(mid_price, 4),
            "spread": round(spread, 4),
            "implied_probability": round(mid_price, 4),
            "bids": bids[:10],  # Top 10
            "asks": asks[:10],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/convergence", response_model=PolymarketConvergenceResponse)
async def get_convergence_opportunities():
    """
    Compare Polymarket prices with Kalshi prices for matching events.
    Uses fuzzy keyword matching from polymarket_fetcher.
    """
    job = job_store.create_job(domain="events", kind="convergence_scan", metadata={})
    job_store.mark_running(job["id"], message="Convergence scan started")
    try:
        from services.polymarket_fetcher import get_fetcher

        fetcher = get_fetcher()
        poly_markets = await asyncio.to_thread(fetcher.fetch_markets, 50)

        # Get Kalshi markets for comparison
        try:
            from routers.kalshi import _get_api
            api = _get_api()
            kalshi_markets = await asyncio.to_thread(api.get_markets, 50, None, "open")
        except Exception:
            kalshi_markets = []

        if not kalshi_markets:
            result = {
                "opportunities": [],
                "message": "Kalshi API not connected — cannot compare markets",
                "polymarket_scanned": len(poly_markets),
                "job_id": job["id"],
            }
            job_store.mark_completed(job["id"], message="Convergence scan completed (Kalshi unavailable)")
            return result

        matches = await asyncio.to_thread(fetcher.match_events, poly_markets, kalshi_markets)
        result = {
            "opportunities": matches,
            "count": len(matches),
            "polymarket_scanned": len(poly_markets),
            "kalshi_scanned": len(kalshi_markets),
            "job_id": job["id"],
        }
        job_store.mark_completed(
            job["id"],
            message="Convergence scan completed",
            metadata={"count": len(matches), "polymarket_scanned": len(poly_markets), "kalshi_scanned": len(kalshi_markets)},
        )
        return result
    except Exception as e:
        job_store.mark_failed(job["id"], str(e))
        raise HTTPException(status_code=500, detail=str(e))
