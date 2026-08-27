"""Expiring in-memory session store for multi-step Telegram flows.

The clarify, promotion, tickle and Q&A flows each kept a bare module-level dict with
no eviction, so a flow abandoned mid-way (or one that errored before its cleanup line)
left state behind forever and the "session expired" branches were unreachable.

State is deliberately process-local: these are short interactive flows, and losing them
on restart is correct behaviour.
"""

import time
from typing import Any

DEFAULT_TTL_SECONDS = 30 * 60


class SessionStore:
    """A dict keyed by chat_id whose entries expire after ``ttl`` seconds."""

    def __init__(self, name: str, ttl: int = DEFAULT_TTL_SECONDS):
        self.name = name
        self.ttl = ttl
        self._data: dict[Any, tuple[float, Any]] = {}

    def _purge(self) -> None:
        cutoff = time.monotonic() - self.ttl
        for key in [k for k, (ts, _) in self._data.items() if ts < cutoff]:
            del self._data[key]

    def get(self, key, default=None) -> Any | None:
        self._purge()
        entry = self._data.get(key)
        return entry[1] if entry else default

    def set(self, key, value) -> None:
        self._purge()
        self._data[key] = (time.monotonic(), value)

    def pop(self, key, default=None):
        self._purge()
        entry = self._data.pop(key, None)
        return entry[1] if entry else default

    def touch(self, key) -> None:
        """Reset the expiry clock, e.g. after each step of a guided flow."""
        if key in self._data:
            self._data[key] = (time.monotonic(), self._data[key][1])

    # Mapping-style sugar so existing call sites read unchanged
    def __contains__(self, key) -> bool:
        self._purge()
        return key in self._data

    def __getitem__(self, key):
        value = self.get(key)
        if value is None and key not in self:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value) -> None:
        self.set(key, value)

    def __delitem__(self, key) -> None:
        self._data.pop(key, None)

    def __len__(self) -> int:
        self._purge()
        return len(self._data)
