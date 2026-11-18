"""Deprecated names for the event bus, which now lives in auto-linux-desk."""

from autolinuxdesk.events import EVENT_CAPACITY, EventBus, EventError

BrowserEventBus = EventBus
BrowserEventError = EventError

__all__ = ["EVENT_CAPACITY", "BrowserEventBus", "BrowserEventError"]
