"""Unit tests for excluding Camoufox's self-managed default addon.

Camoufox downloads its own uBlock Origin into a shared cache at launch. This
project installs uBlock Origin through ``distribution/policies.json`` at build
time instead, so the launch excludes the default addon: no second copy, and no
network fetch on every start. These tests prove the exclusion skips the
download and that the launch passes it.
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

MANIFEST_FILENAME = "manifest.json"


class _StopLaunch(Exception):
    """Halt _launch_browser right after launch_options is captured."""


def test_default_addon_is_downloaded_unless_excluded() -> None:
    with tempfile.TemporaryDirectory() as directory:

        def fake_addon_path(name: str) -> str:
            return str(Path(directory) / name)

        def fake_download(_url: str, extract_path: str, _name: str) -> None:
            Path(extract_path, MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

        download = MagicMock(side_effect=fake_download)
        with (
            patch.object(camoufox_addons, "get_addon_path", fake_addon_path),
            patch.object(camoufox_addons, "download_and_extract", download),
        ):
            # Without the exclusion Camoufox fetches its own uBlock Origin.
            included: list[str] = []
            add_default_addons(included, None)
            assert included == [fake_addon_path(DefaultAddons.UBO.name)]
            confirm_paths(included)
            assert download.call_count == 1

            # Excluding it skips the fetch and adds nothing to the launch.
            download.reset_mock()
            excluded: list[str] = []
            add_default_addons(excluded, [DefaultAddons.UBO])
            assert excluded == []
            download.assert_not_called()


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
    test_default_addon_is_downloaded_unless_excluded()
    asyncio.run(test_launch_browser_excludes_default_ublock_addon())
    print(json.dumps({"result": "addon exclusion tests passed"}))


if __name__ == "__main__":
    main_test()
