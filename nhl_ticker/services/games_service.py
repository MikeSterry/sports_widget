# nhl_ticker/services/games_service.py
"""
Game schedule + TV network logic.

Responsibilities:
  - fetch schedule payload
  - normalize games
  - fetch TV schedule payloads for dates as needed
  - extract networks
  - apply preferred ordering + fuzzy mapping to display names
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple
import fnmatch

from dateutil import tz

from ..cache import TTLCache
from ..models import Game
from ..nhl_client import NHLClient


@dataclass
class GamesService:
    """Service responsible for building normalized upcoming/recent game lists."""

    client: NHLClient
    cache: TTLCache
    tz_name: str
    team_code: str
    schedule_ttl: int
    tv_ttl: int

    # Network configuration
    preferred_network_names: Sequence[str] = ()
    network_name_map: dict[str, str] | None = None
    network_name_patterns: Sequence[Tuple[str, str]] = ()

    @property
    def app_tz(self):
        """Return the configured timezone object used for all local conversions."""
        return tz.gettz(self.tz_name)

    def _now_local(self) -> datetime:
        """Return the current time in the app timezone."""
        return datetime.now(tz=self.app_tz)

    def _is_match(self, pattern: str, text: str) -> bool:
        """
        Case-insensitive fuzzy match between a configured pattern and a network string.

        Rules:
          - If pattern contains wildcard meta chars (*, ?, []), use fnmatch.
          - Otherwise, treat as exact OR prefix match (so "FDSN" matches "FDSN1", "FDSNX").
        """
        if not pattern or not text:
            return False

        p = pattern.strip().lower()
        t = text.strip().lower()

        if any(ch in p for ch in ["*", "?", "[", "]"]):
            return fnmatch.fnmatch(t, p)

        return t == p or t.startswith(p)

    def _dedupe_preserve_order(self, items: List[str]) -> List[str]:
        """Remove duplicates while preserving first occurrence order."""
        seen: set[str] = set()
        out: List[str] = []
        for x in items:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def _apply_pattern_name_map(self, net: str) -> str:
        """
        Map a raw network/callsign to a display name.

        Precedence:
          1) pattern list (first match wins)
          2) exact name map
          3) original string
        """
        for pat, mapped in (self.network_name_patterns or ()):
            if self._is_match(pat, net):
                return mapped

        m = self.network_name_map or {}
        return m.get(net, net)

    def normalize_network_names(self, nets: list[str]) -> list[str]:
        """
        Convert raw network strings into display names using pattern/exact mappings.

        Example:
          raw: ["FDSN1", "ESPN Select"] -> ["FanDuel Sports North", "ESPN+"]
        """
        cleaned = [n.strip() for n in nets if isinstance(n, str) and n.strip()]
        mapped = [self._apply_pattern_name_map(n) for n in cleaned]
        return self._dedupe_preserve_order(mapped) if mapped else cleaned

    def use_preferred_network_names(self, nets: list[str]) -> list[str]:
        """
        Filter/order raw network strings using a preferred list that supports fuzzy patterns.

        Example:
          preferred: ["TNT","FDSN*"] and raw: ["FDSN1","ESPN","TNT"]
          -> ["TNT","FDSN1"]
        """
        cleaned = [n.strip() for n in nets if isinstance(n, str) and n.strip()]
        if not cleaned:
            return []

        preferred = list(self.preferred_network_names) if self.preferred_network_names else []
        if not preferred:
            return self._dedupe_preserve_order(cleaned)

        picked: List[str] = []
        for pat in preferred:
            for net in cleaned:
                if self._is_match(pat, net):
                    picked.append(net)

        if not picked:
            picked = cleaned

        return self._dedupe_preserve_order(picked)

    def build_network_display_list(self, raw_networks: list[str]) -> list[str]:
        """
        Build the final display list for networks:
          1) apply preferred ordering/filtering on raw strings
          2) normalize to display names via mapping
        """
        preferred_raw = self.use_preferred_network_names(raw_networks)
        return self.normalize_network_names(preferred_raw)

    def _parse_game_datetime(self, game: Dict[str, Any]) -> datetime | None:
        """
        Parse the game start timestamp from likely fields and convert to app timezone.

        Tries keys in order: startTimeUTC, startTime, gameDate.
        """
        for key in ("startTimeUTC", "startTime", "gameDate"):
            val = game.get(key)
            if not val:
                continue
            try:
                dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(self.app_tz)
            except Exception:
                pass
        return None

    def _normalize_games(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Flatten schedule payload into a list of game objects.

        The NHL schedule endpoint sometimes returns {"games":[...]} or nested week/month structures.
        """
        if isinstance(payload.get("games"), list):
            return payload["games"]

        for key in ("gameWeek", "weeks", "months", "gamesByMonth", "gamesByDate"):
            node = payload.get(key)
            if isinstance(node, list):
                out: List[Dict[str, Any]] = []
                for entry in node:
                    if isinstance(entry, dict) and isinstance(entry.get("games"), list):
                        out.extend(entry["games"])
                if out:
                    return out

        return []

    def _team_name(self, t: Dict[str, Any]) -> str:
        """Return a human-readable team name from common NHL payload shapes."""
        return (
            (t.get("placeName") or {}).get("default")
            or (t.get("name") or {}).get("default")
            or t.get("abbrev")
            or t.get("teamAbbrev")
            or "TBD"
        )

    def _opponent_and_homeaway(self, game: Dict[str, Any]) -> tuple[str, str]:
        """
        Determine opponent name and whether Wild are home ('vs') or away ('@').
        """
        home = game.get("homeTeam") or {}
        away = game.get("awayTeam") or {}

        home_abbrev = home.get("abbrev") or home.get("teamAbbrev")
        away_abbrev = away.get("abbrev") or away.get("teamAbbrev")

        if home_abbrev == self.team_code:
            return self._team_name(away), "vs"
        if away_abbrev == self.team_code:
            return self._team_name(home), "@"
        return self._team_name(away), "vs"

    def _score_line(self, game: Dict[str, Any]) -> str:
        """
        Build a Wild-centric numeric score string when scores are present.

        Returns empty string if scores aren't available.
        Example: "3 – 2"
        """
        home = game.get("homeTeam") or {}
        away = game.get("awayTeam") or {}

        home_abbrev = home.get("abbrev") or home.get("teamAbbrev")
        away_abbrev = away.get("abbrev") or away.get("teamAbbrev")

        hs = home.get("score")
        a_s = away.get("score")

        if hs is None or a_s is None:
            score = game.get("score") or {}
            hs = hs if hs is not None else score.get("home")
            a_s = a_s if a_s is not None else score.get("away")

        if hs is None or a_s is None:
            return ""

        # Wild-centric ordering
        if home_abbrev == self.team_code:
            return f"{hs} – {a_s}"
        if away_abbrev == self.team_code:
            return f"{a_s} – {hs}"

        return f"{hs} – {a_s}"

    def _game_state(self, game: Dict[str, Any]) -> str:
        """Extract a simple game state string from common keys."""
        for key in ("gameState", "gameScheduleState", "gameStatus", "state"):
            v = game.get(key)
            if isinstance(v, str) and v:
                return v
        return ""

    def _extract_networks_from_game_obj(self, game: Dict[str, Any]) -> List[str]:
        """
        Attempt to extract networks/broadcast names directly from the game object.

        Some schedule endpoints embed broadcasts (regional/national) on the game.
        """
        nets: set[str] = set()

        def add_val(v):
            if not v:
                return
            if isinstance(v, str) and v.strip():
                nets.add(v.strip())
                return
            if isinstance(v, dict):
                for k in ("network", "name", "callSign", "callsign", "displayName", "shortName"):
                    sv = v.get(k)
                    if isinstance(sv, str) and sv.strip():
                        nets.add(sv.strip())

        def add_list(lst):
            if isinstance(lst, list):
                for item in lst:
                    add_val(item)

        add_list(game.get("tvBroadcasts"))
        add_list(game.get("broadcasts"))
        add_list(game.get("tvBroadcast"))
        add_list(game.get("tv"))

        b = game.get("broadcast") or game.get("broadcastInfo") or {}
        if isinstance(b, dict):
            add_list(b.get("tvBroadcasts"))
            add_list(b.get("broadcasts"))
            add_val(b.get("network"))

        return sorted({n for n in nets if n and n.lower() not in ("null", "none")})

    def _extract_networks_for_game(self, tv_payload: Dict[str, Any], game_id: str) -> List[str]:
        """
        Walk a tv-schedule payload recursively and extract networks for a given game id.

        This payload shape can vary; recursion is defensive.
        """
        wanted = str(game_id)
        networks: set[str] = set()

        def maybe_add(obj):
            if not obj:
                return
            if isinstance(obj, str) and obj.strip():
                networks.add(obj.strip())
                return
            if isinstance(obj, dict):
                for k in ("network", "name", "callSign", "callsign", "displayName", "shortName"):
                    val = obj.get(k)
                    if isinstance(val, str) and val.strip():
                        networks.add(val.strip())

        def walk(node):
            if isinstance(node, dict):
                for k in ("gameId", "id", "gamePK"):
                    if str(node.get(k, "")) == wanted:
                        for bk in ("broadcasts", "tvBroadcasts", "networks", "channels"):
                            b = node.get(bk)
                            if isinstance(b, list):
                                for item in b:
                                    maybe_add(item)
                            elif isinstance(b, dict):
                                maybe_add(b)
                        for dk in ("network", "callSign", "callsign"):
                            maybe_add(node.get(dk))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(tv_payload)
        return sorted({n for n in networks if n and n.lower() not in ("null", "none")})

    def _tv_payload_for_date(self, date_key: str) -> Dict[str, Any]:
        """Fetch TV schedule payload for a date using cached loading."""
        return self.cache.get_or_set(
            key=f"tv:{date_key}",
            ttl_seconds=self.tv_ttl,
            loader=lambda: self.client.tv_schedule_for_date(date_key),
        )

    def _schedule_payload(self) -> Dict[str, Any]:
        """Fetch team schedule payload (relative to now) using cached loading."""
        return self.cache.get_or_set(
            key=f"schedule:{self.team_code}",
            ttl_seconds=self.schedule_ttl,
            loader=lambda: self.client.team_schedule_now(self.team_code),
        )

    def _get_team_scores(self, game: Dict[str, Any]) -> tuple[int | None, int | None]:
        """
        Extract (wild_score, opp_score) from the game payload.
        Returns (None, None) if scores aren't available.
        """
        home = game.get("homeTeam") or {}
        away = game.get("awayTeam") or {}

        home_abbrev = home.get("abbrev") or home.get("teamAbbrev")
        away_abbrev = away.get("abbrev") or away.get("teamAbbrev")

        hs = home.get("score")
        a_s = away.get("score")

        if hs is None or a_s is None:
            score = game.get("score") or {}
            hs = hs if hs is not None else score.get("home")
            a_s = a_s if a_s is not None else score.get("away")

        if hs is None or a_s is None:
            return None, None

        # Wild-centric
        if home_abbrev == self.team_code:
            return int(hs), int(a_s)
        if away_abbrev == self.team_code:
            return int(a_s), int(hs)

        # Fallback: unknown orientation
        return None, None

    def _result_code(self, game: Dict[str, Any], is_final: bool) -> str:
        """
        Return "W" or "L" only for finals. Otherwise "".
        """
        if not is_final:
            return ""

        wild, opp = self._get_team_scores(game)
        if wild is None or opp is None:
            return ""
        if wild > opp:
            return "W"
        if wild < opp:
            return "L"
        return ""

    def get_games(self, limit_upcoming: int, limit_recent: int) -> tuple[Sequence[Game], Sequence[Game]]:
        """
        Return upcoming and recent games for the configured team.

        Networks are computed only for upcoming games (for display).
        """
        sched = self._schedule_payload()
        games = self._normalize_games(sched)
        now = self._now_local()

        upcoming_raw = []
        recent_raw = []

        for g in games:
            dt = self._parse_game_datetime(g)
            if not dt:
                continue

            opp, ha = self._opponent_and_homeaway(g)
            state = self._normalize_state(g)
            is_live = self._is_live_state(state)
            is_final = self._is_final_state(state)
            score = self._score_line(g)
            game_id = str(g.get("id") or g.get("gameId") or g.get("gamePK") or "")

            item = {
                "raw": g,
                "when": dt,
                "date_str": dt.strftime("%a %b %d"),
                "time_str": dt.strftime("%-I:%M %p"),
                "opponent": opp,
                "homeaway": ha,
                "state": state,
                "is_live": is_live,
                "is_final": is_final,
                "score": score,
                "game_id": game_id,
                "date_key": dt.strftime("%Y-%m-%d"),
            }
            upcoming_raw.append(item)
            recent_raw.append(item.copy())

        upcoming_raw = [x for x in upcoming_raw if x["when"] >= now]
        upcoming_raw.sort(key=lambda x: x["when"])
        upcoming_raw = upcoming_raw[:limit_upcoming]

        recent_raw = [x for x in recent_raw if x["when"] < now]
        recent_raw.sort(key=lambda x: x["when"], reverse=True)
        recent_raw = recent_raw[:limit_recent]

        # Prefetch TV payloads per date for upcoming games to reduce repeated calls.
        tv_by_date = {d: self._tv_payload_for_date(d) for d in sorted({x["date_key"] for x in upcoming_raw})}

        upcoming: List[Game] = []
        for x in upcoming_raw:
            raw = x["raw"]
            raw_nets = self._extract_networks_from_game_obj(raw)

            # If not embedded, fall back to tv-schedule payload for that date.
            if not raw_nets and x["game_id"]:
                raw_nets = self._extract_networks_for_game(tv_by_date.get(x["date_key"], {}), x["game_id"])

            display_nets = self.build_network_display_list(raw_nets)

            upcoming.append(
                Game(
                    when=x["when"],
                    date_str=x["date_str"],
                    time_str=x["time_str"],
                    opponent=x["opponent"],
                    homeaway=x["homeaway"],
                    state=x["state"],
                    score=x["score"],
                    game_id=x["game_id"],
                    date_key=x["date_key"],
                    networks=display_nets,
                    live_label=self._extract_live_label(raw) if is_live else "",
                    result=self._result_code(raw, is_final=is_final),

                )
            )

        recent: List[Game] = [
            Game(
                when=x["when"],
                date_str=x["date_str"],
                time_str=x["time_str"],
                opponent=x["opponent"],
                homeaway=x["homeaway"],
                state=x["state"],
                score=x["score"],
                game_id=x["game_id"],
                date_key=x["date_key"],
                networks=(),
                is_live=bool(x["is_live"]),
                is_final=bool(x["is_final"]),
                live_label=self._extract_live_label(x["raw"]) if x["is_live"] else "",
                result=self._result_code(x["raw"], is_final=bool(x["is_final"])),
            )
            for x in recent_raw
        ]

        return upcoming, recent

    def _normalize_state(self, game: Dict[str, Any]) -> str:
        """
        Normalize the game state into a consistent uppercase token.

        We try multiple keys because NHL payloads vary.
        """
        for key in ("gameState", "gameScheduleState", "gameStatus", "state"):
            v = game.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().upper()
        return ""

    def _is_live_state(self, state: str) -> bool:
        """
        Return True if state indicates the game is currently in progress.

        This is best-effort; NHL can use variants across endpoints.
        """
        s = (state or "").upper()
        return s in ("LIVE", "IN_PROGRESS", "INPROGRESS", "ACTIVE", "CRIT", "CRITICAL", "ONGOING")

    def _is_final_state(self, state: str) -> bool:
        """Return True if state indicates the game has ended."""
        s = (state or "").upper()
        return s in ("FINAL", "OFF", "COMPLETED", "DONE", "FINISHED")

    def _extract_live_label(self, game: Dict[str, Any]) -> str:
        """
        Build a compact label for live games like:
          - "P2 12:34"
          - "INT"
          - "SO"
          - "OT 3:21"

        This is best-effort based on common NHL fields.
        """
        # Common patterns:
        # - game["clock"]["timeRemaining"] or ["timeRemaining"]
        # - game["clock"]["running"]
        # - game["periodDescriptor"]["number"] or game["period"]
        # - game["periodDescriptor"]["periodType"] (REG/OT/SO)
        clock = game.get("clock") or {}
        time_left = None
        if isinstance(clock, dict):
            time_left = clock.get("timeRemaining") or clock.get("timeRemainingInPeriod")

        # Some payloads use direct keys
        if not time_left:
            time_left = game.get("timeRemaining") or game.get("timeRemainingInPeriod")

        pd = game.get("periodDescriptor") or {}
        period_num = None
        period_type = None
        if isinstance(pd, dict):
            period_num = pd.get("number") or pd.get("periodNumber")
            period_type = pd.get("periodType") or pd.get("type")

        if period_num is None:
            period_num = game.get("period") or game.get("currentPeriod")

        if not period_type:
            period_type = game.get("periodType")

        # Intermission flag sometimes exists
        in_int = game.get("inIntermission")
        if in_int is None and isinstance(clock, dict):
            in_int = clock.get("inIntermission")

        if in_int is True:
            return "INT"

        # Determine period prefix
        pt = (str(period_type).upper() if period_type is not None else "")
        if pt in ("SO", "SHOOTOUT"):
            return "SO"
        if pt in ("OT", "OVERTIME"):
            # Some APIs still give a "number" for OT; show OT either way
            if isinstance(time_left, str) and time_left.strip():
                return f"OT {time_left.strip()}"
            return "OT"

        # Default: regulation period number
        if period_num is not None and str(period_num).isdigit():
            if isinstance(time_left, str) and time_left.strip():
                return f"P{period_num} {time_left.strip()}"
            return f"P{period_num}"

        # If we have time left but no period info
        if isinstance(time_left, str) and time_left.strip():
            return time_left.strip()

        return ""

