"""
DFS Router — Sports betting / Daily Fantasy endpoints.
Wraps existing sportsbetting/apex-dfs logic and adds snipe alert infrastructure.
Imports smart scan pipeline from the original apex-dfs app.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Set, Tuple
import os
import sys
import logging
import time as _time

from core import job_store
from core.config_manager import get_config

router = APIRouter()
logger = logging.getLogger("apex.dfs")

# ── Path to vendored DFS project ──
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DFS_ROOT = os.path.join(BACKEND_ROOT, "integrations", "dfs")
DFS_BACKEND = DFS_ROOT
if DFS_BACKEND not in sys.path:
    sys.path.insert(0, DFS_BACKEND)

# Load .env for PROP_ODDS_API_KEY (Apex backend .env)
from dotenv import load_dotenv
_apex_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_apex_env):
    load_dotenv(_apex_env, override=False)


# ── Request Models ──
class EVCalcRequest(BaseModel):
    odds: float          # American odds (+150, -110)
    probability: float   # Your estimated true probability (0-1)
    stake: float = 100.0
    opposing_odds: Optional[float] = None  # For no-vig / devigging
    probability_confidence: float = Field(1.0, ge=0.0, le=1.0)

class EVCalcResponse(BaseModel):
    ev: float
    ev_percent: float
    decimal_odds: float
    implied_probability: float
    your_edge: float
    kelly_fraction: float
    kelly_stake: float
    fair_prob: Optional[float] = None
    vig_pct: Optional[float] = None
    opposing_implied: Optional[float] = None
    user_probability: float
    blended_probability: float
    confidence_weight: float
    devigged: bool

class CorrelationRequest(BaseModel):
    player: str
    stat: str  # 'passing_yards', 'receiving_yards', etc.
    direction: str = "over"  # 'over' or 'under'
    sport: str = "nba"  # 'nba' or 'nfl'

class MiddleCheckRequest(BaseModel):
    player_name: str
    stat: str
    dfs_line: float           # Fixed line on DFS platform (e.g., 24.5)
    sharp_line: float         # Dynamic line on sharp book (e.g., 26.5)
    sharp_odds: int = -110    # Sharp book odds for context
    dfs_odds: int = -110      # DFS side odds for context
    line_std: Optional[float] = None  # Optional std-dev for middle probability estimate
    market_confidence: float = Field(0.7, ge=0.0, le=1.0)
    dfs_platform: str = "prizepicks"
    sharp_book: str = "pinnacle"

class MiddleCheckResponse(BaseModel):
    player_name: str
    stat: str
    dfs_line: float
    sharp_line: float
    gap: float
    direction: str
    is_middle: bool
    strength: str
    action: str
    dfs_platform: str
    sharp_book: str
    middle_probability_estimate: float
    middle_ev_units: float
    breakeven_middle_probability: float
    assumed_std_dev: float
    assumed_mean: float
    confidence_weight: float


# ── Snipe Alert State ──
_snipe_alerts: List[Dict[str, Any]] = []


# ── Endpoints ──
@router.get("/health")
async def dfs_health():
    return {"status": "healthy", "dfs_root": DFS_ROOT}


@router.post("/ev-calculator", response_model=EVCalcResponse)
async def calculate_ev(request: EVCalcRequest):
    """Calculate Expected Value for a bet with optional no-vig calibration."""
    from app.logic import american_to_implied

    def _clamp_prob(value: float, eps: float = 1e-6) -> float:
        return max(eps, min(1.0 - eps, float(value)))

    # Convert American odds to decimal
    if request.odds > 0:
        decimal_odds = (request.odds / 100) + 1
    else:
        decimal_odds = (100 / abs(request.odds)) + 1

    implied_prob = 1 / decimal_odds
    user_prob = _clamp_prob(request.probability)
    true_prob = user_prob

    # ── Devigging: if opposing odds provided, compute fair probability ──
    fair_prob = None
    vig_pct = None
    opposing_implied = None
    if request.opposing_odds is not None:
        opp_odds = int(request.opposing_odds)
        main_implied = american_to_implied(int(request.odds))
        opp_implied = american_to_implied(opp_odds)
        total = main_implied + opp_implied
        vig = total - 1.0
        fair_prob = main_implied / total if total > 0 else None
        vig_pct = round(vig * 100, 2)
        opposing_implied = round(opp_implied, 4)
        if fair_prob is not None:
            # Blend your model with market fair probability.
            # Lower confidence pulls estimate closer to market.
            conf = request.probability_confidence
            true_prob = _clamp_prob((conf * user_prob) + ((1.0 - conf) * fair_prob))

    # EV = p * win_profit - q * stake
    win_profit = request.stake * (decimal_odds - 1.0)
    ev = (true_prob * win_profit) - ((1 - true_prob) * request.stake)
    ev_percent = (ev / request.stake) * 100

    # Kelly Criterion against true/fair probability
    b = decimal_odds - 1
    p = true_prob
    q = 1 - p
    kelly = ((b * p) - q) / b if b > 0 else 0
    # Confidence-scaled Kelly to avoid over-sizing on uncertain model edges.
    kelly *= (0.25 + (0.75 * request.probability_confidence))
    kelly = max(0, kelly)

    return {
        "ev": round(ev, 2),
        "ev_percent": round(ev_percent, 2),
        "decimal_odds": round(decimal_odds, 3),
        "implied_probability": round(implied_prob, 4),
        "your_edge": round(true_prob - implied_prob, 4),
        "kelly_fraction": round(kelly, 4),
        "kelly_stake": round(request.stake * kelly, 2),
        # Devigging fields (None if not used)
        "fair_prob": round(fair_prob, 4) if fair_prob is not None else None,
        "vig_pct": vig_pct,
        "opposing_implied": opposing_implied,
        "user_probability": round(user_prob, 4),
        "blended_probability": round(true_prob, 4),
        "confidence_weight": round(request.probability_confidence, 4),
        "devigged": request.opposing_odds is not None,
    }


@router.get("/snipe-alerts")
async def get_snipe_alerts():
    """Get current snipe alerts (line divergences)."""
    return {"alerts": _snipe_alerts, "count": len(_snipe_alerts)}


@router.post("/snipe-alerts/clear")
async def clear_snipe_alerts():
    global _snipe_alerts
    _snipe_alerts = []
    return {"message": "Alerts cleared"}


@router.post("/correlation")
async def get_correlations(request: CorrelationRequest):
    """Get correlated player picks for Pick'em entries — sport-aware."""
    # ── Sport-specific correlation data ──
    NFL_CORRELATIONS = {
        "passing_yards": [
            {"related_stat": "receiving_yards", "correlation": 0.82, "note": "WR1 stack", "boost": True},
            {"related_stat": "receptions", "correlation": 0.75, "note": "Slot/TE stack", "boost": True},
            {"related_stat": "completions", "correlation": 0.91, "note": "Same QB"},
            {"related_stat": "passing_tds", "correlation": 0.68, "note": "Same QB"},
        ],
        "receiving_yards": [
            {"related_stat": "passing_yards", "correlation": 0.82, "note": "QB stack", "boost": True},
            {"related_stat": "targets", "correlation": 0.88, "note": "Volume link"},
            {"related_stat": "receptions", "correlation": 0.90, "note": "Same player"},
        ],
        "rushing_yards": [
            {"related_stat": "rushing_attempts", "correlation": 0.85, "note": "Volume link"},
            {"related_stat": "passing_yards", "correlation": -0.15, "note": "Game script fade"},
            {"related_stat": "receiving_yards", "correlation": 0.35, "note": "Dual threat RB"},
        ],
    }
    NBA_CORRELATIONS = {
        "points": [
            {"related_stat": "field_goals_made", "correlation": 0.95, "note": "Volume lock"},
            {"related_stat": "free_throws", "correlation": 0.72, "note": "Draw fouls"},
            {"related_stat": "three_pointers", "correlation": 0.65, "note": "Shooters"},
            {"related_stat": "assists", "correlation": -0.20, "note": "Score-first fade"},
        ],
        "rebounds": [
            {"related_stat": "minutes", "correlation": 0.78, "note": "Floor time lock"},
            {"related_stat": "blocks", "correlation": 0.52, "note": "Rim presence"},
            {"related_stat": "points", "correlation": 0.40, "note": "Double-double stack", "boost": True},
        ],
        "assists": [
            {"related_stat": "minutes", "correlation": 0.80, "note": "Floor time lock"},
            {"related_stat": "turnovers", "correlation": 0.60, "note": "Usage link"},
            {"related_stat": "points", "correlation": 0.45, "note": "Playmaker stack"},
        ],
        "three_pointers": [
            {"related_stat": "points", "correlation": 0.65, "note": "Scoring stack", "boost": True},
            {"related_stat": "field_goals_attempted", "correlation": 0.55, "note": "Volume"},
        ],
        "steals": [
            {"related_stat": "assists", "correlation": 0.38, "note": "Active hands guard"},
            {"related_stat": "minutes", "correlation": 0.65, "note": "Floor time"},
        ],
        "blocks": [
            {"related_stat": "rebounds", "correlation": 0.52, "note": "Rim protector", "boost": True},
            {"related_stat": "minutes", "correlation": 0.60, "note": "Floor time"},
        ],
    }

    corr_db = NBA_CORRELATIONS if request.sport.lower() == "nba" else NFL_CORRELATIONS
    related = corr_db.get(request.stat, [])

    # Build strategy suggestion
    boosted = [r for r in related if r.get("boost")]
    if boosted:
        boost_str = ", ".join(r["related_stat"] for r in boosted)
        strategy = (f"🔥 Correlated Boost: Pair {request.player} {request.stat} "
                    f"{request.direction} with {boost_str} for a stacked entry.")
    else:
        strategy = (f"If {request.player} {request.stat} goes {request.direction}, "
                    f"consider pairing with the top correlated stat.")

    return {
        "player": request.player,
        "stat": request.stat,
        "sport": request.sport,
        "direction": request.direction,
        "correlated_picks": related,
        "strategy": strategy,
        "has_boost": len(boosted) > 0,
    }


@router.post("/middling", response_model=MiddleCheckResponse)
async def detect_middle(request: MiddleCheckRequest):
    """Detect middling opportunities between DFS fixed lines and sharp book lines."""
    from math import erf, sqrt

    gap = abs(request.sharp_line - request.dfs_line)
    direction = "over_dfs" if request.sharp_line > request.dfs_line else "under_dfs"

    # Middling is profitable when the gap is large enough to "land in the middle"
    # For totals (points/yards), a gap >= 2.0 is noteworthy; >= 3.0 is strong
    is_middle = gap >= 2.0
    strength = "strong" if gap >= 3.0 else "moderate" if gap >= 2.0 else "weak"

    if is_middle:
        if direction == "over_dfs":
            action = (f"Bet OVER {request.dfs_line} on {request.dfs_platform}, "
                      f"UNDER {request.sharp_line} on {request.sharp_book}. "
                      f"Win both if actual lands between {request.dfs_line}–{request.sharp_line}.")
        else:
            action = (f"Bet UNDER {request.dfs_line} on {request.dfs_platform}, "
                      f"OVER {request.sharp_line} on {request.sharp_book}. "
                      f"Win both if actual lands between {request.sharp_line}–{request.dfs_line}.")
    else:
        action = "Gap too small for a profitable middle. Monitor for line movement."

    # Odds helpers
    def _american_to_decimal(odds: int) -> float:
        return (odds / 100) + 1 if odds > 0 else (100 / abs(odds)) + 1

    def _profit_per_unit(odds: int) -> float:
        return _american_to_decimal(odds) - 1.0

    def _norm_cdf(z: float) -> float:
        return 0.5 * (1 + erf(z / sqrt(2)))

    # Use sharp line as market fair mean; fallback sigma by sport/stat family.
    if request.line_std is not None and request.line_std > 0:
        sigma = request.line_std
    else:
        stat_l = request.stat.lower()
        if "point" in stat_l:
            sigma = 7.5
        elif "yard" in stat_l:
            sigma = 18.0
        elif "assist" in stat_l:
            sigma = 3.5
        elif "rebound" in stat_l:
            sigma = 4.0
        else:
            sigma = 6.0

    lo, hi = sorted([request.dfs_line, request.sharp_line])
    # Mean estimate blends sharp and DFS lines based on confidence in sharp market.
    mu = (request.market_confidence * request.sharp_line) + (
        (1.0 - request.market_confidence) * request.dfs_line
    )
    sigma *= (1.0 + ((1.0 - request.market_confidence) * 0.25))
    if abs(lo - round(lo)) < 1e-9 and abs(hi - round(hi)) < 1e-9:
        lo, hi = lo - 0.5, hi + 0.5
    z_lo = (lo - mu) / sigma
    z_hi = (hi - mu) / sigma
    p_middle = max(0.0, min(1.0, _norm_cdf(z_hi) - _norm_cdf(z_lo)))

    # Two-sided middle economics with 1 unit staked on each side.
    # Non-middle state is modeled as one bet wins and one loses.
    dfs_profit = _profit_per_unit(request.dfs_odds)
    sharp_profit = _profit_per_unit(request.sharp_odds)
    middle_profit = dfs_profit + sharp_profit
    one_side_profit = (dfs_profit - 1.0 + sharp_profit - 1.0) / 2.0
    middle_ev_units = p_middle * middle_profit + (1.0 - p_middle) * one_side_profit
    breakeven_p_middle = (
        -one_side_profit / (middle_profit - one_side_profit)
        if (middle_profit - one_side_profit) != 0
        else 1.0
    )

    return {
        "player_name": request.player_name,
        "stat": request.stat,
        "dfs_line": request.dfs_line,
        "sharp_line": request.sharp_line,
        "gap": round(gap, 1),
        "direction": direction,
        "is_middle": is_middle,
        "strength": strength,
        "action": action,
        "dfs_platform": request.dfs_platform,
        "sharp_book": request.sharp_book,
        "middle_probability_estimate": round(p_middle, 4),
        "middle_ev_units": round(middle_ev_units, 4),
        "breakeven_middle_probability": round(max(0.0, min(1.0, breakeven_p_middle)), 4),
        "assumed_std_dev": round(sigma, 2),
        "assumed_mean": round(mu, 4),
        "confidence_weight": round(request.market_confidence, 4),
    }


# ═══════════════════════════════
# Phase 2: "Daily Grind" Bulk EV Dashboard
# ═══════════════════════════════

# 10-minute cache to protect Odds API free-tier quota (500 req/mo)
_bulk_scan_cache: dict[str, dict] = {}
_scan_cache: dict[str, dict] = {}
_CACHE_TTL = 600  # seconds (10 minutes)

class BulkScanRequest(BaseModel):
    sport: str = "nba"
    max_games: int = 8


@router.post("/bulk-scan")
async def bulk_scan(request: BulkScanRequest):
    """Full slate scan: fetch ALL props, devig ALL rows, rank by edge."""
    job = job_store.create_job(
        domain="dfs",
        kind="bulk_scan",
        metadata={"sport": request.sport, "max_games": request.max_games},
    )
    job_store.mark_running(job["id"], message="DFS bulk scan started")
    cache_key = f"{request.sport}_{request.max_games}"
    now = _time.time()

    # Return cached data if fresh
    if cache_key in _bulk_scan_cache:
        cached = _bulk_scan_cache[cache_key]
        age = now - cached["timestamp"]
        if age < _CACHE_TTL:
            logger.info(f"Bulk scan cache HIT ({cache_key}), age={int(age)}s)")
            result = cached["data"].copy()
            result["cached"] = True
            result["cache_age_seconds"] = int(age)
            result["job_id"] = job["id"]
            job_store.mark_completed(job["id"], message="DFS bulk scan served from cache")
            return result
    try:
        from app.core import PropOddsClient, SleeperClient
        from app.core.clients import (
            filter_sleeper_markets,
            PropOddsAuthError,
            PropOddsPlanError,
        )
        from app.logic import evaluate_prop

        prop_odds = PropOddsClient()
        sleeper = SleeperClient()

        # Step 1: Get ALL props across ALL games
        try:
            all_props = await prop_odds.full_scan(
                sport=request.sport, max_games=request.max_games
            )
        except PropOddsAuthError as e:
            result = {
                "opportunities": [],
                "total_scanned": 0,
                "plays_found": 0,
                "games_scanned": 0,
                "cached": False,
                "cache_age_seconds": 0,
                "job_id": job["id"],
                "message": "Odds API authentication failed. Check backend .env PROP_ODDS_API_KEY.",
                "error": str(e),
            }
            job_store.mark_completed(
                job["id"],
                message="DFS bulk scan unavailable (odds auth failure)",
                metadata={"reason": "odds_auth"},
            )
            return result
        except PropOddsPlanError as e:
            result = {
                "opportunities": [],
                "total_scanned": 0,
                "plays_found": 0,
                "games_scanned": 0,
                "cached": False,
                "cache_age_seconds": 0,
                "job_id": job["id"],
                "message": "Odds API plan/quota blocked the request. Reduce scans or upgrade your plan.",
                "error": str(e),
            }
            job_store.mark_completed(
                job["id"],
                message="DFS bulk scan unavailable (odds plan/quota)",
                metadata={"reason": "odds_plan"},
            )
            return result

        if not all_props:
            return {
                "opportunities": [], "total_scanned": 0, "plays_found": 0,
                "message": "No props available for the current slate."
            }

        # Step 2: Filter to Sleeper-available markets
        allowed_names = None
        try:
            all_players = await sleeper.get_all_players(sport=request.sport)
            allowed_names = {
                f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip()
                for p in all_players.values()
                if p.get("team")
            }
        except Exception as e:
            logger.warning("Sleeper player metadata filter unavailable: %s", e)
        all_props = filter_sleeper_markets(all_props, request.sport, allowed_player_names=allowed_names)

        # Step 3: Build opposing-odds lookup for no-vig calculation
        odds_pairs: dict = {}
        for prop in all_props:
            pair_key = (
                prop["player_name"],
                prop["market"],
                prop.get("line", 0),
                prop.get("book", "unknown"),
            )
            side = prop.get("side", "").lower()
            odds_pairs.setdefault(pair_key, {})[side] = prop.get("odds", -110)

        # Step 4: Evaluate EVERY prop (both Overs and Unders)
        opportunities = []
        seen = set()
        for prop in all_props:
            side = prop.get("side", "").lower()
            if side not in ("over", "under"):
                continue
            key = f"{prop['player_name']}_{prop['market']}_{prop.get('book', '')}_{side}"
            if key in seen:
                continue
            seen.add(key)

            pair_key = (
                prop["player_name"], prop["market"],
                prop.get("line", 0), prop.get("book", "unknown"),
            )
            pair = odds_pairs.get(pair_key, {})
            # Opposing odds: if this is Over, opposing is Under and vice versa
            opposing_side = "under" if side == "over" else "over"
            opposing_odds = pair.get(opposing_side)

            opp = evaluate_prop(
                player_id=prop.get("event_id", "unknown"),
                player_name=prop["player_name"],
                market=prop["market"],
                line=prop.get("line", 0),
                sharp_odds=prop.get("odds", -110),
                sharp_book=prop.get("book", "unknown"),
                opposing_odds=opposing_odds,
            )

            opportunities.append({
                "player_name": opp.player_name,
                "market": opp.market,
                "line": opp.line,
                "side": side,
                "sharp_odds": opp.sharp_odds,
                "sharp_book": opp.sharp_book,
                "edge_pct": round(opp.edge * 100, 2),
                "is_play": opp.is_play,
                "opposing_odds": opp.opposing_odds,
                "sharp_implied_prob": round((opp.sharp_implied_prob or 0) * 100, 2),
                "opposing_implied_prob": round((opp.opposing_implied_prob or 0) * 100, 2) if opp.opposing_implied_prob else None,
                "fair_prob": round((opp.fair_prob or 0) * 100, 2) if opp.fair_prob else None,
                "fixed_implied_prob": round((opp.fixed_implied_prob or 0) * 100, 2),
                "vig_pct": opp.vig_pct,
            })

        opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
        calculated_count = sum(1 for o in opportunities if o.get("is_calculated"))
        uncalculated_count = max(0, len(opportunities) - calculated_count)

        result = {
            "opportunities": opportunities,
            "total_scanned": len(opportunities),
            "plays_found": sum(1 for o in opportunities if o["is_play"]),
            "games_scanned": request.max_games,
        }

        # Store in cache
        _bulk_scan_cache[cache_key] = {"data": result, "timestamp": _time.time()}
        logger.info(f"Bulk scan cache STORED ({cache_key})")

        result["cached"] = False
        result["cache_age_seconds"] = 0
        result["job_id"] = job["id"]

        # Notify via WebSocket if plays found
        if result["plays_found"] > 0:
            from services.notification_manager import send_toast
            await send_toast(
                title="DFS Scan Complete",
                message=f"Found {result['plays_found']} +EV plays in {request.sport.upper()}",
                type="success"
            )

        job_store.mark_completed(
            job["id"],
            message="DFS bulk scan completed",
            metadata={"plays_found": result["plays_found"], "total_scanned": result["total_scanned"]},
        )
        return result
    except Exception as e:
        logger.error(f"Bulk scan failed: {e}")
        import traceback
        traceback.print_exc()
        job_store.mark_failed(job["id"], str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════
# Phase 3: "Prop Professor" Player Research
# ═══════════════════════════════

class PlayerResearchRequest(BaseModel):
    player_name: str
    stat: str = "points"
    line: float = 24.5
    game_logs: List[float] = []  # Client can pass known logs; else we estimate


@router.post("/player-research")
async def player_research(request: PlayerResearchRequest):
    """Deep research for a single player prop: hit rates, streaks, trend."""
    logs = request.game_logs
    line = request.line

    # If no logs provided, generate mock data for demo
    # In production, this would call a stats API (balldontlie, Sleeper, etc.)
    if not logs:
        import random
        random.seed(hash(request.player_name))
        # Generate 20 realistic game logs
        base = line
        logs = [round(base + random.gauss(0, base * 0.25), 1) for _ in range(20)]

    # Calculate hit rates
    l5 = logs[:5]
    l10 = logs[:10]
    l20 = logs[:20]

    def hit_rate(games, threshold):
        hits = sum(1 for g in games if g > threshold)
        return round(hits / len(games) * 100, 1) if games else 0

    l5_hit = hit_rate(l5, line)
    l10_hit = hit_rate(l10, line)
    l20_hit = hit_rate(l20, line)

    # Trend: are recent games trending up or down vs the line?
    recent_avg = sum(l5) / len(l5) if l5 else 0
    overall_avg = sum(l20) / len(l20) if l20 else 0
    trend = "hot" if recent_avg > overall_avg * 1.05 else "cold" if recent_avg < overall_avg * 0.95 else "neutral"

    # Streak
    streak = 0
    for g in logs:
        if g > line:
            streak += 1
        else:
            break

    return {
        "player_name": request.player_name,
        "stat": request.stat,
        "line": line,
        "game_logs": logs[:20],
        "hit_rates": {
            "l5": l5_hit,
            "l10": l10_hit,
            "l20": l20_hit,
        },
        "averages": {
            "l5": round(recent_avg, 1),
            "l10": round(sum(l10) / len(l10), 1) if l10 else 0,
            "l20": round(overall_avg, 1),
        },
        "trend": trend,
        "current_streak": streak,
        "recommendation": "OVER" if l10_hit >= 60 else "UNDER" if l10_hit <= 40 else "PASS",
    }


@router.get("/odds")
async def get_odds_feed():
    """Placeholder: fetch latest odds from Prop Odds API or cached data."""
    try:
        from app.logic import odds_fetcher
        data = odds_fetcher.get_latest()
        return {"odds": data}
    except Exception:
        return {
            "odds": [],
            "message": "Configure PROP_ODDS_API_KEY in .env to enable live odds feed",
        }


# ── Sniper Service ──
@router.get("/sniper/dashboard")
async def sniper_dashboard():
    """Get live sniper data: alerts, movements, stats."""
    try:
        from services.dfs_sniper import get_sniper
        sniper = get_sniper()
        return sniper.get_dashboard_data()
    except Exception as e:
        return {
            "alerts": [], "alert_count": 0,
            "recent_movements": [], "movement_count": 0,
            "error": str(e),
        }


@router.post("/sniper/update-sharp")
async def update_sharp_line(
    player: str, stat: str, book: str, line: float
):
    """Update a sharp book line — triggers snipe detection."""
    from services.dfs_sniper import get_sniper
    sniper = get_sniper()
    movement = sniper.update_sharp_line(player, stat, book, line)
    return {
        "updated": True,
        "movement_detected": movement is not None,
        "active_alerts": len(sniper.active_alerts),
    }


@router.post("/sniper/update-dfs")
async def update_dfs_line(
    player: str, stat: str, platform: str, line: float
):
    """Update a DFS platform line for comparison."""
    from services.dfs_sniper import get_sniper
    sniper = get_sniper()
    sniper.update_dfs_line(player, stat, platform, line)
    return {"updated": True}


# ── Correlation Engine ──
@router.post("/correlation/suggest")
async def suggest_correlations(request: CorrelationRequest):
    """Get correlated picks using the correlation engine."""
    try:
        from services.correlation_engine import get_correlation_engine
        engine = get_correlation_engine()

        # Map stat to position (simplified detection)
        position = _infer_position(request.stat)

        suggestions = engine.get_correlated_picks(
            sport="NFL",
            position=position,
            stat=request.stat,
        )
        return {
            "player": request.player,
            "stat": request.stat,
            "position": position,
            "suggestions": suggestions,
        }
    except Exception as e:
        return {"error": str(e), "suggestions": []}


@router.post("/correlation/parlay-ev")
async def parlay_ev(legs: List[Dict[str, Any]]):
    """Calculate combined EV for a multi-leg entry."""
    from services.correlation_engine import get_correlation_engine
    engine = get_correlation_engine()
    result = engine.calculate_parlay_ev(legs)
    return result


def _infer_position(stat: str) -> str:
    """Infer player position from stat type."""
    stat_lower = stat.lower()
    if "passing" in stat_lower or "completion" in stat_lower:
        return "QB"
    elif "rushing" in stat_lower:
        return "RB"
    elif "receiving" in stat_lower or "reception" in stat_lower:
        return "WR1"
    elif "rebound" in stat_lower:
        return "C"
    elif "assist" in stat_lower:
        return "PG"
    elif "point" in stat_lower:
        return "PG"
    return "QB"  # default


_SCAN_PLATFORM_OPTIONS = {"any", "sleeper", "prizepicks", "underdog"}
_SLIP_PLATFORM = "sleeper"  # direct approved PP/UD feed not integrated yet
_CORE_MARKETS_BY_SPORT: Dict[str, Set[str]] = {
    "nba": {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
        "player_blocks",
        "player_steals",
        "player_turnovers",
    },
    "nfl": {
        "player_pass_yds",
        "player_pass_tds",
        "player_pass_completions",
        "player_pass_attempts",
        "player_pass_interceptions",
        "player_rush_yds",
        "player_rush_attempts",
        "player_rush_tds",
        "player_receptions",
        "player_reception_yds",
        "player_reception_tds",
        "player_anytime_td",
        "player_kicking_points",
    },
    "mlb": {
        "pitcher_strikeouts",
        "pitcher_hits_allowed",
        "pitcher_walks",
        "pitcher_outs",
        "batter_hits",
        "batter_total_bases",
        "batter_rbis",
        "batter_runs_scored",
        "batter_walks",
        "batter_strikeouts",
        "batter_stolen_bases",
        "batter_home_runs",
    },
    "soccer": {
        "player_shots",
        "player_shots_on_target",
        "player_goal_scorer_anytime",
    },
}


def _canonical_platform(value: str) -> str:
    raw = str(value or "sleeper").strip().lower()
    if raw in _SCAN_PLATFORM_OPTIONS:
        return raw
    return "sleeper"


def _fallback_sleeper_market_check(market: str) -> bool:
    """Best-effort compatibility check when scanner flags are missing."""
    try:
        from app.core.clients import SLEEPER_AVAILABLE_MARKETS

        key = str(market or "").strip()
        return any(key in markets for markets in SLEEPER_AVAILABLE_MARKETS.values())
    except Exception:
        return False


def _pick_is_sleeper_compatible(pick: Dict[str, Any]) -> bool:
    if "available_on_sleeper_compatible" in pick:
        return bool(pick.get("available_on_sleeper_compatible"))
    return _fallback_sleeper_market_check(str(pick.get("market", "")))


def _filter_core_markets(props: List[Dict[str, Any]], sport: str) -> List[Dict[str, Any]]:
    allowed = _CORE_MARKETS_BY_SPORT.get(str(sport or "").strip().lower())
    if not allowed:
        return props
    return [p for p in props if str(p.get("market", "")).strip() in allowed]


# ═══════════════════════════════════
# Smart Scan Pipeline (ported from apex-dfs)
# ═══════════════════════════════════

class ScanRequest(BaseModel):
    sport: str = "nfl"
    scope: str = "smart"  # smart | full
    max_games: int = 8
    trending_limit: int = 80
    sleeper_markets_only: bool = True
    target_platform: str = "sleeper"  # any | sleeper | prizepicks | underdog
    consensus_min_books: Optional[int] = None
    consensus_line_window: Optional[float] = None
    consensus_main_line_only: Optional[bool] = None
    consensus_min_trend_count: Optional[int] = None


class SaveScanVersionRequest(BaseModel):
    sport: str = "nba"
    scan_scope: str = "smart"
    stats: Dict[str, Any] = Field(default_factory=dict)
    results: List[Dict[str, Any]] = Field(default_factory=list)
    slip: List[Dict[str, Any]] = Field(default_factory=list)
    locked_keys: List[str] = Field(default_factory=list)


@router.get("/settings")
async def get_current_settings():
    """Return current DFS strategy settings from DFS core."""
    try:
        from app.core import get_settings
        settings = get_settings()
        return {
            "edge_threshold_pct": settings.edge_threshold * 100,
            "dfs_fixed_implied_prob_pct": settings.dfs_fixed_implied_prob * 100,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class EdgeCheckRequest(BaseModel):
    player_name: str
    market: str
    line: float
    sharp_odds: int
    sharp_book: str = "pinnacle"

class GenerateSlipsRequest(BaseModel):
    opportunities: List[Dict[str, Any]]
    slip_sizes: List[int] = [3, 4, 5, 6]
    top_n: int = 10
    min_edge: float = 0.0
    book: str = "sleeper"   # sleeper only (PP/UD direct feed not integrated)
    mode: str = "power"     # power | flex | standard | insured
    sport: str = "nba"      # nfl | nba | mlb | soccer — used for market filtering
    prioritize_dfs_lines: bool = False


@router.get("/trending")
async def get_trending(sport: str = "nfl", limit: int = 25):
    """Fetch trending players from Sleeper (free, no API cost)."""
    try:
        from app.core import SleeperClient
        client = SleeperClient()
        trending = await client.get_trending_with_teams(sport=sport, limit=limit)
        return {"trending": trending, "count": len(trending)}
    except Exception as e:
        logger.error(f"Trending fetch failed: {e}")
        return {"trending": [], "count": 0, "error": str(e)}


@router.post("/scan")
async def scan_opportunities(request: ScanRequest):
    """Unified DFS scan: smart (targeted) or full-market scope."""
    scan_scope = str(request.scope or "smart").strip().lower()
    if scan_scope not in {"smart", "full"}:
        raise HTTPException(status_code=400, detail="scope must be 'smart' or 'full'")
    target_platform = _canonical_platform(request.target_platform)
    effective_max_games = max(1, min(int(request.max_games), 20))
    effective_trending_limit = max(1, min(int(request.trending_limit), 200))

    cfg = get_config()
    dfs_cfg = cfg.get("dfs", {}) if isinstance(cfg, dict) else {}
    consensus_cfg = dfs_cfg.get("consensus", {}) if isinstance(dfs_cfg, dict) else {}
    consensus_weights = consensus_cfg.get("weights", {}) if isinstance(consensus_cfg, dict) else {}
    min_books = int(request.consensus_min_books) if request.consensus_min_books is not None else int(consensus_cfg.get("min_books", 1))
    line_window = float(request.consensus_line_window) if request.consensus_line_window is not None else float(consensus_cfg.get("line_window", 1.0))
    main_line_only = bool(request.consensus_main_line_only) if request.consensus_main_line_only is not None else bool(consensus_cfg.get("main_line_only", True))
    min_trend_count = int(request.consensus_min_trend_count) if request.consensus_min_trend_count is not None else int(consensus_cfg.get("min_trend_count", 0))

    job = job_store.create_job(
        domain="dfs",
        kind="smart_scan" if scan_scope == "smart" else "full_scan",
        metadata={
            "scan_scope": scan_scope,
            "sport": request.sport,
            "target_platform": target_platform,
            "max_games": effective_max_games,
            "trending_limit": effective_trending_limit if scan_scope == "smart" else 0,
            "consensus_min_books": min_books,
            "consensus_line_window": line_window,
            "consensus_main_line_only": main_line_only,
            "consensus_min_trend_count": min_trend_count,
        },
    )
    job_store.mark_running(job["id"], message=f"DFS {scan_scope} scan started")
    try:
        weight_sig = "|".join(
            f"{k}:{consensus_weights.get(k, 0)}"
            for k in ("bookmaker", "pinnacle", "fanduel", "draftkings")
        )
        cache_key = (
            f"v6|scope:{scan_scope}|{request.sport}|games:{effective_max_games}|trend:{effective_trending_limit}"
            f"|sleeper:{int(bool(request.sleeper_markets_only))}"
            f"|platform:{target_platform}"
            f"|books:{min_books}|window:{line_window}|main:{int(main_line_only)}|trendmin:{min_trend_count}"
            f"|w:{weight_sig}"
        )
        cached = _scan_cache.get(cache_key)
        cache_ttl = 120 if scan_scope == "full" else 45
        if cached:
            age = _time.time() - cached.get("timestamp", 0)
            if age < cache_ttl:
                data = dict(cached.get("data", {}))
                data["cached"] = True
                data["cache_age_seconds"] = int(age)
                data["job_id"] = job["id"]
                job_store.mark_completed(job["id"], message=f"DFS {scan_scope} scan served from cache")
                return data

        from app.core import SleeperClient, PropOddsClient
        from app.core.clients import (
            filter_sleeper_markets,
            PropOddsAuthError,
            PropOddsPlanError,
        )
        from app.logic import evaluate_prop
        
        sleeper = SleeperClient()
        prop_odds = PropOddsClient()
        
        # Step 1: Select source universe
        trending: List[Dict[str, Any]] = []
        if scan_scope == "smart":
            try:
                trending = await sleeper.get_trending_with_teams(
                    sport=request.sport, limit=effective_trending_limit
                )
            except Exception as e:
                logger.error(f"Sleeper fetch failed during smart scan: {e}")
                result = {
                    "opportunities": [],
                    "total_scanned": 0,
                    "plays_found": 0,
                    "games_queried": 0,
                    "trending_players": 0,
                    "scan_scope": scan_scope,
                    "target_platform": target_platform,
                    "cached": False,
                    "cache_age_seconds": 0,
                    "job_id": job["id"],
                    "message": "Sleeper is temporarily unreachable. Check network/DNS or SLEEPER_BASE_URL, then retry.",
                    "error": str(e),
                }
                job_store.mark_completed(
                    job["id"],
                    message="DFS smart scan unavailable (Sleeper connection failure)",
                    metadata={"reason": "sleeper_unreachable"},
                )
                return result

            if not trending:
                return {
                    "opportunities": [],
                    "total_scanned": 0,
                    "plays_found": 0,
                    "games_queried": 0,
                    "trending_players": 0,
                    "scan_scope": scan_scope,
                    "target_platform": target_platform,
                    "message": "No trending players on Sleeper.",
                }

        # Step 2: Pull odds data
        try:
            if scan_scope == "smart":
                all_props = await prop_odds.smart_scan(
                    trending_players=trending,
                    sport=request.sport,
                    max_games=effective_max_games,
                )
            else:
                all_props = await prop_odds.full_scan(
                    sport=request.sport,
                    max_games=effective_max_games,
                )
        except PropOddsAuthError as e:
            result = {
                "opportunities": [],
                "total_scanned": 0,
                "plays_found": 0,
                "games_queried": 0,
                "trending_players": len(trending),
                "scan_scope": scan_scope,
                "target_platform": target_platform,
                "cached": False,
                "cache_age_seconds": 0,
                "job_id": job["id"],
                "message": "Odds API authentication failed. Check backend .env PROP_ODDS_API_KEY.",
                "error": str(e),
            }
            job_store.mark_completed(
                job["id"],
                message=f"DFS {scan_scope} scan unavailable (odds auth failure)",
                metadata={"reason": "odds_auth"},
            )
            return result
        except PropOddsPlanError as e:
            result = {
                "opportunities": [],
                "total_scanned": 0,
                "plays_found": 0,
                "games_queried": 0,
                "trending_players": len(trending),
                "scan_scope": scan_scope,
                "target_platform": target_platform,
                "cached": False,
                "cache_age_seconds": 0,
                "job_id": job["id"],
                "message": "Odds API plan/quota blocked player props. Reduce scans or upgrade your plan.",
                "error": str(e),
            }
            job_store.mark_completed(
                job["id"],
                message=f"DFS {scan_scope} scan unavailable (odds plan/quota)",
                metadata={"reason": "odds_plan"},
            )
            return result
        except Exception as e:
            logger.error(f"Prop odds scan failed during {scan_scope} scan: {e}")
            result = {
                "opportunities": [],
                "total_scanned": 0,
                "plays_found": 0,
                "games_queried": 0,
                "trending_players": len(trending),
                "scan_scope": scan_scope,
                "target_platform": target_platform,
                "cached": False,
                "cache_age_seconds": 0,
                "job_id": job["id"],
                "message": "Odds provider is temporarily unreachable. Retry in a moment.",
                "error": str(e),
            }
            job_store.mark_completed(
                job["id"],
                message=f"DFS {scan_scope} scan unavailable (odds provider failure)",
                metadata={"reason": "odds_unreachable"},
            )
            return result

        # Keep only core prop markets (no combo markets like PRA / PTS+REB+AST).
        all_props = _filter_core_markets(all_props, request.sport)

        # Load Sleeper player metadata for compatibility checks/flags.
        allowed_names: Optional[Set[str]] = None
        try:
            all_players = await sleeper.get_all_players(sport=request.sport)
            allowed_names = {
                f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip()
                for p in all_players.values()
                if p.get("team")
            }
        except Exception as e:
            logger.warning("Sleeper player metadata filter unavailable: %s", e)

        # Step 2.5: Optional pre-filter to Sleeper-compatible rows.
        apply_sleeper_filter = bool(request.sleeper_markets_only)
        if target_platform in {"prizepicks", "underdog"}:
            # Don't hard-filter to Sleeper when explicitly exploring other platforms.
            apply_sleeper_filter = False
        if apply_sleeper_filter:
            all_props = filter_sleeper_markets(all_props, request.sport, allowed_player_names=allowed_names)

        if not all_props:
            if apply_sleeper_filter or target_platform == "sleeper":
                msg = "No Sleeper-compatible props found for this scan scope."
            elif target_platform in {"prizepicks", "underdog"}:
                msg = f"No {target_platform.title()} rows found for this scan scope."
            else:
                msg = "No props found for this scan scope."
            return {"opportunities": [], "total_scanned": 0, "plays_found": 0,
                    "games_queried": 0, "trending_players": len(trending), "scan_scope": scan_scope,
                    "target_platform": target_platform,
                    "message": msg}
        
        # Step 3: Consensus engine + line-quality filters.
        from services.consensus_engine import build_consensus_candidates

        trend_counts = {
            str(p.get("name", "")).strip().lower(): int(p.get("count", 0) or 0)
            for p in trending
            if p.get("name")
        }
        effective_min_trend_count = min_trend_count if scan_scope == "smart" else 0
        consensus_rows = build_consensus_candidates(
            props=all_props,
            trend_counts=trend_counts,
            weights_raw=consensus_weights,
            min_books=min_books,
            line_window=line_window,
            main_line_only=main_line_only,
            min_trend_count=effective_min_trend_count,
        )
        if scan_scope == "smart" and effective_min_trend_count > 0 and len(consensus_rows) <= 2:
            # Auto-relax this gate to avoid over-pruning scans to near-empty output.
            consensus_rows = build_consensus_candidates(
                props=all_props,
                trend_counts=trend_counts,
                weights_raw=consensus_weights,
                min_books=min_books,
                line_window=line_window,
                main_line_only=main_line_only,
                min_trend_count=0,
            )
        # Keep going even if consensus rows are empty; we still render raw core props.
        if not consensus_rows:
            consensus_rows = []

        # Step 4: Build output rows from all core props.
        # Consensus rows are "calculated"; non-consensus rows are still shown.
        from services.consensus_engine import canonical_book as canonical_consensus_book

        rows_by_exact_key: Dict[Tuple[str, str, float, str], List[Dict[str, Any]]] = {}
        for row in all_props:
            side = str(row.get("side", "")).strip().lower()
            if side not in {"over", "under"}:
                continue
            player = str(row.get("player_name", "")).strip()
            market = str(row.get("market", "")).strip()
            try:
                line = float(row.get("line", 0) or 0)
            except Exception:
                line = 0.0
            rows_by_exact_key.setdefault((player, market, line, side), []).append(row)

        consensus_by_exact_key: Dict[Tuple[str, str, float, str], Dict[str, Any]] = {}
        for prop in consensus_rows:
            side = str(prop.get("side", "")).strip().lower()
            if side not in {"over", "under"}:
                continue
            player = str(prop.get("player_name", "")).strip()
            market = str(prop.get("market", "")).strip()
            try:
                line = float(prop.get("line", 0) or 0)
            except Exception:
                line = 0.0
            consensus_by_exact_key[(player, market, line, side)] = prop

        max_weight = 0.0
        for v in (consensus_weights or {}).values():
            try:
                fv = float(v)
            except Exception:
                continue
            if fv > 0:
                max_weight += fv

        def _american_to_implied_pct(odds: int) -> float:
            if odds < 0:
                return (abs(odds) / (abs(odds) + 100.0)) * 100.0
            return (100.0 / (odds + 100.0)) * 100.0

        def _book_entries_for_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            best_by_book: Dict[str, int] = {}
            for row in rows:
                book = canonical_consensus_book(row.get("book", ""))
                try:
                    odds = int(row.get("odds"))
                except Exception:
                    continue
                prev = best_by_book.get(book)
                if prev is None or abs(odds) < abs(prev):
                    best_by_book[book] = odds
            entries: List[Dict[str, Any]] = []
            for book, odds in best_by_book.items():
                w = 0.0
                try:
                    w = float((consensus_weights or {}).get(book, 0.0))
                except Exception:
                    w = 0.0
                entries.append(
                    {
                        "book": book,
                        "odds": int(odds),
                        "weight": max(0.0, w),
                        "implied_prob_pct": round(_american_to_implied_pct(int(odds)), 2),
                    }
                )
            entries.sort(key=lambda x: (float(x.get("weight", 0.0)), str(x.get("book", ""))), reverse=True)
            return entries

        def _median_odds(values: List[int]) -> int:
            if not values:
                return -110
            vals = sorted(int(v) for v in values)
            n = len(vals)
            m = n // 2
            if n % 2 == 1:
                return int(vals[m])
            return int(round((vals[m - 1] + vals[m]) / 2.0))

        book_entries_by_key: Dict[Tuple[str, str, float, str], List[Dict[str, Any]]] = {}
        fallback_odds_by_key: Dict[Tuple[str, str, float, str], int] = {}
        for key, rows in rows_by_exact_key.items():
            entries = _book_entries_for_rows(rows)
            book_entries_by_key[key] = entries
            weighted = [int(e["odds"]) for e in entries if float(e.get("weight", 0.0)) > 0.0]
            if weighted:
                fallback_odds_by_key[key] = _median_odds(weighted)
            else:
                fallback_odds_by_key[key] = _median_odds([int(e["odds"]) for e in entries])

        opportunities = []
        trending_names = {p.get("name", "").lower() for p in trending if p.get("name")}
        sleeper_compat_cache: Dict[Tuple[str, str], bool] = {}

        def _is_sleeper_compatible(player_name: str, market_key: str) -> bool:
            k = (player_name.strip().lower(), market_key.strip().lower())
            cached_value = sleeper_compat_cache.get(k)
            if cached_value is not None:
                return cached_value
            value = bool(
                filter_sleeper_markets(
                    [{"player_name": player_name, "market": market_key}],
                    request.sport,
                    allowed_player_names=allowed_names,
                )
            )
            sleeper_compat_cache[k] = value
            return value

        sorted_keys = sorted(rows_by_exact_key.keys(), key=lambda t: (t[0].lower(), t[1].lower(), t[2], t[3]))
        for player_name, market, line_value, side in sorted_keys:
            key = (player_name, market, line_value, side)
            opp_side = "under" if side == "over" else "over"
            opp_key = (player_name, market, line_value, opp_side)

            entries = book_entries_by_key.get(key, [])
            platform_books = {str(e.get("book", "")) for e in entries if str(e.get("book", "")).strip()}
            available_on_prizepicks_direct = "prizepicks" in platform_books
            available_on_underdog_direct = "underdog" in platform_books
            available_on_sleeper_direct = "sleeper" in platform_books
            available_on_sleeper_compatible = _is_sleeper_compatible(player_name, market)

            if target_platform == "sleeper" and not available_on_sleeper_compatible:
                continue
            if target_platform == "prizepicks" and not available_on_prizepicks_direct:
                continue
            if target_platform == "underdog" and not available_on_underdog_direct:
                continue

            consensus_prop = consensus_by_exact_key.get(key)
            opposing_consensus = consensus_by_exact_key.get(opp_key)
            calculated = bool(consensus_prop)

            if calculated:
                sharp_odds = int(consensus_prop.get("consensus_odds", fallback_odds_by_key.get(key, -110)))
                opposing_odds = (
                    int(opposing_consensus.get("consensus_odds"))
                    if isinstance(opposing_consensus, dict) and opposing_consensus.get("consensus_odds") is not None
                    else None
                )
                book_odds_payload = consensus_prop.get("book_odds", entries)
                if not isinstance(book_odds_payload, list):
                    book_odds_payload = entries
                calc_reason = None
            else:
                sharp_odds = int(fallback_odds_by_key.get(key, -110))
                opposing_odds = int(fallback_odds_by_key[opp_key]) if opp_key in fallback_odds_by_key else None
                book_odds_payload = entries
                calc_reason = "No weighted consensus from configured books."

            opp = evaluate_prop(
                player_id=(rows_by_exact_key.get(key, [{}])[0] or {}).get("event_id", "unknown"),
                player_name=player_name,
                market=market,
                line=line_value,
                sharp_odds=sharp_odds,
                sharp_book="apex_consensus" if calculated else "raw_market",
                opposing_odds=opposing_odds,
            )

            weighted_books_used = len([e for e in entries if float(e.get("weight", 0.0)) > 0.0])
            total_weight = sum(float(e.get("weight", 0.0)) for e in entries if float(e.get("weight", 0.0)) > 0.0)
            fallback_coverage = round((total_weight / max_weight) * 100.0, 2) if max_weight > 0 else 0.0
            first_row = (rows_by_exact_key.get(key, [{}]) or [{}])[0]
            consensus_books_used = weighted_books_used
            consensus_coverage = fallback_coverage
            if calculated:
                try:
                    consensus_books_used = int(consensus_prop.get("books_used", weighted_books_used))
                except Exception:
                    consensus_books_used = weighted_books_used
                try:
                    consensus_coverage = float(consensus_prop.get("weight_coverage_pct", fallback_coverage))
                except Exception:
                    consensus_coverage = fallback_coverage

            opportunities.append({
                "player_name": opp.player_name,
                "market": opp.market,
                "line": opp.line,
                "side": side,
                "sharp_odds": opp.sharp_odds,
                "apex_odds": opp.sharp_odds,
                "sharp_book": opp.sharp_book,
                "edge_pct": round(opp.edge * 100, 2) if calculated else 0.0,
                "is_play": bool(opp.is_play) if calculated else False,
                "is_calculated": calculated,
                "calc_reason": calc_reason,
                "is_trending": player_name.lower() in trending_names,
                "opposing_odds": opp.opposing_odds,
                "sharp_implied_prob": round((opp.sharp_implied_prob or 0) * 100, 2),
                "opposing_implied_prob": round((opp.opposing_implied_prob or 0) * 100, 2) if opp.opposing_implied_prob else None,
                "fair_prob": round((opp.fair_prob or 0) * 100, 2) if opp.fair_prob else None,
                "fixed_implied_prob": round((opp.fixed_implied_prob or 0) * 100, 2),
                "vig_pct": opp.vig_pct,
                "consensus_prob_pct": consensus_prop.get("consensus_prob_pct") if calculated else None,
                "books_used": consensus_books_used,
                "weight_coverage_pct": consensus_coverage,
                "book_odds": list(book_odds_payload),
                "available_books": sorted(platform_books),
                "available_on_sleeper_compatible": available_on_sleeper_compatible,
                "available_on_sleeper_direct": available_on_sleeper_direct,
                "available_on_prizepicks_direct": available_on_prizepicks_direct,
                "available_on_underdog_direct": available_on_underdog_direct,
                "eligible_for_slip": available_on_sleeper_compatible,
                "slip_platform": _SLIP_PLATFORM,
                "commence_time": first_row.get("commence_time"),
                "home_team": first_row.get("home_team"),
                "away_team": first_row.get("away_team"),
            })
        
        opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
        calculated_count = sum(1 for o in opportunities if o.get("is_calculated"))
        uncalculated_count = max(0, len(opportunities) - calculated_count)

        result = {
            "opportunities": opportunities,
            "total_scanned": len(opportunities),
            "plays_found": sum(1 for o in opportunities if o["is_play"]),
            "games_queried": (
                min(effective_max_games, (len(all_props) // 20) + 1)
                if scan_scope == "smart"
                else effective_max_games
            ),
            "trending_players": len(trending),
            "scan_scope": scan_scope,
            "target_platform": target_platform,
            "slip_platform": _SLIP_PLATFORM,
            "platform_notice": "Slip builder is currently enforced to Sleeper-compatible rows.",
            "calculated_count": calculated_count,
            "uncalculated_count": uncalculated_count,
            "cached": False,
            "cache_age_seconds": 0,
            "job_id": job["id"],
        }
        if not opportunities:
            if target_platform == "sleeper":
                result["message"] = "No Sleeper-compatible consensus rows matched the current filters."
            elif target_platform in {"prizepicks", "underdog"}:
                result["message"] = f"No direct {target_platform.title()} rows matched the current filters."
            else:
                result["message"] = "No rows matched the current filters."
        elif calculated_count == 0:
            result["message"] = "Showing core props only. No weighted-consensus rows matched current filters."
        _scan_cache[cache_key] = {"data": result, "timestamp": _time.time()}
        job_store.mark_completed(
            job["id"],
            message=f"DFS {scan_scope} scan completed",
            metadata={"plays_found": result["plays_found"], "total_scanned": result["total_scanned"]},
        )
        return result
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        import traceback
        traceback.print_exc()
        job_store.mark_failed(job["id"], str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan-history")
async def save_scan_history_version(request: SaveScanVersionRequest):
    """Persist a DFS scan version (results + stats + slip snapshot)."""
    try:
        from services import dfs_scan_store

        payload = {
            "sport": request.sport,
            "scan_scope": request.scan_scope,
            "stats": request.stats,
            "results": request.results,
            "slip": request.slip,
            "locked_keys": request.locked_keys,
        }
        return await dfs_scan_store.save_scan_version(payload)
    except Exception as e:
        logger.error("Failed to save DFS scan history version: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scan-history")
async def list_scan_history_versions(limit: int = 40):
    """List saved DFS scan versions (newest first)."""
    try:
        from services import dfs_scan_store

        rows = await dfs_scan_store.list_scan_versions(limit=max(1, min(limit, 200)))
        return {"versions": rows, "count": len(rows)}
    except Exception as e:
        logger.error("Failed to list DFS scan history versions: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scan-history/{version_id}")
async def get_scan_history_version(version_id: str):
    """Get a single saved DFS scan version by id."""
    try:
        from services import dfs_scan_store

        row = await dfs_scan_store.get_scan_version(version_id)
        if not row:
            raise HTTPException(status_code=404, detail="Scan version not found")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to load DFS scan history version %s: %s", version_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/scan-history/{version_id}")
async def delete_scan_history_version(version_id: str):
    """Delete a saved DFS scan version by id."""
    try:
        from services import dfs_scan_store

        deleted = await dfs_scan_store.delete_scan_version(version_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Scan version not found")
        return {"deleted": True, "id": version_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete DFS scan history version %s: %s", version_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/check-edge")
async def check_edge(request: EdgeCheckRequest):
    """Quick edge check for a manually entered prop."""
    try:
        from app.logic import evaluate_prop
        
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
            "edge_pct": round(opp.edge * 100, 2),
            "is_play": opp.is_play,
            "recommendation": "✅ PLAY" if opp.is_play else "❌ PASS"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-slips")
async def generate_slips(request: GenerateSlipsRequest):
    """Auto-build optimal parlays from scanned opportunities."""
    try:
        from app.logic import generate_top_slips

        eligible = [o for o in request.opportunities if _pick_is_sleeper_compatible(o)]
        if not eligible:
            return {
                "slips": [],
                "total_generated": 0,
                "book": _SLIP_PLATFORM,
                "mode": "power",
                "platform_notice": "Slip builder currently supports Sleeper-compatible rows only.",
                "message": "No Sleeper-compatible rows available for slip optimization.",
            }

        slips = generate_top_slips(
            opportunities=eligible,
            slip_sizes=request.slip_sizes,
            top_n=request.top_n,
            min_edge=request.min_edge,
            book=_SLIP_PLATFORM,
            mode="power",
            sport=request.sport,
            prioritize_dfs_lines=request.prioritize_dfs_lines,
        )

        return {
            "slips": slips,
            "total_generated": len(slips),
            "book": _SLIP_PLATFORM,
            "mode": "power",
            "platform_notice": "Slip builder currently supports Sleeper-compatible rows only.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ManualSlipRequest(BaseModel):
    picks: List[Dict[str, Any]]  # Each pick has player_name, market, line, sharp_odds, edge_pct
    platform: str = "sleeper"


@router.post("/manual-slip-ev")
async def manual_slip_ev(request: ManualSlipRequest):
    """Calculate EV for a manually-built slip."""
    try:
        from app.logic.slip_optimizer import calculate_slip_ev

        picks = request.picks
        platform = _canonical_platform(request.platform)
        if platform != _SLIP_PLATFORM:
            platform = _SLIP_PLATFORM
        size = len(picks)

        if size < 2:
            return {"error": "Need at least 2 picks for a slip", "valid": False}
        if size > 6:
            return {"error": "Sleeper max is 6 picks", "valid": False}

        # Check for duplicate players
        names = [p.get("player_name", "").strip().lower() for p in picks]
        if len(names) != len(set(names)):
            return {"error": "Duplicate players not allowed on Sleeper", "valid": False}

        invalid = [p for p in picks if not _pick_is_sleeper_compatible(p)]
        if invalid:
            return {
                "error": "One or more picks are not Sleeper-compatible.",
                "valid": False,
                "platform": _SLIP_PLATFORM,
                "invalid_picks": [
                    {
                        "player_name": p.get("player_name"),
                        "market": p.get("market"),
                        "line": p.get("line"),
                    }
                    for p in invalid
                ],
            }

        candidate = calculate_slip_ev(picks, size, book=platform, mode="power")

        return {
            "valid": True,
            "slip_size": size,
            "platform": _SLIP_PLATFORM,
            "combined_edge_pct": round(candidate.combined_edge, 2),
            "win_probability_pct": round(candidate.estimated_win_prob * 100, 2),
            "payout_multiplier": candidate.payout_multiplier,
            "expected_value_pct": round(candidate.expected_value * 100, 2),
            "avg_leg_confidence": round(candidate.avg_leg_confidence, 4),
            "players": [
                {
                    "player_name": p.get("player_name"),
                    "market": p.get("market"),
                    "line": p.get("line"),
                    "edge_pct": p.get("edge_pct", 0),
                }
                for p in picks
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
