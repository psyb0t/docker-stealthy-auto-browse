"""Unit tests for explicit browser navigation controls."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app")

import main
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


class FakePage:
    """Record navigation calls and replay configured outcomes."""

    def __init__(self, outcomes: list[Exception | None] | None = None) -> None:
        self.goto_calls: list[tuple[str, dict[str, Any]]] = []
        self.outcomes = outcomes or []
        self.url = "https://current.example/"
        self.wait_for_load_state = AsyncMock()

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_calls.append((url, kwargs))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if outcome is not None:
                raise outcome
        self.url = url

    async def title(self) -> str:
        return "Navigation Test"


def assert_value_error(command: dict[str, Any], expected: str) -> None:
    try:
        main._navigation_options(command)
    except ValueError as error:
        assert expected in str(error)
        return
    raise AssertionError("expected ValueError")


def test_navigation_option_defaults_and_limits() -> None:
    defaults = main._navigation_options({})
    assert defaults.timeout_seconds == main.NAVIGATION_TIMEOUT_DEFAULT_SECONDS
    assert defaults.retry_count == main.NAVIGATION_RETRY_COUNT_DEFAULT
    assert defaults.retry_delay_seconds == main.NAVIGATION_RETRY_DELAY_DEFAULT_SECONDS
    assert (
        defaults.timeout_milliseconds
        == main.NAVIGATION_TIMEOUT_DEFAULT_SECONDS
        * main._MILLISECONDS_PER_SECOND
    )

    assert main._navigation_options(
        {"timeout": 60, "retry_count": 0, "retry_delay": 0}
    ).retry_count == 0

    cases = [
        ({"timeout": True}, "timeout must be a number"),
        ({"timeout": math.nan}, "timeout must be finite"),
        ({"timeout": 0}, "timeout must be between"),
        ({"retry_count": 1.0}, "retry_count must be an integer"),
        ({"retry_count": 3}, "retry_count must be between"),
        ({"retry_delay": -1}, "retry_delay must be between"),
        (
            {"timeout": 60, "retry_count": 2, "retry_delay": 1},
            "navigation budget",
        ),
    ]
    for command, expected in cases:
        assert_value_error(command, expected)


async def test_navigation_timeout_retries_with_exponential_backoff() -> None:
    page = FakePage(
        [
            PlaywrightTimeoutError("first timeout"),
            PlaywrightTimeoutError("second timeout"),
            None,
        ]
    )
    options = main._navigation_options(
        {"timeout": 10, "retry_count": 2, "retry_delay": 2}
    )

    with patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main._navigate(page, "https://example.com/", options, wait_until="load")

    assert len(page.goto_calls) == 3
    assert [call[1]["timeout"] for call in page.goto_calls] == [
        10 * main._MILLISECONDS_PER_SECOND
    ] * 3
    assert [call[1]["wait_until"] for call in page.goto_calls] == ["load"] * 3
    assert [call.args[0] for call in sleep.await_args_list] == [2, 4]


async def test_navigation_does_not_retry_when_disabled_or_not_timed_out() -> None:
    no_retry_page = FakePage([PlaywrightTimeoutError("timeout")])
    no_retry_options = main._navigation_options(
        {"timeout": 10, "retry_count": 0, "retry_delay": 0}
    )
    try:
        await main._navigate(no_retry_page, "https://example.com/", no_retry_options)
    except PlaywrightTimeoutError:
        pass
    else:
        raise AssertionError("expected timeout")
    assert len(no_retry_page.goto_calls) == 1

    error_page = FakePage([RuntimeError("certificate error")])
    options = main._navigation_options({"timeout": 10, "retry_count": 1})
    try:
        await main._navigate(error_page, "https://example.com/", options)
    except RuntimeError as error:
        assert str(error) == "certificate error"
    else:
        raise AssertionError("expected non-timeout navigation error")
    assert len(error_page.goto_calls) == 1


async def test_dispatch_action_passes_explicit_controls_to_goto_and_refresh() -> None:
    page = FakePage()
    get_page = AsyncMock(return_value=page)
    command = {
        "action": "goto",
        "url": "https://example.com/",
        "wait_until": "load",
        "referer": "https://referrer.example/",
        "timeout": 2.5,
        "retry_count": 0,
        "retry_delay": 0,
    }
    with (
        patch.object(main, "find_loader", return_value=None),
        patch.object(main, "get_active_page", get_page),
    ):
        goto_result = await main.dispatch_action(command)
        refresh_result = await main.dispatch_action(
            {
                "action": "refresh",
                "timeout": 2.5,
                "retry_count": 0,
                "retry_delay": 0,
            }
        )

    assert goto_result["success"]
    assert refresh_result["success"]
    assert page.goto_calls[0] == (
        "https://example.com/",
        {
            "wait_until": "load",
            "referer": "https://referrer.example/",
            "timeout": 2.5 * main._MILLISECONDS_PER_SECOND,
        },
    )
    assert page.goto_calls[1] == (
        "https://example.com/",
        {
            "wait_until": "domcontentloaded",
            "timeout": 2.5 * main._MILLISECONDS_PER_SECOND,
        },
    )


async def test_dispatch_action_passes_the_app_default_timeout() -> None:
    page = FakePage()
    with (
        patch.object(main, "find_loader", return_value=None),
        patch.object(main, "get_active_page", new=AsyncMock(return_value=page)),
    ):
        result = await main.dispatch_action(
            {"action": "goto", "url": "https://example.com/"}
        )

    assert result["success"]
    assert page.goto_calls == [
        (
            "https://example.com/",
            {
                "wait_until": "domcontentloaded",
                "timeout": (
                    main.NAVIGATION_TIMEOUT_DEFAULT_SECONDS
                    * main._MILLISECONDS_PER_SECOND
                ),
            },
        )
    ]


async def test_new_tab_uses_explicit_navigation_controls() -> None:
    new_page = FakePage()
    context = SimpleNamespace(
        new_page=AsyncMock(return_value=new_page),
        pages=[new_page],
    )
    fake_browser = SimpleNamespace(_context=context)
    with (
        patch.object(main, "browser", fake_browser),
        patch.object(main, "_setup_page_handlers"),
        patch.object(main, "_focus_active_tab", new=AsyncMock()),
    ):
        result = await main.dispatch_action(
            {
                "action": "new_tab",
                "url": "https://example.com/",
                "timeout": 3,
                "retry_count": 0,
                "retry_delay": 0,
            }
        )

    assert result["success"]
    assert new_page.goto_calls == [
        (
            "https://example.com/",
            {
                "wait_until": "domcontentloaded",
                "timeout": 3 * main._MILLISECONDS_PER_SECOND,
            },
        )
    ]


def main_test() -> None:
    test_navigation_option_defaults_and_limits()
    asyncio.run(test_navigation_timeout_retries_with_exponential_backoff())
    asyncio.run(test_navigation_does_not_retry_when_disabled_or_not_timed_out())
    asyncio.run(test_dispatch_action_passes_explicit_controls_to_goto_and_refresh())
    asyncio.run(test_dispatch_action_passes_the_app_default_timeout())
    asyncio.run(test_new_tab_uses_explicit_navigation_controls())
    print(json.dumps({"result": "navigation option tests passed"}))


if __name__ == "__main__":
    main_test()
