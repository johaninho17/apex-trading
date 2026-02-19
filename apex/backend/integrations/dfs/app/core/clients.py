"""HTTP clients for external APIs."""
import os
import httpx
from typing import Any

from .config import get_settings


# In-memory cache for player metadata (shared)
_player_metadata_cache: dict[str, dict[str, Any]] = {}


class PropOddsAuthError(RuntimeError):
    """Raised when Odds API credentials are rejected."""


class PropOddsPlanError(RuntimeError):
    """Raised when Odds API plan/quota blocks request."""


class SleeperClient:
    """Client for Sleeper API."""
    
    def __init__(self):
        self.base_url = get_settings().sleeper_base_url
        
    async def get_trending_players(
        self, 
        sport: str = "nfl",
        trend_type: str = "add",
        lookback_hours: int = 24,
        limit: int = 25
    ) -> list[dict[str, Any]]:
        """
        Fetch trending players from Sleeper.
        
        Returns:
            List of {player_id, count} dicts
        """
        url = f"{self.base_url}/players/{sport}/trending/{trend_type}"
        params = {"lookback_hours": lookback_hours, "limit": limit}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    
    async def get_all_players(self, sport: str = "nfl") -> dict[str, Any]:
        """Fetch all player metadata (cached globally)."""
        global _player_metadata_cache
        
        if sport in _player_metadata_cache:
            return _player_metadata_cache[sport]
        
        url = f"{self.base_url}/players/{sport}"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            _player_metadata_cache[sport] = data
            return data
    
    async def get_trending_with_teams(
        self,
        sport: str = "nfl",
        limit: int = 25
    ) -> list[dict[str, Any]]:
        """
        Get trending players enriched with team info.
        
        Returns:
            List of {player_id, count, name, team} dicts
        """
        trending = await self.get_trending_players(sport=sport, limit=limit)
        all_players = await self.get_all_players(sport=sport)
        
        enriched = []
        for trend in trending:
            player_id = trend.get("player_id", "")
            player_data = all_players.get(player_id, {})
            
            enriched.append({
                "player_id": player_id,
                "count": trend.get("count", 0),
                "name": f"{player_data.get('first_name', '')} {player_data.get('last_name', '')}".strip(),
                "team": player_data.get("team", ""),  # e.g., "KC", "BAL"
                "position": player_data.get("position", ""),
            })
        
        return enriched


# Sport key mapping: Sleeper sport -> The Odds API sport key
SPORT_KEY_MAP = {
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "soccer": "soccer_usa_mls",
}

# Player prop markets for each sport
# Expanded to cover all markets available on DFS sites (Sleeper, PrizePicks, Underdog, etc.)
PROP_MARKETS = {
    "nfl": [
        # Passing
        "player_pass_yds",
        "player_pass_tds",
        "player_pass_completions",
        "player_pass_attempts",
        "player_pass_interceptions",
        # Rushing
        "player_rush_yds",
        "player_rush_attempts",
        "player_rush_tds",
        # Receiving
        "player_receptions",
        "player_reception_yds",
        "player_reception_tds",
        # Combo markets (available on DFS apps)
        "player_rush_reception_yds",
        "player_rush_reception_tds",
        # Misc
        "player_anytime_td",
        "player_kicking_points",
    ],
    "nba": [
        # Core stats
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
        "player_blocks",
        "player_steals",
        "player_turnovers",
        # Combo markets (most popular on DFS apps)
        "player_points_rebounds_assists",
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_blocks_steals",
        "player_double_double",
        "player_triple_double",
    ],
    "mlb": [
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
    ],
    "soccer": [
        "player_shots",
        "player_shots_on_target",
        "player_goal_scorer_anytime",
    ],
}

# ── Per-Book Available Markets ──
# Only these prop types are offered on each DFS app.
# Used to filter out props unavailable on the selected book.
SLEEPER_AVAILABLE_MARKETS = {
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
    "soccer": {
        "player_shots", "player_shots_on_target",
        "player_goal_scorer_anytime",
    },
}

# PrizePicks available markets (broader than Sleeper, includes most combos)
PRIZEPICKS_AVAILABLE_MARKETS = {
    "nba": SLEEPER_AVAILABLE_MARKETS["nba"],
    "nfl": SLEEPER_AVAILABLE_MARKETS["nfl"],
    "mlb": SLEEPER_AVAILABLE_MARKETS["mlb"] | {"pitcher_hits_allowed", "pitcher_walks"},
    "soccer": SLEEPER_AVAILABLE_MARKETS["soccer"],
}

# Underdog available markets (same breadth as PrizePicks)
UNDERDOG_AVAILABLE_MARKETS = PRIZEPICKS_AVAILABLE_MARKETS

# Unified lookup by book key
BOOK_AVAILABLE_MARKETS: dict[str, dict[str, set[str]]] = {
    "sleeper": SLEEPER_AVAILABLE_MARKETS,
    "prizepicks": PRIZEPICKS_AVAILABLE_MARKETS,
    "underdog": UNDERDOG_AVAILABLE_MARKETS,
}


# Common suffixes to strip for fuzzy matching
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalize_player_name(name: str) -> str:
    """Normalize a player name for comparison: lowercase, alphanumeric only, no suffixes."""
    # Strip punctuation and lowercase
    compact = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in str(name or "").lower())
    parts = compact.split()
    # Remove trailing suffixes (jr, sr, iii, etc.)
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def _name_matches_fuzzy(prop_name: str, norm_names: set[str]) -> bool:
    """Fuzzy player name match: exact normalized, then initial-expanded variants."""
    norm = _normalize_player_name(prop_name)
    if norm in norm_names:
        return True
    # Try expanding initials: "cj mccollum" -> "c j mccollum" and vice versa
    parts = norm.split()
    if not parts:
        return False
    first = parts[0]
    rest = parts[1:]
    # Expand single-char runs at start of first name (e.g. "cj" -> ["c", "j"])
    if len(first) <= 3 and first.isalpha():
        expanded = " ".join(list(first) + rest)
        if expanded in norm_names:
            return True
    # Collapse initials: "c j" -> "cj"
    if len(first) == 1 and rest and len(rest[0]) == 1:
        collapsed = first + rest[0]
        if " ".join([collapsed] + rest[1:]) in norm_names:
            return True
    return False


def filter_sleeper_markets(
    props: list[dict[str, Any]],
    sport: str,
    allowed_player_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter props to only include markets available on Sleeper.

    If `allowed_player_names` is provided, enforce player-name membership using
    fuzzy matching (tolerates suffixes like Jr./III and initial formatting).
    """
    allowed = SLEEPER_AVAILABLE_MARKETS.get(sport, set())
    norm_names: set[str] | None = None
    if allowed_player_names:
        norm_names = {_normalize_player_name(n) for n in allowed_player_names if str(n).strip()}

    if not allowed and norm_names is None:
        return props

    filtered: list[dict[str, Any]] = []
    for p in props:
        market_ok = (not allowed) or (p.get("market", "") in allowed)
        if not market_ok:
            continue
        if norm_names is not None:
            if not _name_matches_fuzzy(str(p.get("player_name", "")), norm_names):
                continue
        filtered.append(p)
    return filtered

# Mapping: Sleeper team abbreviation -> The Odds API team name
# This is approximate and may need tuning
NFL_TEAM_MAP = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

NBA_TEAM_MAP = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}

MLB_TEAM_MAP = {
    "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves", "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox", "CHC": "Chicago Cubs", "CHW": "Chicago White Sox",
    "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians", "COL": "Colorado Rockies",
    "DET": "Detroit Tigers", "HOU": "Houston Astros", "KC": "Kansas City Royals",
    "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers", "MIN": "Minnesota Twins", "NYM": "New York Mets",
    "NYY": "New York Yankees", "OAK": "Oakland Athletics", "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates", "SD": "San Diego Padres", "SF": "San Francisco Giants",
    "SEA": "Seattle Mariners", "STL": "St. Louis Cardinals", "TB": "Tampa Bay Rays",
    "TEX": "Texas Rangers", "TOR": "Toronto Blue Jays", "WAS": "Washington Nationals",
}

SOCCER_TEAM_MAP: dict[str, str] = {
    # MLS teams — Sleeper abbrevs are approximate
}


class PropOddsClient:
    """Client for The Odds API (player props)."""
    
    def __init__(self):
        settings_key = get_settings().prop_odds_api_key
        self.api_key = (
            os.getenv("PROP_ODDS_API_KEY")
            or os.getenv("THE_ODDS_API_KEY")
            or settings_key
            or ""
        ).strip()
        self.base_url = "https://api.the-odds-api.com"

    async def _get_json(self, url: str, params: dict[str, Any], timeout: float = 15.0) -> Any:
        if not self.api_key:
            raise PropOddsAuthError(
                "Odds API key is missing. Set PROP_ODDS_API_KEY (or THE_ODDS_API_KEY) in backend .env."
            )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params)

        if response.status_code == 401:
            raise PropOddsAuthError(
                "Odds provider rejected API credentials (401). "
                "Check PROP_ODDS_API_KEY / THE_ODDS_API_KEY in backend .env."
            )
        if response.status_code in {402, 429}:
            raise PropOddsPlanError(
                f"Odds provider quota/plan limit hit ({response.status_code}). "
                "Upgrade plan or reduce scan usage."
            )
        response.raise_for_status()
        return response.json()
        
    async def get_upcoming_events(self, sport: str = "nfl") -> list[dict[str, Any]]:
        """
        Fetch upcoming events for a sport.
        
        Returns list of events with id, sport_key, commence_time, home_team, away_team.
        """
        sport_key = SPORT_KEY_MAP.get(sport, f"americanfootball_{sport}")
        url = f"{self.base_url}/v4/sports/{sport_key}/events"
        params = {
            "apiKey": self.api_key,
        }
        return await self._get_json(url, params=params, timeout=15.0)
    
    async def get_event_player_props(
        self,
        event_id: str,
        sport: str = "nfl",
        markets: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Fetch player prop odds for a specific event.
        """
        sport_key = SPORT_KEY_MAP.get(sport, f"americanfootball_{sport}")
        
        if markets is None:
            markets = PROP_MARKETS.get(sport, ["player_points"])
        
        url = f"{self.base_url}/v4/sports/{sport_key}/events/{event_id}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "us,us2",   # us2 covers DFS books (PrizePicks/Underdog/Sleeper)
            "markets": ",".join(markets),
            "oddsFormat": "american",
        }
        return await self._get_json(url, params=params, timeout=15.0)
    
    def _parse_props_from_event(
        self,
        event: dict[str, Any],
        event_odds: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Parse bookmaker data into flat prop list."""
        props = []
        for bookmaker in event_odds.get("bookmakers", []):
            book_name = bookmaker.get("key", "unknown")
            
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "unknown")
                
                for outcome in market.get("outcomes", []):
                    prop = {
                        "event_id": event["id"],
                        "commence_time": event.get("commence_time"),
                        "home_team": event.get("home_team", ""),
                        "away_team": event.get("away_team", ""),
                        "player_name": outcome.get("description", "Unknown"),
                        "market": market_key,
                        "line": outcome.get("point", 0),
                        "side": outcome.get("name", "Over"),
                        "odds": outcome.get("price", -110),
                        "book": book_name,
                    }
                    props.append(prop)
        return props
    
    async def smart_scan(
        self,
        trending_players: list[dict[str, Any]],
        sport: str = "nfl",
        max_games: int = 3
    ) -> list[dict[str, Any]]:
        """
        Smart Scan: Only fetch props for games that have trending players.
        
        Args:
            trending_players: List from SleeperClient.get_trending_with_teams()
            sport: Sport type
            max_games: Maximum games to query (to save API credits)
            
        Returns:
            Flat list of player props from high-value games
        """
        team_map = {"nfl": NFL_TEAM_MAP, "nba": NBA_TEAM_MAP, "mlb": MLB_TEAM_MAP, "soccer": SOCCER_TEAM_MAP}.get(sport, {})
        
        # Step 1: Extract unique teams from trending players
        trending_teams: set[str] = set()
        for player in trending_players:
            team_abbrev = player.get("team", "")
            if team_abbrev and team_abbrev in team_map:
                trending_teams.add(team_map[team_abbrev])
        
        if not trending_teams:
            print(f"DEBUG: No valid teams found. Trending raw: {len(trending_players)} items.")
            print(f"DEBUG: Sample trending player: {trending_players[0] if trending_players else 'None'}")
            return []
        
        print(f"DEBUG: Found {len(trending_teams)} unique trending teams: {trending_teams}")

        # Step 2: Get upcoming events
        try:
            events = await self.get_upcoming_events(sport)
            print(f"DEBUG: Fetched {len(events)} upcoming events for {sport}")
        except (PropOddsAuthError, PropOddsPlanError):
            raise
        except Exception as e:
            print(f"Error fetching events: {e}")
            return []
        
        # Step 3: Score each event by how many trending teams are playing
        scored_events: list[tuple[int, dict]] = []
        for event in events:
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            score = 0
            if home in trending_teams:
                score += 1
            if away in trending_teams:
                score += 1
            
            print(f"DEBUG: Analyzing Game {home} vs {away} - Score: {score}")

            if score > 0:
                scored_events.append((score, event))
        
        # Step 4: Sort by score (highest first) and take top N
        scored_events.sort(key=lambda x: x[0], reverse=True)
        selected_events = [ev for _, ev in scored_events[:max_games]]
        
        if not selected_events:
            print("No games found matching trending player teams")
            return []
        
        print(f"Smart Scan: Querying {len(selected_events)} games (saved {len(events) - len(selected_events)} API calls)")
        
        # Step 5: Fetch props only for selected events
        all_props: list[dict[str, Any]] = []
        for event in selected_events:
            try:
                event_odds = await self.get_event_player_props(
                    event_id=event["id"],
                    sport=sport
                )
                props = self._parse_props_from_event(event, event_odds)
                all_props.extend(props)
            except (PropOddsAuthError, PropOddsPlanError):
                raise
            except Exception as e:
                print(f"Error fetching props for event {event.get('id')}: {e}")
                continue
        
        return all_props

    async def full_scan(
        self,
        sport: str = "nba",
        max_games: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Full Scan: Fetch ALL props for ALL upcoming games.
        This powers the 'Daily Grind' bulk EV dashboard.

        Args:
            sport: Sport type
            max_games: Maximum games to query

        Returns:
            Flat list of all player props across all games
        """
        # Step 1: Get all upcoming events
        try:
            events = await self.get_upcoming_events(sport)
            print(f"FULL SCAN: Fetched {len(events)} upcoming events for {sport}")
        except (PropOddsAuthError, PropOddsPlanError):
            raise
        except Exception as e:
            print(f"Error fetching events: {e}")
            return []

        selected = events[:max_games]
        print(f"FULL SCAN: Querying {len(selected)} games for all props")

        # Step 2: Fetch props for every selected event
        all_props: list[dict[str, Any]] = []
        for event in selected:
            try:
                event_odds = await self.get_event_player_props(
                    event_id=event["id"],
                    sport=sport,
                )
                props = self._parse_props_from_event(event, event_odds)
                all_props.extend(props)
            except (PropOddsAuthError, PropOddsPlanError):
                raise
            except Exception as e:
                print(f"Error fetching props for event {event.get('id')}: {e}")
                continue

        print(f"FULL SCAN: Total props fetched: {len(all_props)}")
        return all_props
