"""Unit tests for browser primitive foundations."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, "/app")

from browser_events import BrowserEventBus, BrowserEventError
from dom_targets import (
    ElementRegistry,
    TargetError,
    resolve_target,
    uses_plain_selector_target,
)


def test_plain_selector_target_preserves_legacy_path() -> None:
    assert uses_plain_selector_target({"selector": "#submit"})
    assert uses_plain_selector_target({"selector": "#submit", "frames": []})
    assert not uses_plain_selector_target(
        {"selector": "#submit", "frames": ["iframe"]}
    )
    assert not uses_plain_selector_target({"element_id": "element-reference"})
    assert not uses_plain_selector_target({"role": "button"})


def test_event_bus_filters_and_bounds() -> None:
    bus = BrowserEventBus(capacity=2)
    bus.publish("request", {"url": "https://example.invalid/one"})
    second = bus.publish("console", {"text": "hello"})
    third = bus.publish("request", {"url": "https://example.invalid/two"})
    events = bus.get()
    assert events[0]["type"] == "gap"
    assert [event["id"] for event in events[1:]] == [second["id"], third["id"]]
    assert bus.get(after_id=1, event_types={"console"}) == [second]
    assert bus.get(after_id=second["id"]) == [third]


def test_event_bus_waits_and_validates() -> None:
    async def exercise() -> None:
        bus = BrowserEventBus()
        waiter = asyncio.create_task(bus.wait(event_types={"popup"}, timeout=1))
        await asyncio.sleep(0)
        bus.publish("console", {})
        bus.publish("popup", {"url": "about:blank"})
        events = await waiter
        assert len(events) == 1
        assert events[0]["type"] == "popup"
        assert await bus.wait(timeout=0.01, after_id=events[0]["id"]) == []
        try:
            bus.get(limit=0)
        except BrowserEventError:
            pass
        else:
            raise AssertionError("invalid event limit was accepted")
        try:
            bus.get(after_id=100)
        except BrowserEventError:
            pass
        else:
            raise AssertionError("future event cursor was accepted")

    asyncio.run(exercise())


def test_registry_rejects_stale_and_wrong_page() -> None:
    async def exercise() -> None:
        registry = ElementRegistry(capacity=1, ttl_seconds=10)
        page = object()
        handle = AsyncMock()
        handle.evaluate.return_value = True
        first = await registry.add(page, handle)
        assert await registry.get(page, first) is handle
        try:
            await registry.get(object(), first)
        except TargetError as error:
            assert "stale or unknown" in str(error)
        else:
            raise AssertionError("cross-page element reference was accepted")
        replacement = AsyncMock()
        replacement.evaluate.return_value = True
        await registry.add(page, replacement)
        handle.dispose.assert_awaited_once()

    asyncio.run(exercise())


def test_target_validation_and_frame_role_chain() -> None:
    async def exercise() -> None:
        page = MagicMock()
        frame = page.frame_locator.return_value
        locator = frame.get_by_role.return_value
        locator.count = AsyncMock(return_value=1)
        registry = ElementRegistry()
        resolved = await resolve_target(
            page,
            {"frames": ["iframe#child"], "role": "button", "name": "Save"},
            registry,
        )
        assert resolved is locator
        page.frame_locator.assert_called_once_with("iframe#child")
        frame.get_by_role.assert_called_once_with("button", name="Save")
        for invalid in ({}, {"selector": "a", "role": "link"}):
            try:
                await resolve_target(page, invalid, registry)
            except TargetError:
                pass
            else:
                raise AssertionError("invalid target was accepted")
        for invalid in (
            {"role": "not-a-role"},
            {"selector": "a", "frames": ["iframe"] * 9},
            {"selector": "a" * 4097},
        ):
            try:
                await resolve_target(page, invalid, registry)
            except TargetError:
                pass
            else:
                raise AssertionError("unbounded target was accepted")

    asyncio.run(exercise())


if __name__ == "__main__":
    test_event_bus_filters_and_bounds()
    test_event_bus_waits_and_validates()
    test_registry_rejects_stale_and_wrong_page()
    test_target_validation_and_frame_role_chain()
