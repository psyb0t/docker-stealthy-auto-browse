"""Bounded browser event storage and waiting."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

EVENT_CAPACITY = 1000
EVENT_WAIT_TIMEOUT_SECONDS = 30.0


class BrowserEventError(Exception):
    """An event request was invalid."""


class BrowserEventBus:
    """Keep a bounded process-local sequence of browser events."""

    def __init__(self, capacity: int = EVENT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._next_id = 1
        self._changed = asyncio.Event()

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> dict:
        if not event_type:
            raise BrowserEventError("event type required")
        event = {
            "id": self._next_id,
            "type": event_type,
            "timestamp": time.time(),
            "data": data or {},
        }
        self._next_id += 1
        self._events.append(event)
        changed = self._changed
        self._changed = asyncio.Event()
        changed.set()
        return dict(event)

    def get(
        self,
        *,
        after_id: int = 0,
        event_types: set[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if after_id < 0:
            raise BrowserEventError("after_id must be non-negative")
        if limit < 1 or limit > EVENT_CAPACITY:
            raise BrowserEventError(f"limit must be between 1 and {EVENT_CAPACITY}")
        latest_id = self._next_id - 1
        if after_id > latest_id:
            raise BrowserEventError("after_id cannot be newer than the latest event")
        gap: list[dict[str, Any]] = []
        if self._events and after_id < self._events[0]["id"] - 1:
            gap = [
                {
                    "id": self._events[0]["id"] - 1,
                    "type": "gap",
                    "timestamp": time.time(),
                    "data": {"oldest_available_id": self._events[0]["id"]},
                }
            ]
        matching = [
            dict(event)
            for event in self._events
            if event["id"] > after_id
            and (event_types is None or event["type"] in event_types)
        ]
        return (gap + matching)[:limit]

    async def wait(
        self,
        *,
        after_id: int = 0,
        event_types: set[str] | None = None,
        timeout: float = EVENT_WAIT_TIMEOUT_SECONDS,
    ) -> list[dict[str, Any]]:
        if timeout <= 0 or timeout > 300:
            raise BrowserEventError("timeout must be greater than 0 and at most 300")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            events = self.get(after_id=after_id, event_types=event_types)
            if events:
                return events
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return []
            changed = self._changed
            try:
                await asyncio.wait_for(changed.wait(), timeout=remaining)
            except TimeoutError:
                return []
