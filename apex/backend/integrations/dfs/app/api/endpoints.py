"""API endpoints for Apex DFS."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import SleeperClient, PropOddsClient, get_settings
from app.logic import evaluate_prop, generate_top_slips

router = APIRouter()


class ScanRequest(BaseModel):
    """Request model for scanning."""
    sport: str = "nfl"
    max_games: int = 3  # Max games to query (API credit saver)
    trending_limit: int = 25  # How many trending players to consider


class EdgeCheckRequest(BaseModel):
    """Quick edge check for a single prop."""
    player_name: str
    market: str
    line: float
    sharp_odds: int
    sharp_book: str = "pinnacle"


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "apex-dfs"}


@router.get("/trending")
async def get_trending(
    sport: str = "nfl",
    trend_type: str = "add",
    lookback_hours: int = 24,
    limit: int = 25
):
    """Fetch trending players from Sleeper with team info."""
    client = SleeperClient()
    
    try:
        trending = await client.get_trending_with_teams(sport=sport, limit=limit)
        return {"trending": trending, "count": len(trending)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan")
async def scan_opportunities(request: ScanRequest):
    """
    Smart Scan: Only query games with trending players.
    
    This minimizes API usage by:
    1. Getting trending players from Sleeper (free)
    2. Filtering upcoming games to only those with trending player teams
    3. Fetching props only for those specific games
    """
    sleeper = SleeperClient()
    prop_odds = PropOddsClient()
    
    try:
        # Step 1: Get trending players with team info (FREE)
        trending = await sleeper.get_trending_with_teams(
            sport=request.sport,
            limit=request.trending_limit
        )
        
        if not trending:
            return {
                "opportunities": [],
                "total_scanned": 0,
                "plays_found": 0,
                "games_queried": 0,
                "trending_players": 0,
                "message": "No trending players found on Sleeper."
            }
        
        # Step 2 & 3: Smart Scan - only query high-value games
        all_props = await prop_odds.smart_scan(
            trending_players=trending,
            sport=request.sport,
            max_games=request.max_games
        )
        
        if not all_props:
            return {
                "opportunities": [],
                "total_scanned": 0,
                "plays_found": 0,
                "games_queried": 0,
                "trending_players": len(trending),
                "message": "No games scheduled for trending player teams. Check back closer to game time."
            }
        
        # Step 4: Evaluate each prop for edge (only "Over" side)
        opportunities = []
        seen = set()  # Deduplicate by player+market
        
        # Build set of trending player names for highlighting
        trending_names = {p.get("name", "").lower() for p in trending}
        
        for prop in all_props:
            # Only process "Over" bets for simplicity
            if prop.get("side", "").lower() != "over":
                continue
            
            key = f"{prop['player_name']}_{prop['market']}"
            if key in seen:
                continue
            seen.add(key)
            
            opp = evaluate_prop(
                player_id=prop.get("event_id", "unknown"),
                player_name=prop["player_name"],
                market=prop["market"],
                line=prop.get("line", 0),
                sharp_odds=prop.get("odds", -110),
                sharp_book=prop.get("book", "unknown")
            )
            
            # Check if this player is in trending list
            is_trending = prop["player_name"].lower() in trending_names
            
            opportunities.append({
                "player_id": opp.player_id,
                "player_name": opp.player_name,
                "market": opp.market,
                "line": opp.line,
                "sharp_odds": opp.sharp_odds,
                "sharp_book": opp.sharp_book,
                "edge_pct": round(opp.edge * 100, 2),
                "is_play": opp.is_play,
                "is_trending": is_trending,
            })
        
        # Sort by edge descending
        opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
        
        return {
            "opportunities": opportunities,
            "total_scanned": len(opportunities),
            "plays_found": sum(1 for o in opportunities if o["is_play"]),
            "games_queried": min(request.max_games, len(all_props) // 20 + 1),  # Estimate
            "trending_players": len(trending),
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/check-edge")
async def check_edge(request: EdgeCheckRequest):
    """Quick edge check for a manually entered prop."""
    opp = evaluate_prop(
        player_id="manual",
        player_name=request.player_name,
        market=request.market,
        line=request.line,
        sharp_odds=request.sharp_odds,
        sharp_book=request.sharp_book
    )
    
    return {
        "player_name": opp.player_name,
        "market": opp.market,
        "line": opp.line,
        "sharp_odds": opp.sharp_odds,
        "sharp_implied_prob": round(opp.sharp_implied_prob * 100, 2),
        "fixed_implied_prob": round(opp.fixed_implied_prob * 100, 2),
        "edge_pct": round(opp.edge * 100, 2),
        "is_play": opp.is_play,
        "recommendation": "✅ PLAY" if opp.is_play else "❌ PASS"
    }


@router.get("/settings")
async def get_current_settings():
    """Return current strategy settings."""
    settings = get_settings()
    return {
        "edge_threshold_pct": settings.edge_threshold * 100,
        "dfs_fixed_implied_prob_pct": settings.dfs_fixed_implied_prob * 100
    }


class GenerateSlipsRequest(BaseModel):
    """Request model for slip generation."""
    opportunities: list[dict]
    slip_sizes: list[int] = [3, 4, 5]
    top_n: int = 5
    min_edge: float = 0.0


@router.post("/generate-slips")
async def generate_slips(request: GenerateSlipsRequest):
    """
    Generate top-ranked parlay slips from scanned opportunities.
    
    Uses combinatorial optimization to find the best EV slips.
    """
    try:
        slips = generate_top_slips(
            opportunities=request.opportunities,
            slip_sizes=request.slip_sizes,
            top_n=request.top_n,
            min_edge=request.min_edge
        )
        
        return {
            "slips": slips,
            "total_generated": len(slips)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
