# nhl_ticker/cache.py
"""
Simple in-memory TTL cache.

This is per-process cache. If you run multiple gunicorn workers, each worker has its own cache.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """A single cached value and the timestamp when it was set."""
    ts: float
    value: Optional[T]


class TTLCache:
    """A small key/value TTL cache with lazy loading."""

    def __init__(self) -> None:
        """Initialize an empty cache store."""
        self._store: dict[str, CacheEntry] = {}

    def get_or_set(self, key: str, ttl_seconds: int, loader: Callable[[], T]) -> T:
        """
        Retrieve a cached value if not expired, otherwise compute & store a new value.

        Args:
            key: Cache key.
            ttl_seconds: Time-to-live for the entry.
            loader: Function that returns the value if the cache is stale/missing.

        Returns:
            The cached or newly loaded value.
        """
        now = time.time()
        entry = self._store.get(key)

        if entry and entry.value is not None and (now - entry.ts) < ttl_seconds:
            return entry.value

        value = loader()
        self._store[key] = CacheEntry(ts=now, value=value)
        return value

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()
