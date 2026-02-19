"""Slip Optimizer - Generates ranked parlay combinations for DFS books."""
from dataclasses import dataclass
from itertools import combinations
from typing import Any


@dataclass
class SlipCandidate:
    """A potential parlay slip."""
    players: list[dict[str, Any]]
    combined_edge: float
    estimated_win_prob: float
    expected_value: float
    payout_multiplier: float
    avg_leg_confidence: float
    mode: str = "standard"       # "power" | "flex" | "standard"
    book: str = "sleeper"


# ── Payout tables per book ────────────────────────────────────────────────────
#
# Power: must hit ALL legs to win.
# Flex:  partial payouts for hitting most legs.
#        Flex EV = sum over k=0..n of C(n,k)*p^k*(1-p)^(n-k) * multiplier[k]
#
# Sources (approximate, verify with live promotions):
#   PrizePicks: https://app.prizepicks.com/rules
#   Underdog:   https://underdogfantasy.com/picks
#   Sleeper:    ~1.75x per leg (dynamic, no official table)

BOOK_PAYOUTS: dict[str, dict[str, Any]] = {
    "prizepicks": {
        "power": {
            2: 3.0,
            3: 5.0,
            4: 10.0,
            5: 20.0,        # PrizePicks caps power at 5
        },
        "flex": {
            # flex[n][k] = multiplier for hitting exactly k-of-n legs
            3: {3: 2.25, 2: 1.25, 1: 0.0,  0: 0.0},
            4: {4: 5.0,  3: 1.5,  2: 0.0,  1: 0.0,  0: 0.0},
            5: {5: 10.0, 4: 2.0,  3: 0.4,  2: 0.0,  1: 0.0, 0: 0.0},
            6: {6: 25.0, 5: 2.0,  4: 0.4,  3: 0.0,  2: 0.0, 1: 0.0, 0: 0.0},
        },
    },
    "underdog": {
        "standard": {
            3: 6.0,
            4: 10.0,
            5: 20.0,
            6: 40.0,
        },
        "insured": {
            # insured = full payout if all hit, entry refund if exactly (n-1) hit
            # represented as {n: {k: multiplier}}
            3: {3: 3.0, 2: 1.0, 1: 0.0, 0: 0.0},
            4: {4: 6.0, 3: 1.5, 2: 0.0, 1: 0.0, 0: 0.0},
            5: {5: 10.0, 4: 2.5, 3: 0.0, 2: 0.0, 1: 0.0, 0: 0.0},
            6: {6: 20.0, 5: 2.5, 4: 0.0, 3: 0.0, 2: 0.0, 1: 0.0, 0: 0.0},
        },
    },
    "sleeper": {
        # Sleeper uses a fixed multiplier per leg (~1.75x)
        "power": {n: round(1.75 ** n, 2) for n in range(2, 7)},
    },
}

# Fallback for unknown books
_LEGACY_PAYOUTS = {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0, 6: 40.0}


def get_payout(book: str, mode: str, n_legs: int) -> float | dict[int, float]:
    """
    Returns the payout for a given book/mode/leg count.
    Power/standard modes: returns a single float multiplier (all-or-nothing).
    Flex/insured modes:   returns a dict {hits: multiplier}.
    """
    book_data = BOOK_PAYOUTS.get(book, {})
    mode_data = book_data.get(mode, {})
    if not mode_data:
        return _LEGACY_PAYOUTS.get(n_legs, 2.0)
    return mode_data.get(n_legs, _LEGACY_PAYOUTS.get(n_legs, 2.0))


# ── Probability helpers ───────────────────────────────────────────────────────

def american_to_implied_prob(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def no_vig_prob(main_odds: int, opposing_odds: int | None) -> float:
    p_main = american_to_implied_prob(main_odds)
    if opposing_odds is None:
        return p_main
    p_opp = american_to_implied_prob(opposing_odds)
    total = p_main + p_opp
    return p_main / total if total > 0 else p_main


def leg_confidence_weight(player: dict[str, Any]) -> float:
    has_opposing = player.get("opposing_odds") is not None
    edge_pct = abs(float(player.get("edge_pct", 0.0)))
    edge_factor = min(0.35, (edge_pct / 100.0) * 1.75)
    base = 0.55 if has_opposing else 0.40
    return max(0.2, min(0.95, base + edge_factor))


def confidence_adjusted_prob(probability: float, confidence: float) -> float:
    return 0.5 + ((probability - 0.5) * confidence)


# ── Combinatorial helpers ─────────────────────────────────────────────────────

from math import comb as _comb


def _flex_ev(leg_probs: list[float], payout_table: dict[int, float]) -> float:
    """
    Compute expected value for a flex slip with partial payouts.

    EV = sum_{k=0}^{n} C(n,k) * p^k*(1-p)^(n-k) * m[k] - 1

    This uses the independent-legs assumption.  For simplicity we use the
    average per-leg probability across all legs.
    """
    n = len(leg_probs)
    p_avg = sum(leg_probs) / n if n else 0.5

    ev = -1.0
    for k in range(n + 1):
        m = payout_table.get(k, 0.0)
        if m == 0:
            continue
        prob_k = _comb(n, k) * (p_avg ** k) * ((1 - p_avg) ** (n - k))
        ev += prob_k * m
    return ev


# ── Core EV calculation ───────────────────────────────────────────────────────

def calculate_slip_ev(
    players: list[dict[str, Any]],
    slip_size: int,
    book: str = "sleeper",
    mode: str = "power",
) -> SlipCandidate:
    """Calculate Expected Value of a slip."""
    payout_info = get_payout(book, mode, slip_size)

    confidences: list[float] = []
    leg_probs: list[float] = []
    for p in players:
        sharp_odds = p.get("sharp_odds", -110)
        opposing_odds = p.get("opposing_odds")
        leg_prob = no_vig_prob(sharp_odds, opposing_odds)
        confidence = leg_confidence_weight(p)
        confidences.append(confidence)
        leg_probs.append(confidence_adjusted_prob(leg_prob, confidence))

    win_prob = 1.0
    for lp in leg_probs:
        win_prob *= lp

    is_flex = isinstance(payout_info, dict)
    if is_flex:
        payout_table: dict[int, float] = payout_info  # type: ignore[assignment]
        expected_value = _flex_ev(leg_probs, payout_table)
        # "payout multiplier" for display = all-in-multiplier (max hits)
        display_multiplier = payout_table.get(slip_size, payout_table.get(max(payout_table), 0.0))
        breakeven_prob = 1.0 / display_multiplier if display_multiplier > 0 else 1.0
    else:
        payout_mult: float = float(payout_info)  # type: ignore[arg-type]
        expected_value = (win_prob * payout_mult) - 1.0
        breakeven_prob = 1.0 / payout_mult if payout_mult > 0 else 1.0
        display_multiplier = payout_mult

    combined_edge = (win_prob - breakeven_prob) * 100.0

    return SlipCandidate(
        players=players,
        combined_edge=combined_edge,
        estimated_win_prob=win_prob,
        expected_value=expected_value,
        payout_multiplier=display_multiplier,
        avg_leg_confidence=(sum(confidences) / len(confidences)) if confidences else 0.0,
        mode=mode,
        book=book,
    )


# ── Slip generation ───────────────────────────────────────────────────────────

# ── DFS book market lookup (used by availability filter) ──────────────────────
# Maps book key -> sport -> set of allowed market keys
# Mirrors the BOOK_AVAILABLE_MARKETS in clients.py without a circular import.
_DFS_BOOK_MARKETS: dict[str, dict[str, set[str]]] = {
    "sleeper": {
        "nba": {
            "player_points", "player_rebounds", "player_assists",
            "player_threes", "player_blocks", "player_steals",
            "player_turnovers", "player_points_rebounds_assists",
            "player_points_rebounds", "player_points_assists",
            "player_rebounds_assists", "player_double_double",
            "player_blocks_steals", "player_triple_double",
        },
        "nfl": {
            "player_pass_yds", "player_pass_tds", "player_pass_completions",
            "player_pass_attempts", "player_pass_interceptions",
            "player_rush_yds", "player_rush_attempts", "player_rush_tds",
            "player_receptions", "player_reception_yds", "player_reception_tds",
            "player_rush_reception_yds", "player_rush_reception_tds",
            "player_anytime_td", "player_kicking_points",
        },
        "mlb": {
            "pitcher_strikeouts", "pitcher_outs", "batter_hits",
            "batter_total_bases", "batter_rbis", "batter_runs_scored",
            "batter_walks", "batter_stolen_bases", "batter_home_runs",
        },
        "soccer": {"player_shots", "player_shots_on_target", "player_goal_scorer_anytime"},
    },
}
_DFS_BOOK_MARKETS["prizepicks"] = dict(_DFS_BOOK_MARKETS["sleeper"])
_DFS_BOOK_MARKETS["prizepicks"]["mlb"] = _DFS_BOOK_MARKETS["sleeper"]["mlb"] | {"pitcher_hits_allowed", "pitcher_walks"}
_DFS_BOOK_MARKETS["underdog"] = _DFS_BOOK_MARKETS["prizepicks"]


def _canonical_book_name(book: str) -> str:
    """Normalize sportsbook aliases to a stable key."""
    raw = "".join(ch for ch in str(book or "").lower() if ch.isalnum())
    aliases = {
        "sleeper": "sleeper",
        "prizepicks": "prizepicks",
        "underdog": "underdog",
        "underdogsports": "underdog",
        "draftkings": "draftkings",
        "fanduel": "fanduel",
        "betmgm": "betmgm",
        "mgm": "betmgm",
        "pinnacle": "pinnacle",
        "bookmaker": "bookmaker",
    }
    return aliases.get(raw, raw)


def _pick_identity(p: dict[str, Any]) -> tuple[str, str, str, float]:
    player = str(p.get("player_name", "")).strip().lower()
    market = str(p.get("market", "")).strip().lower()
    side = str(p.get("side", "over")).strip().lower()
    try:
        line = float(p.get("line", 0) or 0)
    except Exception:
        line = 0.0
    return (player, market, side, line)


def _row_quality(p: dict[str, Any]) -> tuple[float, float, float]:
    edge = float(p.get("edge_pct", 0.0) or 0.0)
    books = float(p.get("books_used", 0.0) or 0.0)
    coverage = float(p.get("weight_coverage_pct", 0.0) or 0.0)
    return (edge, books, coverage)


def _overlap_ratio(a: set[tuple[str, str, str, float]], b: set[tuple[str, str, str, float]]) -> float:
    if not a or not b:
        return 0.0
    union = len(a.union(b))
    if union <= 0:
        return 0.0
    return len(a.intersection(b)) / union


def generate_top_slips(
    opportunities: list[dict[str, Any]],
    slip_sizes: list[int] | None = None,
    top_n: int = 5,
    min_edge: float = 0.0,
    book: str = "sleeper",
    mode: str = "power",
    sport: str = "nba",
    prioritize_dfs_lines: bool = False,
) -> list[dict[str, Any]]:
    """
    Generate the best slip(s) for every requested size, ranked by EV.

    Args:
        opportunities: Flat list of scan result props.
        slip_sizes: Which parlay sizes to build (default 3-6).
        top_n: Return at most this many slips across all sizes.
        min_edge: Minimum edge % to include a prop.
        book: Target DFS book key ('sleeper', 'prizepicks', 'underdog').
        mode: Book payout mode ('power', 'flex', etc.).
        sport: Sport key used for market filtering.
        prioritize_dfs_lines: If True, use the DFS book line instead of
            the consensus line when computing EV (not yet implemented;
            reserved for future use).

    Returns:
        One *best* slip per size, serialized as dicts.
    """
    if slip_sizes is None:
        slip_sizes = [3, 4, 5, 6]

    canonical_book = _canonical_book_name(book)
    # No approved direct PP/UD feed yet: enforce Sleeper-compatible slip generation.
    if canonical_book not in {"sleeper"}:
        canonical_book = "sleeper"
        mode = "power"

    # Determine which modes to generate for this book
    book_data = BOOK_PAYOUTS.get(canonical_book, BOOK_PAYOUTS.get(book, {}))
    if mode not in book_data and book_data:
        mode = next(iter(book_data))

    # ── Book-specific availability filter ──────────────────────────────────────
    # Only keep props that are offered on the selected DFS book.
    def _is_available(opp: dict[str, Any]) -> bool:
        target = canonical_book
        if target == "sleeper":
            # Prefer scanner-provided compatibility flag when present.
            if "available_on_sleeper_compatible" in opp:
                return bool(opp.get("available_on_sleeper_compatible"))
            allowed = _DFS_BOOK_MARKETS.get("sleeper", {}).get(sport)
            return str(opp.get("market", "")) in (allowed or set())
        # 1. Check whether the book appears explicitly in book_odds
        for entry in (opp.get("book_odds") or []):
            if isinstance(entry, dict):
                if _canonical_book_name(str(entry.get("book", ""))) == target:
                    return True
        # 2. Fall back to market-type whitelist
        allowed = _DFS_BOOK_MARKETS.get(target, _DFS_BOOK_MARKETS.get(book, {})).get(sport)
        if allowed is not None:
            return str(opp.get("market", "")) in allowed
        # Unknown book — allow everything
        return True

    eligible = [
        p for p in opportunities
        if p.get("edge_pct", 0) >= min_edge and _is_available(p)
    ]
    # Deduplicate exact duplicate legs before combinatorics.
    deduped: dict[tuple[str, str, str, float], dict[str, Any]] = {}
    for p in eligible:
        key = _pick_identity(p)
        cur = deduped.get(key)
        if cur is None or _row_quality(p) > _row_quality(cur):
            deduped[key] = p
    eligible = list(deduped.values())
    eligible.sort(key=_row_quality, reverse=True)
    pool = eligible[:24]  # Keep combinatorics manageable

    all_candidates: list[SlipCandidate] = []

    for size in slip_sizes:
        if len(pool) < size or size > 6:
            continue

        size_candidates: list[SlipCandidate] = []
        combo_seen: set[tuple[tuple[str, str, str, float], ...]] = set()
        for combo in combinations(pool, size):
            player_names = [p.get("player_name", "").strip().lower() for p in combo]
            if len(player_names) != len(set(player_names)):
                continue
            combo_key = tuple(sorted(_pick_identity(p) for p in combo))
            if combo_key in combo_seen:
                continue
            combo_seen.add(combo_key)
            candidate = calculate_slip_ev(list(combo), size, book=canonical_book or book, mode=mode)
            size_candidates.append(candidate)
        if size_candidates:
            size_candidates.sort(key=lambda c: c.expected_value, reverse=True)
            all_candidates.extend(size_candidates[: max(10, top_n * 6)])

    if not all_candidates:
        return []

    all_candidates.sort(key=lambda c: c.expected_value, reverse=True)
    selected: list[SlipCandidate] = []
    selected_sets: list[set[tuple[str, str, str, float]]] = []
    for cand in all_candidates:
        sig = {_pick_identity(p) for p in cand.players}
        if any(_overlap_ratio(sig, prev) > 0.82 for prev in selected_sets):
            continue
        selected.append(cand)
        selected_sets.append(sig)
        if len(selected) >= top_n:
            break
    if not selected:
        selected = all_candidates[:top_n]

    # Serialize
    results = []
    for slip in selected:
        size = len(slip.players)
        results.append({
            "rank": len(results) + 1,
            "slip_size": size,
            "book": canonical_book or book,
            "mode": mode,
            "players": [
                {
                    "player_name": p.get("player_name", "Unknown"),
                    "market": p.get("market", "unknown"),
                    "line": p.get("line", 0),
                    "side": p.get("side", "over"),
                    "edge_pct": p.get("edge_pct", 0),
                }
                for p in slip.players
            ],
            "combined_edge_pct": round(slip.combined_edge, 2),
            "win_probability_pct": round(slip.estimated_win_prob * 100, 2),
            "payout_multiplier": slip.payout_multiplier,
            "expected_value_pct": round(slip.expected_value * 100, 2),
            "avg_leg_confidence": round(slip.avg_leg_confidence, 4),
        })

    return results[:top_n]
