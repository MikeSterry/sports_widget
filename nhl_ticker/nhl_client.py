# nhl_ticker/nhl_client.py
"""
Thin HTTP client wrapper for NHL endpoints.
"""

from __future__ import annotations

import requests
from typing import Any, Dict


class NHLClient:
    """A minimal client for retrieving JSON from the NHL API base."""

    def __init__(self, base_url: str) -> None:
        """Store the base URL and build request headers."""
        self.base_url = base_url.rstrip("/")
        self._headers = {"User-Agent": "mnwild-ticker/2.1"}

    def get_json(self, path: str, timeout: int = 10) -> Dict[str, Any]:
        """
        Execute a GET request to base_url + path and return parsed JSON.

        Raises:
            requests.HTTPError on non-2xx responses.
        """
        url = f"{self.base_url}{path}"
        r = requests.get(url, timeout=timeout, headers=self._headers)
        r.raise_for_status()
        return r.json()

    def team_schedule_now(self, team_code: str) -> Dict[str, Any]:
        """Fetch the current season schedule (relative to now) for a team code (e.g., MIN)."""
        return self.get_json(f"/v1/club-schedule-season/{team_code}/now")

    def tv_schedule_for_date(self, yyyy_mm_dd: str) -> Dict[str, Any]:
        """Fetch the TV schedule payload for a given date (YYYY-MM-DD)."""
        return self.get_json(f"/v1/network/tv-schedule/{yyyy_mm_dd}")

    def standings_now(self) -> Dict[str, Any]:
        """Fetch standings payload relative to 'now'."""
        return self.get_json("/v1/standings/now")
