from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Dict, List, Tuple


DEFAULT_WEIGHTS: Dict[str, float] = {
    "bookmaker": 4.0,
    "pinnacle": 3.0,
    "fanduel": 6.0,
    "draftkings": 4.0,
    # DFS apps: weight=0 so their lines don't distort consensus,
    # but they appear in book_odds for availability checks.
    "underdog": 0.0,
    "sleeper": 0.0,
    "prizepicks": 0.0,
}

_ALIASES = {
    "bookmaker": "bookmaker",
    "bookmakercom": "bookmaker",
    "bookmaker.eu": "bookmaker",
    "pinnacle": "pinnacle",
    "fanduel": "fanduel",
    "fanduelsportsbook": "fanduel",
    "draftkings": "draftkings",
    "draftkingssportsbook": "draftkings",
    # DFS book aliases
    "underdog": "underdog",
    "underdogfantasy": "underdog",
    "underdogsports": "underdog",
    "sleeper": "sleeper",
    "sleeperpicks": "sleeper",
    "prizepicks": "prizepicks",
    "prize_picks": "prizepicks",
}


def canonical_book(name: str) -> str:
    compact = "".join(ch for ch in str(name or "").lower() if ch.isalnum() or ch in {".", "_"})
    compact = compact.replace("_", "")
    return _ALIASES.get(compact, compact)


def american_to_implied(odds: int) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    return 100.0 / (odds + 100.0)


def implied_to_american(prob: float) -> int:
    p = max(1e-6, min(1.0 - 1e-6, float(prob)))
    if p >= 0.5:
        return int(round(-100.0 * p / (1.0 - p)))
    return int(round((100.0 / p) - 100.0))


def _normalize_weights(raw: Dict[str, Any] | None) -> Dict[str, float]:
    out = dict(DEFAULT_WEIGHTS)
    if not isinstance(raw, dict):
        return out
    for key in DEFAULT_WEIGHTS.keys():
        v = raw.get(key, out[key])
        try:
            fv = float(v)
        except Exception:
            fv = out[key]
        out[key] = max(0.0, fv)
    return out


def _consensus_from_rows(rows: List[Dict[str, Any]], weights: Dict[str, float]) -> Dict[str, Any] | None:
    # Keep one representative price per book for this exact (player, market, line, side).
    # We store all books for availability visibility, then apply weights only to
    # the configured sharp books for consensus math.
    best_per_book: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        book = canonical_book(row.get("book", ""))
        try:
            odds = int(row.get("odds"))
        except Exception:
            continue
        prev = best_per_book.get(book)
        if prev is None or abs(odds) < abs(int(prev.get("odds", odds))):
            rec = dict(row)
            rec["book"] = book
            rec["odds"] = odds
            best_per_book[book] = rec

    if not best_per_book:
        return None

    weighted_books = {
        book: rec
        for book, rec in best_per_book.items()
        if float(weights.get(book, 0.0)) > 0.0
    }
    if not weighted_books:
        return None

    weighted_prob_sum = 0.0
    total_weight = 0.0
    for book, rec in weighted_books.items():
        odds = int(rec["odds"])
        implied = american_to_implied(odds)
        w = float(weights.get(book, 0.0))
        weighted_prob_sum += implied * w
        total_weight += w

    books = []
    for book, rec in best_per_book.items():
        odds = int(rec["odds"])
        implied = american_to_implied(odds)
        books.append(
            {
                "book": book,
                "odds": odds,
                "weight": float(weights.get(book, 0.0)),
                "implied_prob_pct": round(implied * 100.0, 2),
            }
        )

    if total_weight <= 0:
        return None

    consensus_prob = weighted_prob_sum / total_weight
    max_weight = sum(float(v) for v in weights.values() if v > 0)
    books.sort(key=lambda x: (x["weight"], x["book"]), reverse=True)
    return {
        "consensus_prob": consensus_prob,
        "consensus_odds": implied_to_american(consensus_prob),
        "books": books,
        "books_used": len(weighted_books),
        "total_weight": round(total_weight, 4),
        "weight_coverage_pct": round((total_weight / max_weight) * 100.0, 2) if max_weight > 0 else 0.0,
        "available_books": [b["book"] for b in books],
    }


def build_consensus_candidates(
    props: List[Dict[str, Any]],
    trend_counts: Dict[str, int] | None = None,
    weights_raw: Dict[str, Any] | None = None,
    min_books: int = 2,
    line_window: float = 1.0,
    main_line_only: bool = True,
    min_trend_count: int = 0,
) -> List[Dict[str, Any]]:
    trend_counts = trend_counts or {}
    weights = _normalize_weights(weights_raw)

    grouped: Dict[Tuple[str, str, float, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in props:
        side = str(row.get("side", "")).strip().lower()
        if side not in {"over", "under"}:
            continue
        player = str(row.get("player_name", "")).strip()
        market = str(row.get("market", "")).strip()
        try:
            line = float(row.get("line", 0) or 0)
        except Exception:
            line = 0.0
        grouped[(player, market, line, side)].append(row)

    # Preliminary consensus per exact (player, market, line, side)
    exact: Dict[Tuple[str, str, float, str], Dict[str, Any]] = {}
    lines_by_pms: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
    for key, rows in grouped.items():
        consensus = _consensus_from_rows(rows, weights)
        if not consensus:
            continue
        if consensus["books_used"] < max(1, int(min_books)):
            continue
        player, market, line, side = key
        if trend_counts.get(player.lower(), 0) < max(0, int(min_trend_count)):
            continue
        exact[key] = consensus
        lines_by_pms[(player, market, side)].append(line)

    # Apply line-window gating
    gated: Dict[Tuple[str, str, float, str], Dict[str, Any]] = {}
    for key, consensus in exact.items():
        player, market, line, side = key
        lines = lines_by_pms.get((player, market, side), [line])
        med = float(median(lines)) if lines else line
        if line_window >= 0 and abs(line - med) > float(line_window):
            continue
        enriched = dict(consensus)
        enriched["median_line"] = med
        gated[key] = enriched

    if not gated:
        return []

    # Optionally select one main line per (player, market, side)
    selected_keys: List[Tuple[str, str, float, str]] = []
    if main_line_only:
        best_by_pms: Dict[Tuple[str, str, str], Tuple[Tuple[str, str, float, str], Dict[str, Any]]] = {}
        for key, consensus in gated.items():
            player, market, line, side = key
            pms = (player, market, side)
            cur = best_by_pms.get(pms)
            if cur is None:
                best_by_pms[pms] = (key, consensus)
                continue
            cur_key, cur_cons = cur
            cur_dist = abs(cur_key[2] - float(cur_cons.get("median_line", cur_key[2])))
            next_dist = abs(line - float(consensus.get("median_line", line)))
            better = (
                consensus["total_weight"] > cur_cons["total_weight"]
                or (
                    consensus["total_weight"] == cur_cons["total_weight"]
                    and next_dist < cur_dist
                )
            )
            if better:
                best_by_pms[pms] = (key, consensus)
        selected_keys = [item[0] for item in best_by_pms.values()]
    else:
        selected_keys = list(gated.keys())

    out: List[Dict[str, Any]] = []
    for key in selected_keys:
        player, market, line, side = key
        side_cons = gated.get(key)
        if not side_cons:
            continue
        opp_side = "under" if side == "over" else "over"
        opp_cons = gated.get((player, market, line, opp_side))

        sample = grouped[key][0]
        out.append(
            {
                "player_name": player,
                "market": market,
                "line": line,
                "side": side,
                "consensus_odds": side_cons["consensus_odds"],
                "consensus_prob_pct": round(side_cons["consensus_prob"] * 100.0, 2),
                "opposing_consensus_odds": opp_cons["consensus_odds"] if opp_cons else None,
                "books_used": side_cons["books_used"],
                "weight_coverage_pct": side_cons["weight_coverage_pct"],
                "book_odds": side_cons["books"],
                "event_id": sample.get("event_id", "unknown"),
                "commence_time": sample.get("commence_time"),
                "home_team": sample.get("home_team"),
                "away_team": sample.get("away_team"),
            }
        )

    return out
