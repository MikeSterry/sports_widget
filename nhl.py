from datetime import datetime, timezone
import time
import requests
from typing import Any
from collections import defaultdict


class NHL:

    def __init__(self, nhl_api_base: str = "https://api-web.nhle.com", team_code: str = "MIN", division: str = "Central",
                 cache_ttl_seconds: int = 60, standings_cache_ttl_seconds: int = 300, app_tz = None):
        self.nhl_api_base = nhl_api_base
        self.team_code = team_code
        self.division = division
        self.cache_ttl_seconds = cache_ttl_seconds
        self.standings_cache_ttl_seconds = standings_cache_ttl_seconds
        self.app_tz = app_tz
        self.user_agent = 'mnwild-ticker/1.1'

    def now_local(self) -> datetime:
        return datetime.now(tz=self.app_tz)

    _cache = {
        "schedule": {"ts": 0, "data": None},
        "tv_by_date": {},  # date_str -> {"ts":..., "data":...}
        "standings_now": {"ts": 0, "data": None},
    }

    def safe_int(self, v, default=0) -> int:
        try:
            return int(v)
        except Exception:
            return default

    def get_nested(self, obj: Any, path: list[str], default=None):
        cur = obj
        for k in path:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(k)
        return cur if cur is not None else default


    def http_get_json(self, url: str, timeout: int = 10) -> dict:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": self.user_agent})
        r.raise_for_status()
        return r.json()


    def get_team_schedule_now(self) -> dict:
        """
        Team season schedule (relative 'now').

        Endpoint (unofficial but widely used):
            /v1/club-schedule-season/{team}/now
        """
        url = f"{self.nhl_api_base}/v1/club-schedule-season/{self.team_code}/now"
        return self.http_get_json(url)


    def get_tv_schedule_for_date(self, date_str: str) -> dict:
        """
        TV schedule for a date (YYYY-MM-DD).

        Endpoint (unofficial but widely used):
            /v1/network/tv-schedule/{date}
        """
        url = f"{self.nhl_api_base}/v1/network/tv-schedule/{date_str}"
        return self.http_get_json(url)


    def cached_schedule(self) -> dict:
        ts = self._cache["schedule"]["ts"]
        if self._cache["schedule"]["data"] is not None and (time.time() - ts) < self.cache_ttl_seconds:
            return self._cache["schedule"]["data"]

        data = self.get_team_schedule_now()
        self._cache["schedule"] = {"ts": time.time(), "data": data}
        return data


    def cached_tv(self, date_str: str) -> dict:
        entry = self._cache["tv_by_date"].get(date_str)
        if entry and entry["data"] is not None and (time.time() - entry["ts"]) < self.cache_ttl_seconds:
            return entry["data"]

        data = self.get_tv_schedule_for_date(date_str)
        self._cache["tv_by_date"][date_str] = {"ts": time.time(), "data": data}
        return data


    def parse_game_datetime(self, game: dict) -> datetime | None:
        """
        Parse the game start time and convert to APP_TZ.
        We try common keys defensively.
        """
        for key in ("startTimeUTC", "startTime", "gameDate"):
            val = game.get(key)
            if not val:
                continue
            try:
                # startTimeUTC often: 2025-10-09T00:00:00Z
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(self.app_tz)
            except Exception:
                pass
        return None


    def opponent_and_homeaway(self, game: dict) -> tuple[str, str]:
        """
        Return (opponent_name, "vs" or "@")
        """
        home = game.get("homeTeam") or {}
        away = game.get("awayTeam") or {}

        def team_name(t: dict) -> str:
            return (
                (t.get("placeName") or {}).get("default")
                or (t.get("name") or {}).get("default")
                or t.get("abbrev")
                or t.get("teamAbbrev")
                or "TBD"
            )

        home_abbrev = home.get("abbrev") or home.get("teamAbbrev")
        away_abbrev = away.get("abbrev") or away.get("teamAbbrev")

        wild_is_home = home_abbrev == self.team_code
        wild_is_away = away_abbrev == self.team_code

        if wild_is_home:
            return team_name(away), "vs"
        if wild_is_away:
            return team_name(home), "@"

        # Fallback
        return team_name(away) if team_name(away) != "TBD" else team_name(home), "vs"


    def parse_score_line(self, game: dict) -> str:
        """
        Build a compact Wild-centric score string if present.
        """
        home = game.get("homeTeam") or {}
        away = game.get("awayTeam") or {}

        home_abbrev = home.get("abbrev") or home.get("teamAbbrev")
        away_abbrev = away.get("abbrev") or away.get("teamAbbrev")

        home_score = home.get("score")
        away_score = away.get("score")

        # Fallback guesses
        if home_score is None or away_score is None:
            score = game.get("score") or {}
            home_score = home_score if home_score is not None else score.get("home")
            away_score = away_score if away_score is not None else score.get("away")

        if home_score is None or away_score is None:
            return ""

        wild_is_home = home_abbrev == self.team_code
        if wild_is_home:
            if home_score > away_score:
                return f"W {home_score} – {away_score}"
            else:
                return f"L {home_score} – {away_score}"
        else:
            if home_score > away_score:
                return f"L {home_score} – {away_score}"
            else:
                return f"W {home_score} – {away_score}"

        #     return f"{self.team_code} {home_score} – {away_score}"
        # return f"{self.team_code} {away_score} – {home_score}"


    def parse_game_state(self, game: dict) -> str:
        for key in ("gameState", "gameScheduleState", "gameStatus", "state"):
            v = game.get(key)
            if isinstance(v, str) and v:
                return v
        return ""


    def normalize_games(self, schedule_payload: dict) -> list[dict]:
        """
        Flatten possible schedule response shapes into a list of games.
        """
        if isinstance(schedule_payload, dict):
            if isinstance(schedule_payload.get("games"), list):
                return schedule_payload["games"]

            # Sometimes nested by week/month etc.
            for key in ("gameWeek", "weeks", "months", "gamesByMonth", "gamesByDate"):
                node = schedule_payload.get(key)
                if isinstance(node, list):
                    out = []
                    for entry in node:
                        if isinstance(entry, dict) and isinstance(entry.get("games"), list):
                            out.extend(entry["games"])
                    if out:
                        return out

        return []


    # ---------- TV / network extraction ----------

    def extract_networks_from_game_obj(self, game: dict) -> list[str]:
        """
        Some schedule/score endpoints embed broadcast info on the game object.
        We try several shapes defensively.
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

        # Common keys
        add_list(game.get("tvBroadcasts"))
        add_list(game.get("broadcasts"))
        add_list(game.get("tvBroadcast"))
        add_list(game.get("tv"))

        # Sometimes nested
        b = game.get("broadcast") or game.get("broadcastInfo") or {}
        if isinstance(b, dict):
            add_list(b.get("tvBroadcasts"))
            add_list(b.get("broadcasts"))
            add_val(b.get("network"))

        return sorted({n for n in nets if n and n.lower() not in ("null", "none")})


    def extract_networks_for_game(tv_payload: dict, game_id: int | str) -> list[str]:
        """
        Walk /v1/network/tv-schedule/{date} payload and extract network names for a given game id.
        Payload shape varies, so we recursively traverse it.
        """
        wanted = str(game_id)
        networks: set[str] = set()

        def maybe_add(obj):
            if not obj:
                return
            if isinstance(obj, str):
                if obj.strip():
                    networks.add(obj.strip())
                return
            if isinstance(obj, dict):
                for k in ("network", "name", "callSign", "callsign", "displayName", "shortName"):
                    val = obj.get(k)
                    if isinstance(val, str) and val.strip():
                        networks.add(val.strip())

        def walk(node):
            if isinstance(node, dict):
                # Match by common id keys
                for k in ("gameId", "id", "gamePK"):
                    if str(node.get(k, "")) == wanted:
                        # Where broadcasts might live
                        for bk in ("broadcasts", "tvBroadcasts", "networks", "channels"):
                            b = node.get(bk)
                            if isinstance(b, list):
                                for item in b:
                                    maybe_add(item)
                            elif isinstance(b, dict):
                                maybe_add(b)
                        # Also direct keys
                        for dk in ("network", "callSign", "callsign"):
                            maybe_add(node.get(dk))

                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(tv_payload)

        cleaned = sorted({n for n in networks if n and n.lower() not in ("null", "none")})
        return cleaned


    def get_networks_for_game(self, game_obj: dict, local_dt: datetime, game_id) -> list[str]:
        """
        Best-effort network resolution:
            1) Extract from the game object itself
            2) Fall back to TV schedule endpoint by local date
        """
        nets = self.extract_networks_from_game_obj(game_obj)
        if nets:
            return nets

        try:
            date_str = local_dt.strftime("%Y-%m-%d")
            tv_payload = self.cached_tv(date_str)
            return self.extract_networks_for_game(tv_payload, game_id) if game_id else []
        except Exception:
            return []


    def normalize_network_names(self, nets: list[str]) -> list[str]:
        """
        Normalize common network name variations.
        """
        name_map = {
            "ESPN Select": "ESPN+",
            "ESPN": "ESPN",
            "TNT": "TNT",
            "TruTV": "TruTV",
            "FDSN": "FanDuel",
            "Prime": "Prime Video",
        }

        new_list = []
        for net in nets:
            new_list.append(name_map.get(net))

        if len(new_list) == 0:
            new_list = nets

        return new_list


    def use_preferred_network_names(self, nets: list[str]) -> list[str]:
        """
        Normalize common network name variations.
        """
        normalized = self.normalize_fanduel_callsign(nets)
        preferred_network_names = ['TNT', 'TruTV', 'ESPN', 'FDSN', 'Prime', 'ESPN Select']

        # Use preferred network names
        new_list = []
        for net in normalized:
            if net in preferred_network_names:
                new_list.append(net)

        if len(new_list) == 0:
            new_list = normalized

        return new_list


    def normalize_fanduel_callsign(self, callsign_list: list) -> str:
        """
        Normalize FanDuel Sports Network callsign variations.
        """
        found = False
        new_list = []
        for cs in callsign_list:
            if 'FDSN' in cs:
                found = True
            else:
                new_list.append(cs)
        if found:
            new_list.append('FDSN')

        return new_list

    # ---------------------------
    # Standings
    # ---------------------------

    def get_standings_now(self) -> dict:
        # /v1/standings/now (redirects to /v1/standings/YYYY-MM-DD)
        url = f"{self.nhl_api_base}/v1/standings/now"
        return self.http_get_json(url)

    def cached_standings_now(self) -> dict:
        ts = self._cache["standings_now"]["ts"]
        if self._cache["standings_now"]["data"] is not None and (time.time() - ts) < self.standings_cache_ttl_seconds:
            return self._cache["standings_now"]["data"]

        data = self.get_standings_now()
        self._cache["standings_now"] = {"ts": time.time(), "data": data}
        return data

    def normalize_standings(self, payload: dict) -> list[dict]:
        """
        Standings payload usually includes a list under 'standings'.
        We keep this defensive.
        """
        if isinstance(payload, dict) and isinstance(payload.get("standings"), list):
            return payload["standings"]
        return []

    def parse_streak(self, row: dict) -> str:
        """
        Common: streakCode + streakCount (e.g., W + 3 => W3)
        If missing, return empty.
        """
        code = row.get("streakCode") or row.get("streak") or ""
        count = row.get("streakCount")
        if isinstance(code, str) and code and count is not None:
            return f"{code}{count}"
        if isinstance(code, str):
            return code
        return ""

    def row_to_team_name(self, row: dict) -> str:
        # Common: teamName.default, or teamCommonName.default, or teamAbbrev
        return (
                self.get_nested(row, ["teamName", "default"])
                or self.get_nested(row, ["teamCommonName", "default"])
                or row.get("teamAbbrev")
                or "TBD"
        )

    def division_matches(self, row: dict, division: str) -> bool:
        """
        Match either divisionName or divisionAbbrev (case-insensitive).
        """
        div = (division or "").strip().lower()
        if not div:
            return True

        name = (row.get("divisionName") or "").strip().lower()
        abbr = (row.get("divisionAbbrev") or "").strip().lower()

        # allow "central" or "c"
        return div == name or div == abbr

    def build_standings_view(self, division: str) -> dict:
        payload = self.cached_standings_now()
        rows = self.normalize_standings(payload)

        # Filter division
        filtered = [r for r in rows if self.division_matches(r, division)]

        # Sort by points desc, then pointsPct desc (if present), then RW desc
        def sort_key(r: dict):
            pts = self.safe_int(r.get("points"), 0)
            pts_pct = r.get("pointsPct")
            try:
                pts_pct_val = float(pts_pct) if pts_pct is not None else -1.0
            except Exception:
                pts_pct_val = -1.0
            rw = self.safe_int(r.get("regulationWins") or r.get("regWins") or r.get("rw"), 0)
            return (pts, pts_pct_val, rw)

        filtered.sort(key=sort_key, reverse=True)

        table: list[dict] = []
        for r in filtered:
            gp = self.safe_int(r.get("gamesPlayed"), 0)
            w = self.safe_int(r.get("wins"), 0)
            l = self.safe_int(r.get("losses"), 0)
            otl = self.safe_int(r.get("otLosses") or r.get("overtimeLosses"), 0)
            pts = self.safe_int(r.get("points"), 0)

            rw = self.safe_int(r.get("regulationWins") or r.get("regWins") or r.get("rw"), 0)
            row_val = self.safe_int(
                r.get("regulationPlusOvertimeWins")
                or r.get("regulationPlusOtWins")
                or r.get("row"),
                0,
            )

            gf = self.safe_int(r.get("goalFor") or r.get("gf"), 0)
            ga = self.safe_int(r.get("goalAgainst") or r.get("ga"), 0)
            dif = self.safe_int(r.get("goalDifferential") or r.get("goalDiff") or r.get("diff"), gf - ga)

            # Home/Away records vary by payload; try common keys and fall back to derived if partial.
            home_gp = self.safe_int(r.get("homeGamesPlayed"), 0)
            home_w = self.safe_int(r.get("homeWins"), 0)
            home_l = self.safe_int(r.get("homeLosses"), 0)
            home_otl = self.safe_int(r.get("homeOtLosses") or r.get("homeOTLosses"), 0)

            away_gp = self.safe_int(r.get("roadGamesPlayed") or r.get("awayGamesPlayed"), 0)
            away_w = self.safe_int(r.get("roadWins") or r.get("awayWins"), 0)
            away_l = self.safe_int(r.get("roadLosses") or r.get("awayLosses"), 0)
            away_otl = self.safe_int(r.get("roadOtLosses") or r.get("awayOtLosses") or r.get("awayOTLosses"), 0)

            # If home/away gp aren't present but we have W/L/OTL breakdown, keep display simple anyway.
            home_record = f"{home_w}-{home_l}-{home_otl}" if (home_w or home_l or home_otl) else ""
            away_record = f"{away_w}-{away_l}-{away_otl}" if (away_w or away_l or away_otl) else ""

            table.append(
                {
                    "team": self.row_to_team_name(r),
                    "abbr": r.get("teamAbbrev") or "",
                    "gp": gp,
                    "w": w,
                    "l": l,
                    "otl": otl,
                    "pts": pts,
                    "rw": rw,
                    "row": row_val,
                    "strk": self.parse_streak(r),
                    "dif": dif,
                    "gf": gf,
                    "ga": ga,
                    "home": home_record,
                    "away": away_record,
                }
            )

        # Collect divisions present so the UI can offer a dropdown later if you want.
        divisions = sorted({(r.get("divisionName") or r.get("divisionAbbrev") or "").strip() for r in rows if
                            (r.get("divisionName") or r.get("divisionAbbrev"))})

        return {
            "division": division,
            "divisions": divisions,
            "standings": table,
            "standings_generated_at": self.now_local(),
        }


    # ---------- View model ----------

    def build_view_model(self, limit_upcoming: int, limit_recent: int, division: str | None, include_standings: bool) -> dict:
        sched = self.cached_schedule()
        games = self.normalize_games(sched)
        now = self.now_local()

        upcoming = []
        recent = []

        for g in games:
            dt = self.parse_game_datetime(g)
            if dt is None:
                continue

            opponent, ha = self.opponent_and_homeaway(g)
            state = self.parse_game_state(g)
            score = self.parse_score_line(g)
            game_id = g.get("id") or g.get("gameId") or g.get("gamePK") or ""

            # Linux-friendly strftime; docker is Linux, so %-I is OK.
            date_str = dt.strftime("%a %b %d")
            time_str = dt.strftime("%-I:%M %p")

            item = {
                "when": dt,
                "date_str": date_str,
                "time_str": time_str,
                "opponent": opponent,
                "homeaway": ha,
                "state": state,
                "score": score,
                "game_id": game_id,
                "date_key": dt.strftime("%Y-%m-%d"),
                "raw": g,  # keep raw so we can extract embedded broadcast info
            }

            # Put in both buckets then filter/slice below
            upcoming.append(item)
            recent.append(item.copy())

        # Upcoming: now or future
        upcoming = [g for g in upcoming if g["when"] >= now]
        upcoming.sort(key=lambda x: x["when"])
        upcoming = upcoming[:limit_upcoming]

        # Recent: past (most recent first)
        recent = [g for g in recent if g["when"] < now]
        recent.sort(key=lambda x: x["when"], reverse=True)
        recent = recent[:limit_recent]

        # Pre-fetch TV payloads by date for upcoming games (saves duplicate calls)
        dates_to_fetch = sorted({g["date_key"] for g in upcoming})
        tv_by_date: dict[str, dict] = {}
        for d in dates_to_fetch:
            try:
                tv_by_date[d] = self.cached_tv(d)
            except Exception:
                tv_by_date[d] = {}

        # Attach networks to upcoming games
        for item in upcoming:
            raw_game = item.get("raw", {})
            nets = self.extract_networks_from_game_obj(raw_game)
            if not nets:
                # Use the prefetched tv payload
                payload = tv_by_date.get(item["date_key"], {})
                gid = item.get("game_id")
                nets = self.extract_networks_for_game(payload, gid) if gid else []
            preferred_nets = self.use_preferred_network_names(nets)
            normalized_nets = self.normalize_network_names(preferred_nets)

            item["networks"] = normalized_nets
            item.pop("raw", None)

        # Remove raw from recent too (not needed)
        for item in recent:
            item.pop("raw", None)

        vm = {"now": now, "upcoming": upcoming, "recent": recent}

        if include_standings:
            div = (division or self.division).strip()
            standings = self.build_standings_view(div)
            print(f'DEBUG: Standings: {standings}')
            vm.update(standings)

        return vm
