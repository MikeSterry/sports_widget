# nhl_ticker/services/standings_service.py
"""
Standings logic.

Responsibilities:
  - fetch standings payload
  - filter by division
  - normalize rows into StandingsRow model
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from ..cache import TTLCache
from ..models import StandingsRow
from ..nhl_client import NHLClient


def safe_int(v, default=0) -> int:
    """Convert a value to int safely; return default on failures."""
    try:
        return int(v)
    except Exception:
        return default


def get_nested(obj: Any, path: list[str], default=None):
    """Safely access nested dict keys by path; return default if missing."""
    cur = obj
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


@dataclass
class StandingsService:
    """Service responsible for returning normalized standings rows."""

    client: NHLClient
    cache: TTLCache
    standings_ttl: int

    def _payload(self) -> Dict[str, Any]:
        """Fetch standings payload using cached loading."""
        return self.cache.get_or_set(
            key="standings:now",
            ttl_seconds=self.standings_ttl,
            loader=self.client.standings_now,
        )

    def _normalize(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract the list of standings rows from the raw payload."""
        rows = payload.get("standings")
        return rows if isinstance(rows, list) else []

    def _division_matches(self, row: Dict[str, Any], division: str) -> bool:
        """Return True if a standings row matches the division (name or abbrev)."""
        div = (division or "").strip().lower()
        if not div:
            return True
        name = (row.get("divisionName") or "").strip().lower()
        abbr = (row.get("divisionAbbrev") or "").strip().lower()
        return div == name or div == abbr

    def _team_name(self, row: Dict[str, Any]) -> str:
        """Extract a team name from common payload fields."""
        return (
            get_nested(row, ["teamName", "default"])
            or get_nested(row, ["teamCommonName", "default"])
            or row.get("teamAbbrev")
            or "TBD"
        )

    @staticmethod
    def _streak(row: Dict[str, Any]) -> str:
        """Build a compact streak string such as 'W3' from code + count when available."""
        code = row.get("streakCode") or row.get("streak") or ""
        count = row.get("streakCount")
        if isinstance(code, str) and code and count is not None:
            return f"{code}{count}"
        return code if isinstance(code, str) else ""

    def get_division(self, division: str) -> Sequence[StandingsRow]:
        """
        Return standings for a given division as a list of StandingsRow.

        Sorting is points desc, then pointsPct desc (if present), then RW desc.
        """
        payload = self._payload()
        rows = self._normalize(payload)
        filtered = [r for r in rows if self._division_matches(r, division)]

        def sort_key(r: Dict[str, Any]):
            pts = safe_int(r.get("points"), 0)
            try:
                pct = float(r.get("pointsPct")) if r.get("pointsPct") is not None else -1.0
            except Exception:
                pct = -1.0
            rw = safe_int(r.get("regulationWins") or r.get("regWins") or r.get("rw"), 0)
            return (pts, pct, rw)

        filtered.sort(key=sort_key, reverse=True)

        out: List[StandingsRow] = []
        for r in filtered:
            gf = safe_int(r.get("goalFor") or r.get("gf"), 0)
            ga = safe_int(r.get("goalAgainst") or r.get("ga"), 0)
            dif = safe_int(r.get("goalDifferential") or r.get("goalDiff") or r.get("diff"), gf - ga)

            home_w = safe_int(r.get("homeWins"), 0)
            home_l = safe_int(r.get("homeLosses"), 0)
            home_otl = safe_int(r.get("homeOtLosses") or r.get("homeOTLosses"), 0)

            away_w = safe_int(r.get("roadWins") or r.get("awayWins"), 0)
            away_l = safe_int(r.get("roadLosses") or r.get("awayLosses"), 0)
            away_otl = safe_int(r.get("roadOtLosses") or r.get("awayOtLosses") or r.get("awayOTLosses"), 0)

            out.append(
                StandingsRow(
                    team=self._team_name(r),
                    abbr=self._team_abbr(r),
                    gp=safe_int(r.get("gamesPlayed"), 0),
                    w=safe_int(r.get("wins"), 0),
                    l=safe_int(r.get("losses"), 0),
                    otl=safe_int(r.get("otLosses") or r.get("overtimeLosses"), 0),
                    pts=safe_int(r.get("points"), 0),
                    rw=safe_int(r.get("regulationWins") or r.get("regWins") or r.get("rw"), 0),
                    row=safe_int(r.get("regulationPlusOvertimeWins") or r.get("row"), 0),
                    strk=self._streak(r),
                    dif=dif,
                    gf=gf,
                    ga=ga,
                    home=f"{home_w}-{home_l}-{home_otl}" if (home_w or home_l or home_otl) else "",
                    away=f"{away_w}-{away_l}-{away_otl}" if (away_w or away_l or away_otl) else "",
                )
            )

        return out

    def _team_abbr(self, row: Dict[str, Any]) -> str:
        """
        Extract a team abbreviation as a string.

        Payload can be either:
          - "COL"
          - {"default": "COL"}
        """
        ab = row.get("teamAbbrev")

        if isinstance(ab, str):
            return ab.strip()

        if isinstance(ab, dict):
            val = ab.get("default")
            if isinstance(val, str):
                return val.strip()

        return ""
