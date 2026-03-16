"""
DFS Sniper Service — Line Movement Detection & Board Lag Alerts.

Polls the Prop Odds API (or cached data) for real-time line movements at
sharp books, then compares against slower DFS platform lines to detect
exploitable "board lag" windows.
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger("apex.dfs.sniper")


@dataclass
class LineMovement:
    """Represents a detected line movement at a sharp book."""
    player: str
    stat: str  # 'passing_yards', 'rushing_yards', etc.
    book: str  # 'DraftKings', 'FanDuel', etc.
    old_line: float
    new_line: float
    direction: str  # 'up' or 'down'
    timestamp: float
    magnitude: float  # absolute change


@dataclass
class SnipeAlert:
    """A detected board lag opportunity."""
    player: str
    stat: str
    sharp_line: float
    sharp_book: str
    dfs_line: float
    dfs_platform: str  # 'PrizePicks', 'Sleeper', 'Underdog'
    line_diff: float
    stale_seconds: int
    direction: str  # 'over' or 'under' (which side to take on DFS)
    ev_estimate: float  # estimated edge percentage
    timestamp: float
    priority: str  # 'HIGH', 'MEDIUM', 'LOW'


class DFSSniper:
    """
    Board Lag Detector for DFS platforms.
    
    Strategy:
    1. Poll sharp book lines (via Prop Odds API or cached data)
    2. Compare against cached DFS platform lines
    3. When |sharp - dfs| > threshold, emit a snipe alert
    4. Track how long the DFS line has been stale ("stale window")
    5. Prioritize alerts by magnitude and stale duration
    """

    def __init__(self):
        self.sharp_lines: Dict[str, Dict[str, float]] = {}  # key: "player|stat"
        self.dfs_lines: Dict[str, Dict[str, Any]] = {}  # key: "player|stat|platform"
        self.line_movements: List[LineMovement] = []
        self.active_alerts: List[SnipeAlert] = []
        self.is_running = False
        self.config = {
            "min_line_diff": 1.5,  # minimum line difference to trigger alert
            "poll_interval": 30,   # seconds between Prop Odds API polls
            "max_stale_window": 600,  # 10 min — alert becomes stale after this
            "max_movements": 100,  # keep last N movements
        }
        self.stats = {
            "polls": 0,
            "movements_detected": 0,
            "alerts_fired": 0,
            "session_start": None,
        }

    def _key(self, player: str, stat: str) -> str:
        return f"{player.lower().strip()}|{stat.lower().strip()}"

    def update_sharp_line(self, player: str, stat: str, book: str, 
                          new_line: float) -> Optional[LineMovement]:
        """
        Update a sharp book line. If it changed, record the movement
        and check for snipe opportunities.
        """
        key = self._key(player, stat)
        old_entry = self.sharp_lines.get(key, {})
        old_line = old_entry.get("line")
        
        self.sharp_lines[key] = {
            "player": player,
            "stat": stat,
            "book": book,
            "line": new_line,
            "updated_at": time.time(),
        }

        if old_line is not None and abs(new_line - old_line) > 0.25:
            movement = LineMovement(
                player=player,
                stat=stat,
                book=book,
                old_line=old_line,
                new_line=new_line,
                direction="up" if new_line > old_line else "down",
                timestamp=time.time(),
                magnitude=abs(new_line - old_line),
            )
            self.line_movements.append(movement)
            self.stats["movements_detected"] += 1

            # Trim history
            if len(self.line_movements) > self.config["max_movements"]:
                self.line_movements = self.line_movements[-self.config["max_movements"]:]

            # Check for snipe opportunities
            self._check_snipe(key)
            return movement

        return None

    def update_dfs_line(self, player: str, stat: str, platform: str,
                        line: float) -> None:
        """Update a DFS platform line (PrizePicks, Sleeper, etc.)."""
        key = f"{self._key(player, stat)}|{platform.lower()}"
        self.dfs_lines[key] = {
            "player": player,
            "stat": stat,
            "platform": platform,
            "line": line,
            "updated_at": time.time(),
        }

    def _check_snipe(self, sharp_key: str) -> Optional[SnipeAlert]:
        """Check if a sharp line movement creates a snipe opportunity."""
        sharp = self.sharp_lines.get(sharp_key)
        if not sharp:
            return None

        now = time.time()

        # Check against all DFS platforms
        for dfs_key, dfs in self.dfs_lines.items():
            if not dfs_key.startswith(sharp_key):
                continue

            line_diff = abs(sharp["line"] - dfs["line"])

            if line_diff >= self.config["min_line_diff"]:
                stale_seconds = int(now - dfs["updated_at"])

                # Determine which side to take
                if sharp["line"] > dfs["line"]:
                    direction = "over"  # sharp moved up, DFS is slow
                else:
                    direction = "under"  # sharp moved down, DFS is slow

                # Estimate EV based on line diff
                ev_estimate = min(15.0, line_diff * 2.5)

                # Priority based on magnitude + stale duration
                if line_diff >= 3.0 or stale_seconds > 300:
                    priority = "HIGH"
                elif line_diff >= 2.0 or stale_seconds > 120:
                    priority = "MEDIUM"
                else:
                    priority = "LOW"

                alert = SnipeAlert(
                    player=sharp["player"],
                    stat=sharp["stat"],
                    sharp_line=sharp["line"],
                    sharp_book=sharp["book"],
                    dfs_line=dfs["line"],
                    dfs_platform=dfs["platform"],
                    line_diff=round(line_diff, 1),
                    stale_seconds=stale_seconds,
                    direction=direction,
                    ev_estimate=round(ev_estimate, 1),
                    timestamp=now,
                    priority=priority,
                )

                # Remove duplicates for same player/stat/platform
                self.active_alerts = [
                    a for a in self.active_alerts
                    if not (a.player == alert.player and 
                            a.stat == alert.stat and
                            a.dfs_platform == alert.dfs_platform)
                ]
                self.active_alerts.append(alert)
                self.stats["alerts_fired"] += 1

                logger.info(
                    f"🎯 SNIPE: {alert.player} {alert.stat} — "
                    f"{alert.sharp_book} {alert.sharp_line} vs "
                    f"{alert.dfs_platform} {alert.dfs_line} "
                    f"(diff: {alert.line_diff}, EV: {alert.ev_estimate}%)"
                )
                return alert

        return None

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Get all active alerts, sorted by priority."""
        now = time.time()
        # Filter out stale alerts
        self.active_alerts = [
            a for a in self.active_alerts
            if (now - a.timestamp) < self.config["max_stale_window"]
        ]

        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        sorted_alerts = sorted(
            self.active_alerts,
            key=lambda a: (priority_order.get(a.priority, 3), -a.ev_estimate)
        )

        return [
            {
                "player": a.player,
                "stat": a.stat,
                "sharp_line": a.sharp_line,
                "sharp_book": a.sharp_book,
                "dfs_line": a.dfs_line,
                "dfs_platform": a.dfs_platform,
                "line_diff": a.line_diff,
                "stale_seconds": a.stale_seconds,
                "direction": a.direction,
                "ev_estimate": a.ev_estimate,
                "priority": a.priority,
                "timestamp": a.timestamp,
                "time_ago": f"{int(now - a.timestamp)}s ago",
            }
            for a in sorted_alerts
        ]

    def get_recent_movements(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent line movements."""
        return [
            {
                "player": m.player,
                "stat": m.stat,
                "book": m.book,
                "old_line": m.old_line,
                "new_line": m.new_line,
                "direction": m.direction,
                "magnitude": m.magnitude,
                "timestamp": m.timestamp,
            }
            for m in reversed(self.line_movements[-limit:])
        ]

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Return current state for the frontend."""
        return {
            "alerts": self.get_active_alerts(),
            "alert_count": len(self.active_alerts),
            "recent_movements": self.get_recent_movements(),
            "movement_count": len(self.line_movements),
            "tracked_sharp_lines": len(self.sharp_lines),
            "tracked_dfs_lines": len(self.dfs_lines),
            "stats": self.stats,
            "config": self.config,
        }


# ── Singleton ──
_sniper = DFSSniper()


def get_sniper() -> DFSSniper:
    return _sniper
