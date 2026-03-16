"""
Convergence Service — Cross-market comparison engine.
Compares Polymarket (global) vs Kalshi (US) prices for matching events.
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger("apex.convergence")


async def find_convergence_opportunities() -> List[Dict[str, Any]]:
    """
    Find events that exist on both Polymarket and Kalshi,
    then calculate the price spread (convergence opportunity).
    """
    opportunities = []

    try:
        # Fetch Polymarket markets
        from routers.polymarket import _cached_get, CLOB_BASE
        poly_markets = _cached_get("convergence_poly", f"{CLOB_BASE}/markets", {"limit": 50})

        # Fetch Kalshi markets (use router helper to avoid cross-project import collisions)
        from routers.kalshi import _get_api
        kalshi = _get_api()
        kalshi_markets = kalshi.get_markets(limit=100, status="open")

        if not poly_markets or not kalshi_markets:
            return []

        # Build keyword index from Kalshi
        kalshi_index = {}
        for km in kalshi_markets:
            title = km.get("title", "").lower()
            ticker = km.get("ticker", "")
            yes_price = km.get("yes_price", 0)
            if yes_price:
                # Kalshi prices are in cents (0-100), normalize to 0-1
                kalshi_index[title] = {
                    "ticker": ticker,
                    "title": km.get("title", ""),
                    "price": yes_price / 100 if yes_price > 1 else yes_price,
                }

        # Match Polymarket questions to Kalshi titles
        for pm in (poly_markets if isinstance(poly_markets, list) else []):
            question = pm.get("question", "").lower()
            tokens = pm.get("tokens", [])

            if not tokens:
                continue

            # Simple keyword matching (can be improved with fuzzy matching)
            for kalshi_title, kalshi_data in kalshi_index.items():
                # Check for significant word overlap
                q_words = set(question.split())
                k_words = set(kalshi_title.split())
                overlap = q_words & k_words
                # Require at least 3 common meaningful words
                meaningful = {w for w in overlap if len(w) > 3}

                if len(meaningful) >= 3:
                    # We have a match — calculate spread
                    poly_price = 0.5  # Default, will be updated from book data
                    kalshi_price = kalshi_data["price"]

                    spread = abs(poly_price - kalshi_price)
                    signal = "BUY_KALSHI" if poly_price > kalshi_price else "FADE_KALSHI"

                    opportunities.append({
                        "polymarket_question": pm.get("question", ""),
                        "kalshi_title": kalshi_data["title"],
                        "kalshi_ticker": kalshi_data["ticker"],
                        "polymarket_price": round(poly_price, 4),
                        "kalshi_price": round(kalshi_price, 4),
                        "spread": round(spread, 4),
                        "signal": signal,
                        "signal_strength": round(spread * 100, 1),
                        "match_score": len(meaningful),
                    })

        # Sort by spread (biggest opportunities first)
        opportunities.sort(key=lambda x: x["spread"], reverse=True)

    except Exception as e:
        logger.error(f"Convergence scan error: {e}")

    return opportunities[:20]  # Top 20
