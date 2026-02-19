"""
Blind Proxy Strategy Engine.

This module implements the core "Blind Proxy" algorithm for finding +EV DFS plays.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.core import get_settings


@dataclass
class PropOpportunity:
    """A potential +EV prop opportunity."""
    player_id: str
    player_name: str
    market: str  # e.g., "player_points", "player_rebounds"
    line: float  # e.g., 24.5
    sharp_odds: int  # American odds, e.g., -140
    sharp_book: str  # e.g., "pinnacle", "draftkings"
    sharp_implied_prob: float
    fixed_implied_prob: float
    edge: float  # Positive = +EV
    is_play: bool
    # Opposing odds data (for no-vig calculation)
    opposing_odds: int | None = None
    opposing_implied_prob: float | None = None
    fair_prob: float | None = None  # No-vig probability
    vig_pct: float | None = None  # Bookmaker vig percentage


def american_to_implied(odds: int) -> float:
    """
    Convert American odds to implied probability.
    
    Examples:
        -140 -> 0.583 (58.3%)
        +120 -> 0.455 (45.5%)
        -110 -> 0.524 (52.4%)
    """
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_edge(
    sharp_odds: int,
    fixed_implied_prob: Optional[float] = None,
    opposing_odds: Optional[int] = None,
    assumed_vig: float = 0.05,
) -> dict:
    """
    Calculate the edge of a sharp line vs DFS fixed payout.

    When opposing_odds are provided, removes the bookmaker's vig from both
    sides (multiplicative devig) to get the No-Vig Fair Probability.

    When opposing_odds are NOT provided (single-side consensus), we still
    devig using the assumed_vig parameter (default 5%) so that the edge
    reflects a realistic true probability rather than a vig-inflated implied
    probability. This prevents artificially negative edge numbers caused by
    comparing a vig-inclusive implied prob against the DFS payout baseline.

    Args:
        sharp_odds: American odds from a sharp book (e.g., -140)
        fixed_implied_prob: Implied probability of DFS site (default: from config)
        opposing_odds: American odds for the other side (e.g., +120 for Under)
        assumed_vig: Fraction of vig to remove when opposing_odds not available (default 5%)

    Returns:
        Dict with sharp_prob, fixed_prob, edge, and optional fair_prob/vig_pct
    """
    if fixed_implied_prob is None:
        fixed_implied_prob = get_settings().dfs_fixed_implied_prob

    sharp_prob = american_to_implied(sharp_odds)

    result = {
        "sharp_prob": sharp_prob,
        "fixed_prob": fixed_implied_prob,
        "opposing_prob": None,
        "fair_prob": None,
        "vig_pct": None,
    }

    if opposing_odds is not None:
        opp_prob = american_to_implied(opposing_odds)
        total_prob = sharp_prob + opp_prob  # > 1.0 due to vig
        vig = total_prob - 1.0
        # No-vig fair probability (multiplicative method)
        fair_prob = sharp_prob / total_prob
        result["opposing_prob"] = round(opp_prob, 4)
        result["fair_prob"] = round(fair_prob, 4)
        result["vig_pct"] = round(vig * 100, 2)
        result["edge"] = fair_prob - fixed_implied_prob
    else:
        # Single-side devig: approximate fair prob by scaling out assumed vig.
        # Assumed total implied = 1 + assumed_vig, so our side's share is
        # sharp_prob / (1 + assumed_vig).
        assumed_total = 1.0 + max(0.0, float(assumed_vig))
        fair_prob = sharp_prob / assumed_total
        result["fair_prob"] = round(fair_prob, 4)
        result["vig_pct"] = round(assumed_vig * 100, 2)
        result["edge"] = fair_prob - fixed_implied_prob

    return result


def evaluate_prop(
    player_id: str,
    player_name: str,
    market: str,
    line: float,
    sharp_odds: int,
    sharp_book: str = "pinnacle",
    opposing_odds: Optional[int] = None,
) -> PropOpportunity:
    """
    Evaluate a single prop for +EV potential.
    
    Args:
        player_id: Unique player identifier
        player_name: Display name
        market: Prop market type
        line: The prop line (e.g., 24.5 points)
        sharp_odds: American odds from sharp book
        sharp_book: Name of the sharp book
        opposing_odds: American odds for the other side (Under if Over, etc.)
        
    Returns:
        PropOpportunity with edge calculation
    """
    settings = get_settings()
    calc = calculate_edge(sharp_odds, opposing_odds=opposing_odds)
    
    edge = calc["edge"]
    is_play = edge >= settings.edge_threshold
    
    return PropOpportunity(
        player_id=player_id,
        player_name=player_name,
        market=market,
        line=line,
        sharp_odds=sharp_odds,
        sharp_book=sharp_book,
        sharp_implied_prob=round(calc["sharp_prob"], 4),
        fixed_implied_prob=round(calc["fixed_prob"], 4),
        edge=round(edge, 4),
        is_play=is_play,
        opposing_odds=opposing_odds,
        opposing_implied_prob=calc["opposing_prob"],
        fair_prob=calc["fair_prob"],
        vig_pct=calc["vig_pct"],
    )


async def scan_for_opportunities(
    trending_players: list[dict],
    player_metadata: dict,
    prop_odds_data: list[dict]
) -> list[PropOpportunity]:
    """
    Main scanning function that combines trending data with prop odds.
    
    Args:
        trending_players: List from Sleeper trending endpoint
        player_metadata: Full player data from Sleeper
        prop_odds_data: Prop odds from PropOdds API
        
    Returns:
        List of PropOpportunity sorted by edge (highest first)
    """
    opportunities: list[PropOpportunity] = []
    
    # Build a map of player_id -> player_name
    player_names = {
        pid: f"{data.get('first_name', '')} {data.get('last_name', '')}"
        for pid, data in player_metadata.items()
    }
    
    # For each trending player, check if we have prop odds
    for trend in trending_players:
        player_id = trend.get("player_id", "")
        player_name = player_names.get(player_id, f"Unknown ({player_id})")
        
        # Find matching prop odds (simplified matching logic)
        for prop in prop_odds_data:
            if prop.get("player_name", "").lower() == player_name.lower():
                opp = evaluate_prop(
                    player_id=player_id,
                    player_name=player_name,
                    market=prop.get("market", "unknown"),
                    line=prop.get("line", 0.0),
                    sharp_odds=prop.get("odds", -110),
                    sharp_book=prop.get("book", "unknown")
                )
                opportunities.append(opp)
    
    # Sort by edge, highest first
    opportunities.sort(key=lambda x: x.edge, reverse=True)
    
    return opportunities
