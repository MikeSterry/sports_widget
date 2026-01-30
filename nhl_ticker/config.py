# nhl_ticker/config.py
"""
Configuration for the MN Wild ticker.

This module centralizes all tunable settings (timezone, API base URL, cache TTLs,
standings division, and network display preferences).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import List, Tuple, Dict


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, returning default on missing/invalid values."""
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_json(name: str, default):
    """
    Read a JSON environment variable and parse it.

    Intended for:
      - NETWORK_NAME_PATTERNS_JSON: [["FDS*", "FanDuel Sports North"], ...]
      - PREFERRED_NETWORK_NAMES_JSON: ["TNT", "FDSN*", ...]
      - NETWORK_NAME_MAP_JSON: {"ESPN Select":"ESPN+", ...}

    Returns default on missing/invalid JSON.
    """
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _env_pairs(name: str, default: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    Read a semicolon-delimited PATTERN=NAME list.

    Example:
      NETWORK_NAME_PATTERNS="FDS*=FanDuel Sports North;ESPN*=ESPN;Prime*=Prime Video"

    Returns default if unset or malformed.
    """
    raw = os.getenv(name)
    if not raw:
        return default

    pairs: List[Tuple[str, str]] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        pat, val = part.split("=", 1)
        pat = pat.strip()
        val = val.strip()
        if pat and val:
            pairs.append((pat, val))

    return pairs or default


def _env_list(name: str, default: List[str]) -> List[str]:
    """
    Read a comma-delimited string list from the environment.

    Example:
      PREFERRED_NETWORK_NAMES="TNT,TruTV,ESPN*,FDSN*,FDS*,Prime*"
    """
    raw = os.getenv(name)
    if not raw:
        return default
    out = [x.strip() for x in raw.split(",") if x.strip()]
    return out or default


@dataclass(frozen=True)
class AppConfig:
    """
    Immutable app configuration.

    Notes on network config:
      - preferred_network_names: ordering & filtering. Supports wildcards (*).
      - network_name_patterns: first-match mapping. Supports wildcards (*).
      - network_name_map: exact mapping.
    """

    # Core settings
    tz: str = os.getenv("TZ", "America/Chicago")
    nhl_api_base: str = os.getenv("NHL_API_BASE", "https://api-web.nhle.com")
    team_code: str = os.getenv("TEAM_CODE", "MIN")

    # Cache controls
    cache_ttl_seconds: int = _env_int("CACHE_TTL_SECONDS", 60)
    standings_cache_ttl_seconds: int = _env_int("STANDINGS_CACHE_TTL_SECONDS", 300)

    # Widget defaults
    limit_upcoming: int = _env_int("LIMIT_UPCOMING", 8)
    limit_recent: int = _env_int("LIMIT_RECENT", 5)
    default_division: str = os.getenv("DEFAULT_DIVISION", "Central")

    # Exact-name map
    network_name_map: Dict[str, str] = field(default_factory=lambda: {
        "ESPN Select": "ESPN+",
        "ESPN": "ESPN",
        "TNT": "TNT",
        "TruTV": "TruTV",
        "Prime": "Prime Video",
    })

    # Pattern-based mapping (wildcards)
    network_name_patterns: List[Tuple[str, str]] = field(default_factory=lambda: [
        ("FDS*", "FanDuel Sports North"),
    ])

    # Preferred ordering/filtering (wildcards)
    preferred_network_names: List[str] = field(default_factory=lambda: [
        "TNT",
        "TruTV",
        "ESPN*",
        "FDSN*",
        "FDS*",
        "Prime*",
        "ESPN Select",
    ])

    def __post_init__(self):
        """
        Override defaults from optional env vars.

        Supported env options:
          - PREFERRED_NETWORK_NAMES (comma list)
          - PREFERRED_NETWORK_NAMES_JSON (JSON list)
          - NETWORK_NAME_PATTERNS (semicolon PAT=NAME;PAT=NAME)
          - NETWORK_NAME_PATTERNS_JSON (JSON list of [pat, name])
          - NETWORK_NAME_MAP_JSON (JSON dict)
        """
        # dataclass frozen => use object.__setattr__
        preferred = _env_json("PREFERRED_NETWORK_NAMES_JSON", None)
        if isinstance(preferred, list) and all(isinstance(x, str) for x in preferred):
            object.__setattr__(self, "preferred_network_names", preferred)
        else:
            object.__setattr__(
                self,
                "preferred_network_names",
                _env_list("PREFERRED_NETWORK_NAMES", list(self.preferred_network_names)),
            )

        patterns_json = _env_json("NETWORK_NAME_PATTERNS_JSON", None)
        if isinstance(patterns_json, list):
            parsed: List[Tuple[str, str]] = []
            for item in patterns_json:
                if isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item):
                    parsed.append((item[0], item[1]))
            if parsed:
                object.__setattr__(self, "network_name_patterns", parsed)
        else:
            object.__setattr__(
                self,
                "network_name_patterns",
                _env_pairs("NETWORK_NAME_PATTERNS", list(self.network_name_patterns)),
            )

        name_map_json = _env_json("NETWORK_NAME_MAP_JSON", None)
        if isinstance(name_map_json, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in name_map_json.items()):
            object.__setattr__(self, "network_name_map", name_map_json)
