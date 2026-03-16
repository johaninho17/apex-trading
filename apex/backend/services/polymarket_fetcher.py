"""
Polymarket Fetcher — Structured data pipeline for Polymarket CLOB API.

Handles geo-bypass via optional proxy, caching, normalization,
and fuzzy event matching for the convergence engine.
"""

import httpx
import logging
import time
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger("apex.polymarket.fetcher")

CLOB_BASE = "https://clob.polymarket.com"


@dataclass
class NormalizedMarket:
    """Standardized market data for cross-platform comparison."""
    condition_id: str
    question: str
    tokens: List[Dict[str, str]]
    probability: float  # 0-1 based on mid price
    volume: float
    active: bool
    keywords: List[str]  # extracted for fuzzy matching


class PolymarketFetcher:
    """
    Fetches and normalizes Polymarket data.
    Handles rate limiting, caching, and optional geo-bypass.
    """

    def __init__(self, proxy: Optional[str] = None, cache_ttl: int = 30):
        self.proxy = proxy
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, Any] = {}
        self.stats = {
            "requests": 0,
            "cache_hits": 0,
            "errors": 0,
        }

    def _client(self) -> httpx.Client:
        kwargs: Dict[str, Any] = {"timeout": 15.0}
        if self.proxy:
            kwargs["proxies"] = {"https://": self.proxy}
        return httpx.Client(**kwargs)

    def _cached_get(self, key: str, url: str, params: dict = None) -> Any:
        now = time.time()
        if key in self._cache and (now - self._cache[key]["ts"]) < self.cache_ttl:
            self.stats["cache_hits"] += 1
            return self._cache[key]["data"]

        try:
            self.stats["requests"] += 1
            client = self._client()
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            self._cache[key] = {"data": data, "ts": now}
            return data
        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"Polymarket API error: {e}")
            if key in self._cache:
                return self._cache[key]["data"]
            raise

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """Extract meaningful keywords from a market question."""
        stop_words = {
            "will", "the", "be", "in", "on", "at", "to", "of", "a", "an",
            "is", "it", "by", "for", "or", "and", "this", "that", "with",
            "as", "do", "does", "did", "has", "have", "had", "are", "was",
            "were", "been", "being", "before", "after", "than", "more",
            "most", "what", "which", "who", "whom", "how", "when", "where",
        }
        words = re.findall(r'[a-zA-Z0-9]+', text.lower())
        return [w for w in words if len(w) > 2 and w not in stop_words]

    def fetch_markets(self, limit: int = 50) -> List[NormalizedMarket]:
        """Fetch and normalize active markets."""
        data = self._cached_get(f"markets_{limit}", f"{CLOB_BASE}/markets", {"limit": limit})
        markets = []

        for m in (data if isinstance(data, list) else []):
            question = m.get("question", "")
            tokens = m.get("tokens", [])
            
            # Calculate probability from token prices if available
            probability = 0.5  # default
            if tokens:
                # Polymarket tokens have outcome_prices
                try:
                    prices = m.get("outcome_prices", [])
                    if prices and len(prices) > 0:
                        probability = float(prices[0])
                except (ValueError, IndexError):
                    pass

            markets.append(NormalizedMarket(
                condition_id=m.get("condition_id", ""),
                question=question,
                tokens=tokens,
                probability=probability,
                volume=float(m.get("volume", 0) or 0),
                active=m.get("active", False),
                keywords=self._extract_keywords(question),
            ))

        return markets

    def fetch_book(self, token_id: str) -> Dict[str, Any]:
        """Fetch order book for a specific token."""
        data = self._cached_get(f"book_{token_id}", f"{CLOB_BASE}/book", {"token_id": token_id})

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        best_bid = float(bids[0]["price"]) if bids else 0
        best_ask = float(asks[0]["price"]) if asks else 1
        mid_price = (best_bid + best_ask) / 2

        return {
            "token_id": token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": round(mid_price, 4),
            "spread": round(best_ask - best_bid, 4),
            "probability": round(mid_price, 4),
            "bid_depth": sum(float(b.get("size", 0)) for b in bids[:5]),
            "ask_depth": sum(float(a.get("size", 0)) for a in asks[:5]),
        }

    def match_events(self, poly_markets: List[NormalizedMarket],
                     kalshi_markets: List[Dict[str, Any]],
                     min_match_score: int = 3) -> List[Dict[str, Any]]:
        """
        Fuzzy match Polymarket events to Kalshi events using keyword overlap.
        Returns matched pairs with spread calculation.
        """
        # Build Kalshi keyword index
        kalshi_indexed = []
        for km in kalshi_markets:
            title = km.get("title", "")
            keywords = self._extract_keywords(title)
            yes_price = km.get("yes_price", 0)
            
            # Normalize Kalshi price (cents → probability)
            price = yes_price / 100 if yes_price and yes_price > 1 else (yes_price or 0.5)

            kalshi_indexed.append({
                "title": title,
                "ticker": km.get("ticker", ""),
                "keywords": set(keywords),
                "price": price,
                "original": km,
            })

        matches = []

        for pm in poly_markets:
            if not pm.active:
                continue

            pm_keywords = set(pm.keywords)

            for ki in kalshi_indexed:
                overlap = pm_keywords & ki["keywords"]
                score = len(overlap)

                if score >= min_match_score:
                    spread = abs(pm.probability - ki["price"])
                    
                    # Determine signal
                    if pm.probability > ki["price"]:
                        signal = "BUY_KALSHI"  # Poly says higher, Kalshi is cheap
                    else:
                        signal = "FADE_KALSHI"  # Poly says lower

                    matches.append({
                        "polymarket_question": pm.question,
                        "polymarket_condition_id": pm.condition_id,
                        "polymarket_price": round(pm.probability, 4),
                        "kalshi_title": ki["title"],
                        "kalshi_ticker": ki["ticker"],
                        "kalshi_price": round(ki["price"], 4),
                        "spread": round(spread, 4),
                        "signal": signal,
                        "signal_strength": round(spread * 100, 1),
                        "match_score": score,
                        "matched_keywords": list(overlap),
                    })

        # Sort by spread descending
        matches.sort(key=lambda x: x["spread"], reverse=True)
        return matches[:20]


# ── Singleton ──
import os
_fetcher = PolymarketFetcher(proxy=os.getenv("POLYMARKET_PROXY"))


def get_fetcher() -> PolymarketFetcher:
    return _fetcher
