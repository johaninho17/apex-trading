"""
Correlation Engine — DFS Pick'em Optimizer.

Pre-built correlation matrix for NFL/NBA player stacking.
Given a player pick, suggests highest-correlated additional picks
and calculates combined EV for multi-leg entries.
"""

from typing import Dict, Any, List, Tuple, Optional
import logging

logger = logging.getLogger("apex.dfs.correlation")


# ── NFL Correlation Matrix ──
# Format: { (position, stat): { (correlated_position, stat): correlation_coefficient } }
# Based on historical data patterns for DFS stacking

NFL_CORRELATIONS: Dict[Tuple[str, str], Dict[Tuple[str, str], float]] = {
    ("QB", "passing_yards"): {
        ("WR1", "receiving_yards"): 0.72,
        ("WR2", "receiving_yards"): 0.55,
        ("TE", "receiving_yards"): 0.48,
        ("WR1", "receptions"): 0.68,
        ("RB", "receiving_yards"): 0.25,
    },
    ("QB", "passing_tds"): {
        ("WR1", "tds"): 0.65,
        ("WR2", "tds"): 0.40,
        ("TE", "tds"): 0.38,
    },
    ("RB", "rushing_yards"): {
        ("DEF", "points_allowed"): -0.35,  # negative: RB rush ↑ when defense weaker
        ("QB", "passing_yards"): -0.20,     # game script: running = less passing
    },
    ("WR1", "receiving_yards"): {
        ("QB", "passing_yards"): 0.72,
        ("WR2", "receiving_yards"): -0.15,  # slight cannibalization
        ("TE", "receiving_yards"): -0.10,
    },
}

# NBA correlations
NBA_CORRELATIONS: Dict[Tuple[str, str], Dict[Tuple[str, str], float]] = {
    ("PG", "points"): {
        ("SG", "points"): 0.25,
        ("PG", "assists"): 0.60,
    },
    ("PG", "assists"): {
        ("SG", "points"): 0.45,
        ("SF", "points"): 0.40,
        ("C", "points"): 0.35,
    },
    ("C", "rebounds"): {
        ("C", "points"): 0.55,
        ("PF", "rebounds"): -0.20,
    },
}


class CorrelationEngine:
    def __init__(self):
        self.correlations = {
            "NFL": NFL_CORRELATIONS,
            "NBA": NBA_CORRELATIONS,
        }

    def get_correlated_picks(
        self,
        sport: str,
        position: str,
        stat: str,
        top_n: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Given a player's position and stat, return the most correlated picks.
        """
        sport_map = self.correlations.get(sport.upper(), {})
        key = (position.upper(), stat.lower())
        corrs = sport_map.get(key, {})

        results = []
        for (corr_pos, corr_stat), coefficient in corrs.items():
            results.append({
                "position": corr_pos,
                "stat": corr_stat,
                "correlation": round(coefficient, 3),
                "direction": "positive" if coefficient > 0 else "negative",
                "strength": "strong" if abs(coefficient) >= 0.6 else "moderate" if abs(coefficient) >= 0.3 else "weak",
                "recommendation": self._get_recommendation(coefficient, stat, corr_stat),
            })

        results.sort(key=lambda x: abs(x["correlation"]), reverse=True)
        return results[:top_n]

    def _get_recommendation(self, corr: float, primary_stat: str, corr_stat: str) -> str:
        if corr >= 0.6:
            return f"Strong stack: pair with {corr_stat} OVER"
        elif corr >= 0.3:
            return f"Moderate stack: consider {corr_stat} OVER"
        elif corr <= -0.3:
            return f"Negative correlation: consider {corr_stat} UNDER if primary goes OVER"
        else:
            return f"Weak correlation: use independently"

    def calculate_parlay_ev(
        self,
        legs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Calculate combined EV for a multi-leg Pick'em entry.
        
        Each leg: { probability: float, odds: float, stat: str }
        """
        if not legs:
            return {"ev": 0, "combined_prob": 0, "kelly": 0}

        # Independent probability baseline (simple product)
        independent_prob = 1.0
        for leg in legs:
            independent_prob *= leg.get("probability", 0.5)

        # Correlation-aware adjustment (optional):
        # caller may pass pairwise correlation coefficients in each leg under "correlations".
        # We apply a conservative shrink to avoid overconfident estimates.
        corr_values: List[float] = []
        for leg in legs:
            vals = leg.get("correlations", [])
            if isinstance(vals, list):
                corr_values.extend([float(v) for v in vals if isinstance(v, (int, float))])
        if corr_values:
            avg_abs_corr = min(1.0, sum(abs(v) for v in corr_values) / len(corr_values))
            shrink = max(0.5, 1.0 - 0.35 * avg_abs_corr)
            combined_prob = max(0.0, min(1.0, independent_prob * shrink))
        else:
            combined_prob = independent_prob

        # Calculate payout multiplier (Pick'em standard: 3x for 2-leg, 6x for 3-leg, etc.)
        n = len(legs)
        if n == 2:
            payout = 3.0
        elif n == 3:
            payout = 6.0
        elif n == 4:
            payout = 10.0
        elif n == 5:
            payout = 20.0
        elif n == 6:
            payout = 40.0
        else:
            payout = 2.0 ** n  # fallback

        ev = (combined_prob * payout) - 1.0
        ev_percent = ev * 100

        # Kelly criterion
        if ev > 0 and payout > 1:
            kelly = (combined_prob * payout - 1) / (payout - 1)
        else:
            kelly = 0

        return {
            "legs": n,
            "independent_probability": round(independent_prob, 6),
            "combined_probability": round(combined_prob, 6),
            "payout_multiplier": payout,
            "ev_dollars": round(ev, 4),
            "ev_percent": round(ev_percent, 2),
            "kelly_fraction": round(max(0, kelly), 4),
            "recommendation": "BET" if ev > 0 else "SKIP",
        }


# ── Singleton ──
_engine = CorrelationEngine()


def get_correlation_engine() -> CorrelationEngine:
    return _engine
