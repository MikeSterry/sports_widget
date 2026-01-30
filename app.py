# app.py
"""
Flask entrypoint for the sports widget service.

Routes (Hockey):
  HTML:
    - /widget/hockey
    - /widget/hockey/upcoming
    - /widget/hockey/recent
    - /widget/hockey/standings

  JSON:
    - /api/hockey
    - /api/hockey/upcoming
    - /api/hockey/recent
    - /api/hockey/standings

Query parameters (common):
  - theme=dark|light|transparent
  - team=XXX (3-letter NHL team code, e.g. MIN, DAL, NYR)
  - upcoming=N (for upcoming views)
  - recent=N (for recent views)
  - standings=1|0 (for full widget)
  - division=Central (for standings)

Notes:
  - Team selection is per-request and does NOT mutate shared service state.
  - Team code validation uses the NHL standings payload (cached).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from flask import Flask, jsonify, render_template, request, redirect

from nhl_ticker.cache import TTLCache
from nhl_ticker.config import AppConfig
from nhl_ticker.nhl_client import NHLClient
from nhl_ticker.handlers.widget_handler import WidgetHandler
from nhl_ticker.services.games_service import GamesService
from nhl_ticker.services.standings_service import StandingsService


TEAM_RE = re.compile(r"^[A-Z]{3}$")

NHL_TEAM_NAMES = {
    "ANA": "Anaheim Ducks",
    "ARI": "Arizona Coyotes",
    "BOS": "Boston Bruins",
    "BUF": "Buffalo Sabres",
    "CGY": "Calgary Flames",
    "CAR": "Carolina Hurricanes",
    "CHI": "Chicago Blackhawks",
    "COL": "Colorado Avalanche",
    "CBJ": "Columbus Blue Jackets",
    "DAL": "Dallas Stars",
    "DET": "Detroit Red Wings",
    "EDM": "Edmonton Oilers",
    "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings",
    "MIN": "Minnesota Wild",
    "MTL": "Montreal Canadiens",
    "NSH": "Nashville Predators",
    "NJD": "New Jersey Devils",
    "NYI": "New York Islanders",
    "NYR": "New York Rangers",
    "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers",
    "PIT": "Pittsburgh Penguins",
    "SJS": "San Jose Sharks",
    "SEA": "Seattle Kraken",
    "STL": "St. Louis Blues",
    "TBL": "Tampa Bay Lightning",
    "TOR": "Toronto Maple Leafs",
    "VAN": "Vancouver Canucks",
    "VGK": "Vegas Golden Knights",
    "WSH": "Washington Capitals",
    "WPG": "Winnipeg Jets",
}

# Team branding defaults for the banner (bg, text, accent, and font)
TEAM_BRANDING = {
    # Minnesota Wild
    "MIN": {
        "bg": "linear-gradient(90deg, #154734 0%, #0b2f25 100%)",  # Wild green gradient
        "fg": "rgba(255,255,255,0.95)",
        "accent": "#A6192E",  # Wild red
        "font_family": '"Oswald", system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
        "font_href": "https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&display=swap",
    },
}

DEFAULT_BRANDING = {
    "bg": "linear-gradient(90deg, #111827 0%, #0b1220 100%)",
    "fg": "rgba(255,255,255,0.92)",
    "accent": "rgba(255,255,255,0.25)",
    "font_family": 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    "font_href": "",
}

def create_app() -> Flask:
    """
    App factory.

    Builds shared dependencies (client + cache + standings service) once per process,
    and constructs per-request game services so changing team via query param is safe.
    """
    cfg = AppConfig()
    cache = TTLCache()
    client = NHLClient(cfg.nhl_api_base)

    # League-wide standings service can be shared safely (team selection does not affect it).
    standings = StandingsService(
        client=client,
        cache=cache,
        standings_ttl=cfg.standings_cache_ttl_seconds,
    )

    app = Flask(__name__)

    # -------------------------
    # Team validation & metadata
    # -------------------------

    def _extract_team_info_from_standings(payload: Dict[str, Any]) -> Tuple[Set[str], Dict[str, str]]:
        """
        Extract:
          - set of valid team abbreviations (e.g. {"MIN","DAL",...})
          - mapping of team abbreviations -> display name (best-effort)

        We keep this best-effort because the exact name fields can vary slightly.
        """
        valid: Set[str] = set()
        names: Dict[str, str] = {}

        rows = payload.get("standings")
        if not isinstance(rows, list):
            return valid, names

        for r in rows:
            if not isinstance(r, dict):
                continue

            abbr = r.get("teamAbbrev")
            if isinstance(abbr, str) and TEAM_RE.match(abbr):
                valid.add(abbr)

                # Try a few known shapes for team display name
                name = None
                tn = r.get("teamName")
                tcn = r.get("teamCommonName")

                if isinstance(tn, dict):
                    name = tn.get("default")
                if not name and isinstance(tcn, dict):
                    name = tcn.get("default")

                # Fallbacks
                if not name:
                    name = abbr

                if isinstance(name, str) and name.strip():
                    names[abbr] = name.strip()

        return valid, names

    def get_team_registry() -> Tuple[Set[str], Dict[str, str]]:
        """
        Return a cached registry of valid NHL team codes and display names.

        IMPORTANT:
          - If the NHL endpoint fails, we return an empty registry but DO NOT
            cache it for a long time (short TTL), so it can recover quickly.
        """

        def loader():
            payload = client.standings_now()
            return _extract_team_info_from_standings(payload)

        # Try normal cached load
        codes, names = cache.get_or_set(
            "nhl:team_registry",
            ttl_seconds=60 * 60 * 24,  # 24h
            loader=loader,
        )

        # If we somehow got an empty registry, refresh more frequently.
        # (Prevents "cached broken state" for a whole day.)
        if not codes:
            codes, names = cache.get_or_set(
                "nhl:team_registry:retry",
                ttl_seconds=60,  # retry every 60s until it works
                loader=loader,
            )

        return codes, names

    def get_team_code() -> str:
        """
        Read ?team=XXX and validate.

        If the registry is available, validate against it.
        If registry is unavailable/empty, allow any 3-letter code.
        """
        raw = (request.args.get("team") or cfg.team_code).strip().upper()
        if not TEAM_RE.match(raw):
            return cfg.team_code

        valid_codes, _ = get_team_registry()

        # Registry unavailable => accept any 3-letter code so query params still work
        if not valid_codes:
            return raw

        return raw if raw in valid_codes else cfg.team_code

    def get_team_display_name(team_code: str) -> str:
        """
        Return the full NHL team name.

        Priority:
          1. Canonical hard-coded map (guaranteed correctness)
          2. NHL standings payload (best-effort)
          3. Team code fallback
        """
        if team_code in NHL_TEAM_NAMES:
            return NHL_TEAM_NAMES[team_code]

        _, names = get_team_registry()
        return names.get(team_code, team_code)

    # -------------------------
    # Per-request service wiring
    # -------------------------

    def make_games_service(team_code: str) -> GamesService:
        """
        Create a GamesService bound to a specific team code.

        This is intentionally per-request so concurrent requests for different teams
        do not share mutable state.
        """
        return GamesService(
            client=client,
            cache=cache,
            tz_name=cfg.tz,
            team_code=team_code,
            schedule_ttl=cfg.cache_ttl_seconds,
            tv_ttl=cfg.cache_ttl_seconds,
            preferred_network_names=cfg.preferred_network_names,
            network_name_map=cfg.network_name_map,
            network_name_patterns=cfg.network_name_patterns,
        )

    def make_handler(team_code: str) -> WidgetHandler:
        """
        Build a WidgetHandler for a given team.

        Standings service is shared (league-wide), games service is team-bound.
        """
        games_for_team = make_games_service(team_code)
        return WidgetHandler(
            games_service=games_for_team,
            standings_service=standings,
            default_division=cfg.default_division,
        )

    # -------------------------
    # Shared parsing helpers
    # -------------------------

    def parse_theme() -> str:
        """
        Parse theme query param with a safe default.
        Supports: dark, light, transparent
        """
        theme = (request.args.get("theme") or "dark").strip().lower()
        if theme not in ("dark", "light", "transparent"):
            theme = "dark"
        return theme

    def parse_int(name: str, default: int) -> int:
        """Parse an integer query param with default fallback."""
        try:
            return int(request.args.get(name, default))
        except Exception:
            return default

    def parse_bool(name: str, default: bool = True) -> bool:
        """
        Parse a boolean-ish query param.

        Treats these as false: 0, false, no, off
        """
        raw = request.args.get(name)
        if raw is None:
            return default
        return raw.strip().lower() not in ("0", "false", "no", "off")

    def parse_division() -> str:
        """Parse division query param with default fallback."""
        return (request.args.get("division") or cfg.default_division).strip()

    # -------------------------
    # Legacy redirects (optional)
    # -------------------------

    @app.get("/widget")
    def widget_legacy():
        """Legacy route redirect to hockey widget."""
        return redirect("/widget/hockey", code=302)

    @app.get("/api")
    def api_legacy():
        """Legacy route redirect to hockey API."""
        return redirect("/api/hockey", code=302)

    # -------------------------
    # Hockey HTML routes
    # -------------------------

    @app.get("/widget/hockey")
    def widget_hockey():
        """
        Full hockey widget (upcoming + recent + optional standings).

        Query:
          - team=XXX (optional)
          - theme=dark|light|transparent
          - upcoming=N
          - recent=N
          - standings=1|0
          - division=Central
        """
        theme = parse_theme()
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)

        limit_upcoming = parse_int("upcoming", cfg.limit_upcoming)
        limit_recent = parse_int("recent", cfg.limit_recent)
        include_standings = parse_bool("standings", True)
        division = parse_division()

        handler = make_handler(team_code)
        ctx = handler.build_context(
            limit_upcoming=limit_upcoming,
            limit_recent=limit_recent,
            include_standings=include_standings,
            division=division,
        )

        # Provide template helpers:
        # - team_code: chosen team
        # - team_name: best-effort display name
        # - highlight_team: standings should highlight this team (not just MIN)
        ctx["team_code"] = team_code
        ctx["team_name"] = team_name
        ctx["highlight_team"] = team_code

        return render_template("hockey/widget.html", theme=theme, **ctx)

    @app.get("/widget/hockey/upcoming")
    def widget_hockey_upcoming():
        """
        Upcoming-only hockey widget.

        Query:
          - team=XXX (optional)
          - theme=dark|light|transparent
          - upcoming=N
        """
        theme = parse_theme()
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)

        limit_upcoming = parse_int("upcoming", cfg.limit_upcoming)

        handler = make_handler(team_code)
        ctx = handler.build_context(
            limit_upcoming=limit_upcoming,
            limit_recent=0,
            include_standings=False,
            division=None,
        )

        ctx["team_code"] = team_code
        ctx["team_name"] = team_name
        ctx["highlight_team"] = team_code

        return render_template("hockey/upcoming.html", theme=theme, **ctx)

    @app.get("/widget/hockey/recent")
    def widget_hockey_recent():
        """
        Recent-only hockey widget.

        Query:
          - team=XXX (optional)
          - theme=dark|light|transparent
          - recent=N
        """
        theme = parse_theme()
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)

        limit_recent = parse_int("recent", cfg.limit_recent)

        handler = make_handler(team_code)
        ctx = handler.build_context(
            limit_upcoming=0,
            limit_recent=limit_recent,
            include_standings=False,
            division=None,
        )

        ctx["team_code"] = team_code
        ctx["team_name"] = team_name
        ctx["highlight_team"] = team_code

        return render_template("hockey/recent.html", theme=theme, **ctx)

    @app.get("/widget/hockey/standings")
    def widget_hockey_standings():
        """
        Standings-only hockey widget.

        Query:
          - team=XXX (optional; used for highlight only)
          - theme=dark|light|transparent
          - division=Central
        """
        theme = parse_theme()
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)

        division = parse_division()

        handler = make_handler(team_code)
        ctx = handler.build_context(
            limit_upcoming=0,
            limit_recent=0,
            include_standings=True,
            division=division,
        )

        ctx["team_code"] = team_code
        ctx["team_name"] = team_name
        ctx["highlight_team"] = team_code

        return render_template("hockey/standings.html", theme=theme, **ctx)

    ## -------------------------
    # Hockey Banner widget
    # -------------------------

    @app.get("/widget/hockey/banner")
    def widget_hockey_banner():
        """
        Render a simple team banner widget.

        Query:
          - bg: CSS color/gradient for background (optional)
          - logo: URL to team logo image (optional)
          - team: NHL team code (optional, controls name + auto-colors)
          - height: banner height in px (optional)
        """
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)

        branding = TEAM_BRANDING.get(team_code, DEFAULT_BRANDING)

        bg_param = request.args.get("bg")
        bg = (bg_param.strip() if isinstance(bg_param, str) and bg_param.strip() else branding["bg"])

        logo = (request.args.get("logo") or "").strip()
        if logo and not logo.startswith(("http://", "https://")):
            logo = ""

        height_raw = request.args.get("height", "64")
        try:
            height_px = int(height_raw)
        except Exception:
            height_px = 64

        return render_template(
            "hockey/banner.html",
            bg=bg,
            fg=branding["fg"],
            accent=branding["accent"],
            font_family=branding["font_family"],
            font_href=branding["font_href"],
            height=height_px,
            logo=logo,
            team_code=team_code,
            team_name=team_name,
        )

    # -------------------------
    # Hockey JSON routes
    # -------------------------

    def game_to_dict(g) -> Dict[str, Any]:
        """
        Serialize a Game model into JSON-safe primitives.

        Includes:
          - networks: list[str]
          - result: "W" | "L" | ""
        """
        return {
            "when": g.when.isoformat(),
            "date_str": g.date_str,
            "time_str": g.time_str,
            "opponent": g.opponent,
            "homeaway": g.homeaway,
            "state": g.state,
            "score": g.score,
            "is_live": g.is_live,
            "is_final": g.is_final,
            "live_label": g.live_label,
            "result": getattr(g, "result", ""),
            "game_id": g.game_id,
            "date_key": g.date_key,
            "networks": list(g.networks) if g.networks is not None else [],
        }

    def standings_row_to_dict(s) -> Dict[str, Any]:
        """Serialize a StandingsRow model into JSON-safe primitives."""
        return {
            "team": s.team,
            "abbr": s.abbr,
            "gp": s.gp,
            "w": s.w,
            "l": s.l,
            "otl": s.otl,
            "pts": s.pts,
            "rw": s.rw,
            "row": s.row,
            "strk": s.strk,
            "dif": s.dif,
            "gf": s.gf,
            "ga": s.ga,
            "home": s.home,
            "away": s.away,
        }

    @app.get("/api/hockey")
    def api_hockey():
        """
        Full hockey JSON payload (upcoming + recent + optional standings).

        Query:
          - team=XXX (optional)
          - upcoming=N
          - recent=N
          - standings=1|0
          - division=Central
        """
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)

        limit_upcoming = parse_int("upcoming", cfg.limit_upcoming)
        limit_recent = parse_int("recent", cfg.limit_recent)
        include_standings = parse_bool("standings", True)
        division = parse_division()

        handler = make_handler(team_code)
        vm = handler.build(
            limit_upcoming=limit_upcoming,
            limit_recent=limit_recent,
            include_standings=include_standings,
            division=division,
        )

        out: Dict[str, Any] = {
            "generatedAt": vm.now.isoformat(),
            "sport": "hockey",
            "team": team_code,
            "teamName": team_name,
            "upcoming": [game_to_dict(g) for g in vm.upcoming],
            "recent": [game_to_dict(g) for g in vm.recent],
        }

        if vm.standings is not None:
            out["division"] = vm.division
            out["standingsGeneratedAt"] = vm.standings_generated_at.isoformat() if vm.standings_generated_at else None
            out["standings"] = [standings_row_to_dict(s) for s in vm.standings]
            out["highlightTeam"] = team_code

        return jsonify(out)

    @app.get("/api/hockey/upcoming")
    def api_hockey_upcoming():
        """
        Upcoming-only hockey JSON payload.

        Query:
          - team=XXX (optional)
          - upcoming=N
        """
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)
        limit_upcoming = parse_int("upcoming", cfg.limit_upcoming)

        handler = make_handler(team_code)
        vm = handler.build(
            limit_upcoming=limit_upcoming,
            limit_recent=0,
            include_standings=False,
            division=None,
        )

        return jsonify(
            {
                "generatedAt": vm.now.isoformat(),
                "sport": "hockey",
                "team": team_code,
                "teamName": team_name,
                "upcoming": [game_to_dict(g) for g in vm.upcoming],
            }
        )

    @app.get("/api/hockey/recent")
    def api_hockey_recent():
        """
        Recent-only hockey JSON payload.

        Query:
          - team=XXX (optional)
          - recent=N
        """
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)
        limit_recent = parse_int("recent", cfg.limit_recent)

        handler = make_handler(team_code)
        vm = handler.build(
            limit_upcoming=0,
            limit_recent=limit_recent,
            include_standings=False,
            division=None,
        )

        return jsonify(
            {
                "generatedAt": vm.now.isoformat(),
                "sport": "hockey",
                "team": team_code,
                "teamName": team_name,
                "recent": [game_to_dict(g) for g in vm.recent],
            }
        )

    @app.get("/api/hockey/standings")
    def api_hockey_standings():
        """
        Standings-only hockey JSON payload.

        Query:
          - team=XXX (optional; used for highlight only)
          - division=Central
        """
        team_code = get_team_code()
        team_name = get_team_display_name(team_code)
        division = parse_division()

        handler = make_handler(team_code)
        vm = handler.build(
            limit_upcoming=0,
            limit_recent=0,
            include_standings=True,
            division=division,
        )

        return jsonify(
            {
                "generatedAt": vm.now.isoformat(),
                "sport": "hockey",
                "team": team_code,
                "teamName": team_name,
                "division": vm.division,
                "standingsGeneratedAt": vm.standings_generated_at.isoformat() if vm.standings_generated_at else None,
                "standings": [standings_row_to_dict(s) for s in (vm.standings or [])],
                "highlightTeam": team_code,
            }
        )

    # -------------------------
    # Health
    # -------------------------

    @app.get("/health")
    def health():
        """Simple health endpoint for Docker/monitoring checks."""
        return {"ok": True}

    return app


# WSGI entrypoint for gunicorn (Docker CMD uses: app:app)
app = create_app()

if __name__ == "__main__":
    # Dev server (not for production).
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)
