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


def test_webgl_cohort_is_filtered_and_persisted() -> None:
    config: dict[str, object] = {}
    sample_webgl = MagicMock(return_value=_webgl_cohort())

    with patch("camoufox.webgl.sample_webgl", sample_webgl):
        selected = browser._prepare_webgl_config(config)

    assert selected == ("Intel", "Intel renderer")
    assert config["webGl:supportedExtensions"] == ["ANGLE_instanced_arrays"]
    assert config["webGl2:supportedExtensions"] == ["ANGLE_instanced_arrays"]
    assert "webGl2Enabled" not in config
    sample_webgl.assert_called_once_with("lin")


def test_persisted_webgl_identity_selects_the_same_cohort() -> None:
    config: dict[str, object] = {
        "webGl:vendor": "Persisted vendor",
        "webGl:renderer": "Persisted renderer",
    }
    sample_webgl = MagicMock(return_value=_webgl_cohort("Persisted vendor"))

    with patch("camoufox.webgl.sample_webgl", sample_webgl):
        browser._prepare_webgl_config(config)

    sample_webgl.assert_called_once_with(
        "lin",
        "Persisted vendor",
        "Persisted renderer",
    )


def test_invalid_persisted_webgl_identity_falls_back() -> None:
    config: dict[str, object] = {
        "webGl:vendor": "Removed vendor",
        "webGl:renderer": "Removed renderer",
    }
    sample_webgl = MagicMock(
        side_effect=[ValueError("unknown cohort"), _webgl_cohort("Replacement")]
    )

    with patch("camoufox.webgl.sample_webgl", sample_webgl):
        selected = browser._prepare_webgl_config(config)

    assert selected == ("Replacement", "Replacement renderer")
    assert sample_webgl.call_args_list[1].args == ("lin",)


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
    test_webgl_cohort_is_filtered_and_persisted()
    test_persisted_webgl_identity_selects_the_same_cohort()
    test_invalid_persisted_webgl_identity_falls_back()
    test_runtime_fontconfig_uses_absolute_bundled_font_path()
    test_runtime_fontconfig_rejects_missing_bundle_assets()
    test_linux_font_families_match_the_production_image()
    test_finalized_environment_removes_partial_canvas_noise()
    print(json.dumps({"result": "fingerprint config tests passed"}))


if __name__ == "__main__":
    main_test()
