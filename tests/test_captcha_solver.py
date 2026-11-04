"""Unit tests for opt-in CAPTCHA solver providers."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/app")

import captcha_solver

FAKE_KEY = "EXAMPLE-DO-NOT-USE-quote'\\slash"


def _assert_error(environment: dict[str, str], expected: str) -> None:
    with patch.dict(os.environ, environment, clear=True):
        try:
            captcha_solver.CaptchaSolverConfig.from_environment()
        except captcha_solver.CaptchaSolverError as error:
            assert expected in str(error)
            assert FAKE_KEY not in str(error)
        else:
            raise AssertionError("expected invalid CAPTCHA solver configuration")


def test_environment_configuration() -> None:
    providers = sorted(captcha_solver.CAPTCHA_SOLVER_PROVIDERS)
    for provider in providers:
        environment = {"CAPTCHA_SOLVER_PROVIDER": provider}
        if provider != captcha_solver.PROVIDER_NOPECHA:
            environment["CAPTCHA_SOLVER_API_KEY"] = FAKE_KEY
        with patch.dict(os.environ, environment, clear=True):
            configured = captcha_solver.CaptchaSolverConfig.from_environment()
        assert configured.provider == provider
        assert FAKE_KEY not in repr(configured)

    with patch.dict(os.environ, {}, clear=True):
        assert not captcha_solver.CaptchaSolverConfig.from_environment().enabled

    with patch.dict(os.environ, {"NOPECHA_ENABLED": "true"}, clear=True):
        assert not captcha_solver.CaptchaSolverConfig.from_environment().enabled


def test_key_file_and_invalid_configuration() -> None:
    with tempfile.TemporaryDirectory() as directory:
        key_path = Path(directory) / "key"
        key_path.write_text(f"{FAKE_KEY}\n", encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "CAPTCHA_SOLVER_PROVIDER": "capsolver",
                "CAPTCHA_SOLVER_API_KEY_FILE": str(key_path),
            },
            clear=True,
        ):
            configured = captcha_solver.CaptchaSolverConfig.from_environment()
        assert configured.api_key == FAKE_KEY

    cases = [
        ({"CAPTCHA_SOLVER_ENABLED": "yes"}, "must be true or false"),
        ({"CAPTCHA_SOLVER_PROVIDER": "unknown"}, "must be one of"),
        ({"CAPTCHA_SOLVER_PROVIDER": "capsolver"}, "is required"),
        (
            {
                "CAPTCHA_SOLVER_PROVIDER": "capsolver",
                "CAPTCHA_SOLVER_API_KEY": FAKE_KEY,
                "CAPTCHA_SOLVER_API_KEY_FILE": "/unused",
            },
            "mutually exclusive",
        ),
        (
            {
                "CAPTCHA_SOLVER_ENABLED": "false",
                "CAPTCHA_SOLVER_PROVIDER": "nopecha",
            },
            "conflicts",
        ),
        (
            {
                "CAPTCHA_SOLVER_PROVIDER": "capsolver",
                "CAPTCHA_SOLVER_API_KEY": "bad\nkey",
            },
            "control characters",
        ),
    ]
    for environment, expected in cases:
        _assert_error(environment, expected)


def _write_manifest(root: Path, provider: str) -> None:
    spec = captcha_solver._EXTENSION_SPECS[provider]
    manifest: dict[str, object] = {
        "manifest_version": 2,
        "version": spec.version,
        "browser_specific_settings": {"gecko": {"id": spec.extension_id}},
    }
    if provider == captcha_solver.PROVIDER_NOPECHA:
        manifest["nopecha"] = {"enabled": False, "key": "", "keys": []}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _provider_tree(root: Path, provider: str) -> None:
    root.mkdir(parents=True)
    _write_manifest(root, provider)
    if provider == captcha_solver.PROVIDER_CAPSOLVER:
        (root / "assets").mkdir()
        (root / "assets" / "config.js").write_text("  apiKey: '',\n", encoding="utf-8")
    elif provider == captcha_solver.PROVIDER_CAPMONSTER:
        (root / "defaultSettings.json").write_text("{}", encoding="utf-8")
    elif provider == captcha_solver.PROVIDER_NOCAPTCHAAI:
        (root / "defaultConfig.json").write_text("{}", encoding="utf-8")
    elif provider == captcha_solver.PROVIDER_YESCAPTCHA:
        (root / "config.js").write_text("  clientKey: '',\n", encoding="utf-8")
    elif provider == captcha_solver.PROVIDER_2CAPTCHA:
        (root / "common").mkdir()
        (root / "common" / "config.js").write_text(
            "  apiKey: null,\n  autoSolveRecaptchaV2: false,\n",
            encoding="utf-8",
        )


def test_every_extension_configurer() -> None:
    for provider, spec in captcha_solver._EXTENSION_SPECS.items():
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "extension"
            _provider_tree(root, provider)
            spec.configure(root, FAKE_KEY)
            captcha_solver._validate_manifest(root, spec)
            combined = "".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*")
                if path.is_file()
            )
            assert json.dumps(FAKE_KEY)[1:-1] in combined
            if provider == captcha_solver.PROVIDER_2CAPTCHA:
                assert "autoSolveRecaptchaV2: true" in combined


def _archive(provider: str) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "extension"
        _provider_tree(root, provider)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for path in root.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        return output.getvalue()


def test_runtime_copy_does_not_mutate_cache() -> None:
    provider = captcha_solver.PROVIDER_CAPSOLVER
    original_spec = captcha_solver._EXTENSION_SPECS[provider]
    data = _archive(provider)
    with tempfile.TemporaryDirectory() as directory:
        cache_root = Path(directory)
        spec = captcha_solver.ExtensionSpec(
            provider=provider,
            version=original_spec.version,
            url=original_spec.url,
            archive_bytes=len(data),
            sha256=captcha_solver.hashlib.sha256(data).hexdigest(),
            extension_id=original_spec.extension_id,
            configure=original_spec.configure,
        )
        archive_path = cache_root / provider / spec.version / "extension.zip"
        archive_path.parent.mkdir(parents=True)
        archive_path.write_bytes(data)
        config = captcha_solver.CaptchaSolverConfig(
            provider=provider,
            api_key=FAKE_KEY,
            cache_root=cache_root,
        )
        with patch.dict(captcha_solver._EXTENSION_SPECS, {provider: spec}):
            prepared = captcha_solver.prepare_captcha_solver_addon(config)
        assert prepared is not None
        assert json.dumps(FAKE_KEY)[1:-1] in (
            prepared.path / "assets" / "config.js"
        ).read_text()
        with zipfile.ZipFile(archive_path) as archive:
            assert FAKE_KEY not in archive.read("assets/config.js").decode()
        runtime_root = Path(prepared.directory.name)
        prepared.cleanup()
        assert not runtime_root.exists()


def test_unsafe_archives_are_rejected() -> None:
    spec = captcha_solver._EXTENSION_SPECS[captcha_solver.PROVIDER_CAPSOLVER]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, configure in (
            ("traversal.zip", lambda info: setattr(info, "filename", "../escaped")),
            (
                "symlink.zip",
                lambda info: setattr(
                    info,
                    "external_attr",
                    (stat.S_IFLNK | 0o777) << 16,
                ),
            ),
        ):
            archive_path = root / name
            info = zipfile.ZipInfo("entry")
            configure(info)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(info, "bad")
            try:
                captcha_solver._extract_archive(archive_path, root / name, spec)
            except captcha_solver.CaptchaSolverError:
                pass
            else:
                raise AssertionError("expected unsafe archive rejection")


def test_anticaptcha_task_lifecycle() -> None:
    config = captcha_solver.CaptchaSolverConfig(
        provider=captcha_solver.PROVIDER_ANTICAPTCHA,
        api_key=FAKE_KEY,
    )
    responses = [
        {"errorId": 0, "taskId": 42},
        {"errorId": 0, "status": "processing"},
        {"errorId": 0, "status": "ready", "solution": {"token": "answer"}},
    ]
    calls: list[tuple[str, dict[str, object]]] = []

    def post(url: str, payload: dict[str, object], _timeout: float) -> dict:
        calls.append((url, payload))
        return responses.pop(0)

    with (
        patch.object(captcha_solver, "_post_json", side_effect=post),
        patch.object(captcha_solver.time, "sleep"),
    ):
        result = captcha_solver.solve_captcha_task(
            config,
            {"type": "RecaptchaV2TaskProxyless", "websiteURL": "https://example.com"},
            timeout=5,
            poll_interval=1,
        )
    assert result["provider"] == "anticaptcha"
    assert result["solution"] == {"token": "answer"}
    assert len(calls) == 3
    assert calls[0][1]["clientKey"] == FAKE_KEY
    assert FAKE_KEY not in json.dumps(result)


def test_anticaptcha_rejects_bad_boundaries() -> None:
    config = captcha_solver.CaptchaSolverConfig(
        provider=captcha_solver.PROVIDER_ANTICAPTCHA,
        api_key=FAKE_KEY,
    )
    cases = [
        (None, 5, 1, "task must be an object"),
        ({}, 5, 1, "task.type"),
        ({"type": "x", "nested": {"clientKey": "bad"}}, 5, 1, "clientKey"),
        ({"type": "x"}, True, 1, "timeout must be a number"),
        ({"type": "x"}, 4, 1, "timeout must be between"),
        ({"type": "x"}, 5, 31, "poll_interval must be between"),
    ]
    for task, timeout, poll_interval, expected in cases:
        try:
            captcha_solver.solve_captcha_task(config, task, timeout, poll_interval)
        except captcha_solver.CaptchaSolverError as error:
            assert expected in str(error)
            assert FAKE_KEY not in str(error)
        else:
            raise AssertionError("expected invalid Anti-Captcha request")


def main() -> None:
    test_environment_configuration()
    test_key_file_and_invalid_configuration()
    test_every_extension_configurer()
    test_runtime_copy_does_not_mutate_cache()
    test_unsafe_archives_are_rejected()
    test_anticaptcha_task_lifecycle()
    test_anticaptcha_rejects_bad_boundaries()
    print(json.dumps({"result": "CAPTCHA solver provider tests passed"}))


if __name__ == "__main__":
    main()
