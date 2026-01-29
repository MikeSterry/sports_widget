import os
from datetime import datetime

from dateutil import tz
from flask import Flask, jsonify, render_template, request

from nhl import NHL

APP_TZ = tz.gettz(os.getenv("TZ", "America/Chicago"))

NHL_API_BASE = "https://api-web.nhle.com"
TEAM_CODE = os.getenv("TEAM_CODE", "MIN")  # Minnesota Wild = "MIN"
DEFAULT_DIVISION = os.getenv("DEFAULT_DIVISION", "Central")  # Central (Western Conference)

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))
STANDINGS_CACHE_TTL_SECONDS = int(os.getenv("STANDINGS_CACHE_TTL_SECONDS", "300"))


DEFAULT_LIMIT_UPCOMING = int(os.getenv("LIMIT_UPCOMING", "5"))
DEFAULT_LIMIT_RECENT = int(os.getenv("LIMIT_RECENT", "5"))

nhl = NHL(nhl_api_base=NHL_API_BASE, team_code=TEAM_CODE, division=DEFAULT_DIVISION, cache_ttl_seconds=CACHE_TTL_SECONDS,
          standings_cache_ttl_seconds=STANDINGS_CACHE_TTL_SECONDS, app_tz=APP_TZ)

app = Flask(__name__)

def now_local() -> datetime:
    return datetime.now(tz=APP_TZ)


# ---------- Routes ----------

@app.route("/widget/hockey")
def widget_hockey():
    theme = request.args.get("theme", "dark").lower()
    limit_upcoming = int(request.args.get("upcoming", DEFAULT_LIMIT_UPCOMING))
    limit_recent = int(request.args.get("recent", DEFAULT_LIMIT_RECENT))

    # standings controls
    include_standings = request.args.get("standings", "1").lower() not in ("0", "false", "no", "off")
    division = request.args.get("division", DEFAULT_DIVISION)

    vm = nhl.build_view_model(
        limit_upcoming=limit_upcoming,
        limit_recent=limit_recent,
        division=division,
        include_standings=include_standings,
    )
    return render_template("hockey-widget.html", theme=theme, **vm)

@app.route("/widget/hockey/upcoming")
def widget_hockey_upcoming():
    theme = request.args.get("theme", "dark").lower()
    limit_upcoming = int(request.args.get("upcoming", DEFAULT_LIMIT_UPCOMING))
    limit_recent = 0
    include_standings = False
    division = request.args.get("division", DEFAULT_DIVISION)

    vm = nhl.build_view_model(
        limit_upcoming=limit_upcoming,
        limit_recent=limit_recent,
        division=division,
        include_standings=include_standings,
    )
    return render_template("hockey-upcoming-widget.html", theme=theme, **vm)

@app.route("/widget/hockey/recent")
def widget_hockey_recent():
    theme = request.args.get("theme", "dark").lower()
    limit_upcoming = 0
    limit_recent = int(request.args.get("recent", DEFAULT_LIMIT_RECENT))
    include_standings = False

    division = request.args.get("division", DEFAULT_DIVISION)

    vm = nhl.build_view_model(
        limit_upcoming=limit_upcoming,
        limit_recent=limit_recent,
        division=division,
        include_standings=include_standings,
    )
    return render_template("hockey-recent-widget.html", theme=theme, **vm)

@app.route("/widget/hockey/standings")
def widget_hockey_standings():
    theme = request.args.get("theme", "dark").lower()
    limit_upcoming = 0
    limit_recent = 0
    include_standings = True

    division = request.args.get("division", DEFAULT_DIVISION)

    vm = nhl.build_view_model(
        limit_upcoming=limit_upcoming,
        limit_recent=limit_recent,
        division=division,
        include_standings=include_standings,
    )
    return render_template("hockey-standings-widget.html", theme=theme, **vm)

@app.route("/api")
def api():
    limit_upcoming = int(request.args.get("upcoming", DEFAULT_LIMIT_UPCOMING))
    limit_recent = int(request.args.get("recent", DEFAULT_LIMIT_RECENT))
    vm = nhl.build_view_model(limit_upcoming=limit_upcoming, limit_recent=limit_recent)

    def serialize_game(g):
        return {**{k: v for k, v in g.items() if k != "when"}, "when": g["when"].isoformat()}

    return jsonify(
        {
            "generatedAt": now_local().isoformat(),
            "team": TEAM_CODE,
            "upcoming": [serialize_game(g) for g in vm["upcoming"]],
            "recent": [serialize_game(g) for g in vm["recent"]],
        }
    )


@app.route("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)