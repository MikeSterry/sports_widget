# nhl_ticker/handlers/widget_handler.py
"""
Handler/controller responsible for building the full widget view model.

Keeps Flask routes simple by concentrating assembly logic here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from dataclasses import asdict

from ..models import WidgetViewModel
from ..services.games_service import GamesService
from ..services.standings_service import StandingsService


@dataclass
class WidgetHandler:
    """Orchestrates games + standings services into a single view model."""

    games_service: GamesService
    standings_service: StandingsService
    default_division: str

    def build(
        self,
        limit_upcoming: int,
        limit_recent: int,
        include_standings: bool,
        division: Optional[str],
    ) -> WidgetViewModel:
        """
        Build a widget view model for the current request.

        Args:
            limit_upcoming: number of upcoming games to include.
            limit_recent: number of recent games to include.
            include_standings: whether to include standings data.
            division: division name/abbrev to filter standings.

        Returns:
            WidgetViewModel ready for template rendering.
        """
        upcoming, recent = self.games_service.get_games(limit_upcoming, limit_recent)
        now = datetime.now(tz=self.games_service.app_tz)

        if not include_standings:
            return WidgetViewModel(now=now, upcoming=upcoming, recent=recent)

        div = (division or self.default_division).strip()
        standings = self.standings_service.get_division(div)

        return WidgetViewModel(
            now=now,
            upcoming=upcoming,
            recent=recent,
            division=div,
            standings=standings,
            standings_generated_at=now,
        )

    def build_context(
        self,
        limit_upcoming: int,
        limit_recent: int,
        include_standings: bool,
        division: Optional[str],
    ) -> dict:
        """
        Build a plain dict suitable for render_template(**context).

        We keep this separate from build() so different endpoints/templates can reuse
        the same data without repeating assembly logic.
        """
        vm = self.build(
            limit_upcoming=limit_upcoming,
            limit_recent=limit_recent,
            include_standings=include_standings,
            division=division,
        )

        # dataclasses in vm are already JSON-ish; for Jinja, a flat dict is fine
        return vm.__dict__
