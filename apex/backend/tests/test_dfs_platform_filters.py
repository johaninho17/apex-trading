from fastapi.testclient import TestClient

from main import app
from routers import dfs as dfs_router


client = TestClient(app)


def _base_row(player: str, market: str, line: float, edge: float, odds: int = -110):
    return {
        "player_name": player,
        "market": market,
        "line": line,
        "side": "over",
        "sharp_odds": odds,
        "edge_pct": edge,
        "books_used": 2,
        "weight_coverage_pct": 60.0,
        "book_odds": [{"book": "fanduel", "odds": odds}],
        "available_on_sleeper_compatible": True,
        "eligible_for_slip": True,
    }


def test_generate_slips_enforces_sleeper_and_dedupes():
    opportunities = [
        _base_row("Player A", "player_points", 20.5, 6.0, -110),
        _base_row("Player A", "player_points", 20.5, 3.0, -115),  # duplicate leg (weaker)
        _base_row("Player B", "player_rebounds", 8.5, 5.0, -108),
        _base_row("Player C", "player_assists", 6.5, 4.5, -112),
        _base_row("Player D", "player_threes", 2.5, 4.0, -106),
        _base_row("Player E", "player_steals", 1.5, 3.2, -104),
    ]
    resp = client.post(
        "/api/v1/dfs/generate-slips",
        json={
            "opportunities": opportunities,
            "slip_sizes": [2, 3],
            "top_n": 4,
            "min_edge": 0.0,
            "book": "prizepicks",  # should be forced to sleeper
            "mode": "flex",
            "sport": "nba",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["book"] == "sleeper"
    assert body["mode"] == "power"
    slips = body["slips"]
    assert len(slips) > 0

    # Each returned slip should have unique players and no duplicate identical slips.
    signatures = set()
    for slip in slips:
        players = [p["player_name"].lower() for p in slip["players"]]
        assert len(players) == len(set(players))
        sig = tuple(sorted((p["player_name"], p["market"], float(p["line"]), p.get("side", "over")) for p in slip["players"]))
        assert sig not in signatures
        signatures.add(sig)


def test_manual_slip_ev_rejects_non_sleeper_rows():
    resp = client.post(
        "/api/v1/dfs/manual-slip-ev",
        json={
            "platform": "sleeper",
            "picks": [
                _base_row("Player A", "player_points", 20.5, 5.0),
                {
                    "player_name": "Player X",
                    "market": "player_unknown_metric",
                    "line": 3.5,
                    "side": "over",
                    "sharp_odds": -110,
                    "edge_pct": 4.0,
                    "available_on_sleeper_compatible": False,
                    "eligible_for_slip": False,
                },
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert "Sleeper-compatible" in body["error"]
    assert len(body.get("invalid_picks", [])) == 1


def test_scan_keeps_core_props_when_consensus_empty(monkeypatch):
    import app.core as dfs_core
    from services import consensus_engine

    class _Sleeper:
        async def get_trending_with_teams(self, sport="nba", limit=25):
            return [{"name": "LeBron James", "count": 12, "team": "LAL"}]

        async def get_all_players(self, sport="nba"):
            return {
                "1": {"first_name": "LeBron", "last_name": "James", "team": "LAL"},
            }

    class _PropOdds:
        async def smart_scan(self, trending_players, sport="nba", max_games=3):
            return [
                {
                    "event_id": "evt1",
                    "player_name": "LeBron James",
                    "market": "player_points",
                    "line": 24.5,
                    "side": "Over",
                    "odds": -110,
                    "book": "fanduel",
                    "home_team": "Los Angeles Lakers",
                    "away_team": "Boston Celtics",
                    "commence_time": "2026-02-19T23:00:00Z",
                },
                {
                    "event_id": "evt1",
                    "player_name": "LeBron James",
                    "market": "player_points",
                    "line": 24.5,
                    "side": "Under",
                    "odds": -108,
                    "book": "draftkings",
                    "home_team": "Los Angeles Lakers",
                    "away_team": "Boston Celtics",
                    "commence_time": "2026-02-19T23:00:00Z",
                },
                {
                    "event_id": "evt1",
                    "player_name": "LeBron James",
                    "market": "player_points_rebounds_assists",
                    "line": 39.5,
                    "side": "Over",
                    "odds": -112,
                    "book": "fanduel",
                    "home_team": "Los Angeles Lakers",
                    "away_team": "Boston Celtics",
                    "commence_time": "2026-02-19T23:00:00Z",
                },
            ]

        async def full_scan(self, sport="nba", max_games=3):
            return await self.smart_scan([], sport=sport, max_games=max_games)

    monkeypatch.setattr(dfs_core, "SleeperClient", lambda: _Sleeper())
    monkeypatch.setattr(dfs_core, "PropOddsClient", lambda: _PropOdds())
    monkeypatch.setattr(
        consensus_engine,
        "build_consensus_candidates",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        dfs_router,
        "get_config",
        lambda: {
            "dfs": {
                "consensus": {
                    "weights": {"bookmaker": 4, "pinnacle": 3, "fanduel": 6, "draftkings": 4},
                    "min_books": 1,
                    "line_window": 1.0,
                    "main_line_only": True,
                    "min_trend_count": 0,
                }
            }
        },
    )

    resp = client.post(
        "/api/v1/dfs/scan",
        json={
            "sport": "nba",
            "scope": "smart",
            "max_games": 3,
            "trending_limit": 25,
            "target_platform": "sleeper",
            "sleeper_markets_only": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_scanned"] >= 1
    assert body.get("calculated_count", 0) == 0
    assert "core props" in body.get("message", "").lower()
    markets = {row.get("market") for row in body.get("opportunities", [])}
    assert "player_points" in markets
    assert "player_points_rebounds_assists" not in markets
    assert all(row.get("is_calculated") is False for row in body.get("opportunities", []))
