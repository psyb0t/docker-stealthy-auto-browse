"""Unit tests for persisted browser fingerprint consistency."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/app")

import browser


def _webgl_cohort(vendor: str = "Intel") -> dict[str, object]:
    extensions = [
        "ANGLE_instanced_arrays",
        "WEBGL_compressed_texture_astc",
        "WEBGL_compressed_texture_etc",
        "WEBGL_compressed_texture_etc1",
    ]
    return {
        "webGl:vendor": vendor,
        "webGl:renderer": f"{vendor} renderer",
        "webGl:supportedExtensions": extensions.copy(),
        "webGl2:supportedExtensions": extensions.copy(),
        "webGl2Enabled": True,
    }


def test_non_object_persisted_config_is_rejected() -> None:
    invalid_values = (None, [], "fingerprint", 1)
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "props.json"
        with patch.object(browser, "BROWSER_PROPS_FILE", config_path):
            for invalid_value in invalid_values:
                config_path.write_text(json.dumps(invalid_value), encoding="utf-8")
                assert browser._load_persisted_config() is None


def test_known_persisted_webgl_identity_is_kept() -> None:
    config: dict[str, object] = _webgl_cohort()
    expected = dict(config)
    known_gpus = MagicMock(return_value=frozenset({("Intel", "Intel renderer")}))

    with patch("camoufox.webgl.firefox_gpus", known_gpus):
        browser._drop_unknown_webgl_identity(config)

    assert config == expected
    known_gpus.assert_called_once_with("lin")


def test_unknown_persisted_webgl_identity_is_dropped() -> None:
    config: dict[str, object] = {
        **_webgl_cohort("Removed"),
        "navigator.userAgent": "kept",
    }
    known_gpus = MagicMock(return_value=frozenset({("Intel", "Intel renderer")}))

    with patch("camoufox.webgl.firefox_gpus", known_gpus):
        browser._drop_unknown_webgl_identity(config)

    assert config == {"webGl2Enabled": True, "navigator.userAgent": "kept"}


def test_identity_without_webgl_is_left_for_camoufox() -> None:
    config: dict[str, object] = {"navigator.userAgent": "kept"}

    with patch("camoufox.webgl.firefox_gpus", MagicMock(return_value=frozenset())):
        browser._drop_unknown_webgl_identity(config)

    assert config == {"navigator.userAgent": "kept"}


def test_persisted_user_agent_follows_the_installed_firefox() -> None:
    config: dict[str, object] = {
        "navigator.userAgent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
        ),
        "headers.User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
        ),
        "navigator.appVersion": "5.0 (X11)",
    }

    browser._align_firefox_version(config, "156")

    expected = "Mozilla/5.0 (X11; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0"
    assert config["navigator.userAgent"] == expected
    assert config["headers.User-Agent"] == expected
    assert config["navigator.appVersion"] == "5.0 (X11)"


def test_stock_default_overrides_are_dropped() -> None:
    config: dict[str, object] = {
        "navigator.doNotTrack": "1",
        "navigator.globalPrivacyControl": True,
        "headers.Accept-Encoding": "gzip, deflate, br, zstd",
        "window.history.length": 3,
        "navigator.platform": "Linux x86_64",
    }

    browser._drop_stock_default_overrides(config)

    assert config == {"navigator.platform": "Linux x86_64"}


def test_screen_follows_display_and_window_geometry_is_released() -> None:
    config: dict[str, object] = {
        "screen.width": 1366,
        "screen.height": 768,
        "window.outerWidth": 1920,
        "window.outerHeight": 1080,
        "window.innerWidth": 1920,
        "window.innerHeight": 1000,
        "window.screenX": 0,
        "window.screenY": 0,
        "window.devicePixelRatio": 1,
    }

    browser._update_config_screen(config, 1920, 1080)

    assert config == {
        "screen.width": 1920,
        "screen.height": 1080,
        "screen.availWidth": 1920,
        "screen.availHeight": 1080,
        "screen.availLeft": 0,
        "screen.availTop": 0,
        "window.devicePixelRatio": 1,
    }


def test_runtime_fontconfig_uses_absolute_bundled_font_path() -> None:
    with tempfile.TemporaryDirectory() as directory:
        browser_root = Path(directory) / "camoufox"
        executable = browser_root / "camoufox-bin"
        fonts_directory = browser_root / "fonts"
        fontconfig_directory = browser_root / "fontconfig" / "linux"
        system_font_directories = (
            Path(directory) / "dejavu",
            Path(directory) / "liberation",
            Path(directory) / "urw-base35",
        )
        fonts_directory.mkdir(parents=True)
        fontconfig_directory.mkdir(parents=True)
        for system_font_directory in system_font_directories:
            system_font_directory.mkdir()
        executable.touch()
        source = (
            "<fontconfig>"
            '<dir prefix="cwd">fonts</dir>'
            "<alias><family>sans-serif</family><prefer><family>Arimo</family>"
            "</prefer></alias>"
            "</fontconfig>"
        )
        (fontconfig_directory / "fonts.conf").write_text(source, encoding="utf-8")
        options = {
            "executable_path": str(executable),
            "env": {"FONTCONFIG_PATH": "/broken/path"},
        }
        instance = browser.Browser()

        with patch.object(
            browser,
            "_LINUX_SYSTEM_FONT_DIRECTORIES",
            system_font_directories,
        ):
            instance._configure_runtime_fontconfig(options)

        runtime_path = Path(options["env"]["FONTCONFIG_FILE"])
        runtime_source = runtime_path.read_text(encoding="utf-8")
        assert f"<dir>{fonts_directory.resolve()}</dir>" in runtime_source
        for system_font_directory in system_font_directories:
            assert f"<dir>{system_font_directory.resolve()}</dir>" in runtime_source
        for source_family, target_family in browser._LINUX_CANVAS_FONT_ALIASES.items():
            assert f"<string>{source_family}</string>" in runtime_source
            assert f"<string>{target_family}</string>" in runtime_source
        assert "<family>Arimo</family>" in runtime_source
        assert "FONTCONFIG_PATH" not in options["env"]
        instance._fontconfig_directory.cleanup()


def test_runtime_fontconfig_rejects_missing_bundle_assets() -> None:
    instance = browser.Browser()
    try:
        instance._configure_runtime_fontconfig(
            {"executable_path": "/missing/camoufox-bin", "env": {}}
        )
    except browser.BrowserError as error:
        assert "bundled fonts directory is missing" in str(error)
    else:
        raise AssertionError("expected missing Camoufox bundle to be rejected")


def test_linux_font_families_match_the_production_image() -> None:
    config: dict[str, object] = {"fonts": ["Arimo", "Tinos"]}

    browser._prepare_linux_font_families(config)

    configured_fonts = set(config["fonts"])
    assert {"Arimo", "Tinos"}.issubset(configured_fonts)
    assert browser._LINUX_BROWSER_FONT_FAMILIES.issubset(configured_fonts)


def test_finalized_environment_removes_partial_canvas_noise() -> None:
    config: dict[str, object] = {
        "canvas:aaCapOffset": True,
        "canvas:aaOffset": 12,
        "webGl:supportedExtensions": ["WEBGL_compressed_texture_astc"],
        "webGl2:supportedExtensions": ["WEBGL_compressed_texture_etc"],
    }
    options = {
        "env": {
            "CAMOU_CONFIG_1": "stale",
            "CAMOU_CONFIG_2": "stale",
            "UNCHANGED": "value",
        }
    }
    encoded_environment = {
        "CAMOU_CONFIG_1": "final",
        "FONTCONFIG_PATH": "/camoufox/fontconfig/linux",
    }

    with patch(
        "camoufox.utils.get_env_vars",
        return_value=encoded_environment,
    ) as get_env_vars:
        browser._refresh_camoufox_environment(config, options)

    assert "canvas:aaCapOffset" not in config
    assert "canvas:aaOffset" not in config
    assert config["webGl:supportedExtensions"] == []
    assert config["webGl2:supportedExtensions"] == []
    assert options["env"] == {
        "CAMOU_CONFIG_1": "final",
        "FONTCONFIG_PATH": "/camoufox/fontconfig/linux",
        "UNCHANGED": "value",
    }
    get_env_vars.assert_called_once_with(config, "lin")


def main_test() -> None:
    test_non_object_persisted_config_is_rejected()
    test_known_persisted_webgl_identity_is_kept()
    test_unknown_persisted_webgl_identity_is_dropped()
    test_identity_without_webgl_is_left_for_camoufox()
    test_persisted_user_agent_follows_the_installed_firefox()
    test_stock_default_overrides_are_dropped()
    test_screen_follows_display_and_window_geometry_is_released()
    test_runtime_fontconfig_uses_absolute_bundled_font_path()
    test_runtime_fontconfig_rejects_missing_bundle_assets()
    test_linux_font_families_match_the_production_image()
    test_finalized_environment_removes_partial_canvas_noise()
    print(json.dumps({"result": "fingerprint config tests passed"}))


if __name__ == "__main__":
    main_test()
