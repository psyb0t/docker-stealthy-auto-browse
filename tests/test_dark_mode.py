"""Unit tests for DESK_DARK_MODE starting pages in the dark color scheme."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, "/app")

import browser  # noqa: E402


def _config(value: str | None) -> browser.BrowserConfig:
    environment = {} if value is None else {"DESK_DARK_MODE": value}
    with patch.dict(os.environ, environment, clear=True):
        return browser.BrowserConfig.from_environment()


def test_dark_mode_is_off_unless_set() -> None:
    assert _config(None).dark_mode is False
    for value in ("false", "0", "no", "off"):
        assert _config(value).dark_mode is False, value


def test_dark_mode_accepts_the_base_values() -> None:
    for value in ("true", "1", "yes", "on"):
        assert _config(value).dark_mode is True, value


def test_dark_mode_refuses_other_values() -> None:
    for value in ("", "TRUE", "maybe", "true; touch /tmp/x"):
        try:
            _config(value)
        except browser.BrowserError as error:
            assert "DESK_DARK_MODE" in str(error)
        else:
            raise AssertionError(f"expected {value!r} to be refused")


def test_dark_mode_starts_pages_dark() -> None:
    dark = browser.Browser(browser.BrowserConfig(dark_mode=True))
    assert dark.color_scheme == "dark"
    plain = browser.Browser(browser.BrowserConfig())
    assert plain.color_scheme == browser.COLOR_SCHEME_DEFAULT


def main_test() -> None:
    test_dark_mode_is_off_unless_set()
    test_dark_mode_accepts_the_base_values()
    test_dark_mode_refuses_other_values()
    test_dark_mode_starts_pages_dark()
    print(json.dumps({"result": "dark mode tests passed"}))


if __name__ == "__main__":
    main_test()
