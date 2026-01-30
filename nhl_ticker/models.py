# nhl_ticker/models.py
"""
Domain models for the widget.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence


@dataclass(frozen=True)
class Game:
    """A normalized representation of a schedule game for display/JSON output."""
    when: datetime
    date_str: str
    time_str: str
    opponent: str
    homeaway: str   # "vs" or "@"
    state: str
    score: str
    game_id: str
    date_key: str   # YYYY-MM-DD local
    networks: Sequence[str]
    result: str = ""  # NEW: "W" | "L" | "" (blank when not final/unknown)


@dataclass(frozen=True)
class StandingsRow:
    """A single team row for standings display."""
    team: str
    abbr: str
    gp: int
    w: int
    l: int
    otl: int
    pts: int
    rw: int
    row: int
    strk: str
    dif: int
    gf: int
    ga: int
    home: str
    away: str


@dataclass(frozen=True)
class WidgetViewModel:
    """All data needed to render the widget HTML template."""
    now: datetime
    upcoming: Sequence[Game]
    recent: Sequence[Game]
    division: Optional[str] = None
    standings: Optional[Sequence[StandingsRow]] = None
    standings_generated_at: Optional[datetime] = None
