"""Unit tests for excluding Camoufox's self-managed default addon.

Camoufox downloads its own uBlock Origin at launch. When that fetch fails it
leaves an empty ``addons/UBO`` cache directory behind and never re-validates
it, so every later launch appends the empty path and ``confirm_paths`` raises
``InvalidAddonPath`` (``manifest.json is missing``), crash-looping the browser.
This project installs uBlock Origin through ``distribution/policies.json``
instead, so the launch excludes the default addon. These tests reproduce the
poisoned-cache failure and prove the launch passes the exclusion.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/app")

import browser
import camoufox.addons as camoufox_addons
from camoufox.addons import DefaultAddons, add_default_addons, confirm_paths
from camoufox.exceptions import InvalidAddonPath

MANIFEST_FILENAME = "manifest.json"


class _StopLaunch(Exception):
    """Halt _launch_browser right after launch_options is captured."""


def test_poisoned_default_addon_cache_is_bypassed_when_excluded() -> None:
    with tempfile.TemporaryDirectory() as directory:
        poisoned_path = Path(directory) / DefaultAddons.UBO.name
        poisoned_path.mkdir()  # empty dir, no manifest.json: the failure state

        def fake_addon_path(name: str) -> str:
            return str(Path(directory) / name)

        with patch.object(camoufox_addons, "get_addon_path", fake_addon_path):
            # Without the exclusion Camoufox appends the poisoned cache dir and
            # confirm_paths rejects it, reproducing the launch crash loop.
            included: list[str] = []
            add_default_addons(included, None)
            assert included == [str(poisoned_path)]
            try:
                confirm_paths(included)
            except InvalidAddonPath as error:
                assert MANIFEST_FILENAME in str(error)
            else:
                raise AssertionError("expected InvalidAddonPath from poisoned cache")

            # Excluding uBlock Origin keeps the poisoned path out entirely.
            excluded: list[str] = []
            add_default_addons(excluded, [DefaultAddons.UBO])
            assert excluded == []
            confirm_paths(excluded)  # nothing to reject


async def test_launch_browser_excludes_default_ublock_addon() -> None:
    captured: dict[str, object] = {}

    def capture_launch_options(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise _StopLaunch

    fake_playwright = MagicMock()
    fake_playwright.start = AsyncMock(return_value=MagicMock())

    with (
        patch("playwright.async_api.async_playwright", return_value=fake_playwright),
        patch("camoufox.utils.launch_options", side_effect=capture_launch_options),
        patch.object(browser, "_load_persisted_config", return_value={}),
        patch.object(browser, "_update_config_screen"),
        patch.object(browser, "_save_config"),
        patch.object(browser.Browser, "stop", new=AsyncMock()),
    ):
        instance = browser.Browser()
        try:
            await instance._launch_browser()
        except browser.BrowserError:
            pass
        else:
            raise AssertionError("expected launch to stop at the launch_options spy")

    assert captured["exclude_addons"] == [DefaultAddons.UBO]


def main_test() -> None:
    test_poisoned_default_addon_cache_is_bypassed_when_excluded()
    asyncio.run(test_launch_browser_excludes_default_ublock_addon())
    print(json.dumps({"result": "addon exclusion tests passed"}))


if __name__ == "__main__":
    main_test()
