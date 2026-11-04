"""Pinned CAPTCHA solver extensions and API-backed solving."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

CAPTCHA_SOLVER_CACHE_ROOT = Path("/userdata/.cache/captcha-solvers")

PROVIDER_NOPECHA = "nopecha"
PROVIDER_CAPSOLVER = "capsolver"
PROVIDER_CAPMONSTER = "capmonster"
PROVIDER_NOCAPTCHAAI = "nocaptchaai"
PROVIDER_YESCAPTCHA = "yescaptcha"
PROVIDER_2CAPTCHA = "2captcha"
PROVIDER_ANTICAPTCHA = "anticaptcha"

_API_KEY_MAX_BYTES = 4096
_ARCHIVE_DOWNLOAD_TIMEOUT_SECONDS = 60
_API_RESPONSE_MAX_BYTES = 1024 * 1024
_TASK_MAX_BYTES = 10 * 1024 * 1024
_EXTRACTED_MAX_BYTES = 30 * 1024 * 1024
_EXTRACTED_MAX_FILES = 2000
_MANIFEST_FILENAME = "manifest.json"
CAPTCHA_SOLVE_TIMEOUT_DEFAULT_SECONDS = 120.0
_SOLVE_TIMEOUT_MIN_SECONDS = 5.0
_SOLVE_TIMEOUT_MAX_SECONDS = 600.0
CAPTCHA_SOLVE_POLL_INTERVAL_DEFAULT_SECONDS = 5.0
_POLL_INTERVAL_MIN_SECONDS = 1.0
_POLL_INTERVAL_MAX_SECONDS = 30.0
_ANTICAPTCHA_CREATE_TASK_URL = "https://api.anti-captcha.com/createTask"
_ANTICAPTCHA_GET_TASK_RESULT_URL = "https://api.anti-captcha.com/getTaskResult"
_USER_AGENT = "stealthy-auto-browse-captcha-solver"


class CaptchaSolverError(Exception):
    """CAPTCHA solver configuration or operation failed."""


@dataclass(frozen=True)
class ExtensionSpec:
    """Pinned official Firefox extension artifact."""

    provider: str
    version: str
    url: str
    archive_bytes: int
    sha256: str
    extension_id: str
    configure: Callable[[Path, str | None], None] = field(repr=False)


def _replace_javascript_value(
    path: Path,
    pattern: str,
    value: str,
    description: str,
) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CaptchaSolverError(f"{description} could not be read") from error
    updated, count = re.subn(
        pattern,
        lambda match: f"{match.group(1)}{value}{match.group(2)}",
        source,
        count=1,
    )
    if count != 1:
        raise CaptchaSolverError(f"{description} has an unexpected format")
    try:
        path.write_text(updated, encoding="utf-8")
        path.chmod(0o600)
    except OSError as error:
        raise CaptchaSolverError(f"{description} could not be written") from error


def _configure_nopecha(root: Path, api_key: str | None) -> None:
    path = root / _MANIFEST_FILENAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        settings = manifest["nopecha"]
        settings["enabled"] = True
        settings["key"] = api_key or ""
        settings["keys"] = []
        gecko = manifest.setdefault("browser_specific_settings", {}).setdefault(
            "gecko", {}
        )
        gecko["id"] = _EXTENSION_SPECS[PROVIDER_NOPECHA].extension_id
        path.write_text(
            json.dumps(manifest, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except (KeyError, OSError, UnicodeError, ValueError, TypeError) as error:
        raise CaptchaSolverError("NopeCHA extension configuration failed") from error


def _configure_capsolver(root: Path, api_key: str | None) -> None:
    _replace_javascript_value(
        root / "assets" / "config.js",
        r"(?m)^(\s*apiKey:\s*)''(,\s*)$",
        json.dumps(api_key or ""),
        "CapSolver extension configuration",
    )


def _configure_capmonster(root: Path, api_key: str | None) -> None:
    path = root / "defaultSettings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings["isEnabled"] = True
        settings["clientKey"] = api_key or ""
        path.write_text(
            json.dumps(settings, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise CaptchaSolverError("CapMonster extension configuration failed") from error


def _configure_nocaptchaai(root: Path, api_key: str | None) -> None:
    path = root / "defaultConfig.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings["APIKEY"] = api_key or ""
        settings["enabled"] = True
        settings["extensionEnabled"] = "true"
        path.write_text(
            json.dumps(settings, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise CaptchaSolverError("NoCaptchaAI extension configuration failed") from error


def _configure_yescaptcha(root: Path, api_key: str | None) -> None:
    _replace_javascript_value(
        root / "config.js",
        r"(?m)^(\s*clientKey:\s*)''(,.*)$",
        json.dumps(api_key or ""),
        "YesCaptcha extension configuration",
    )


def _configure_2captcha(root: Path, api_key: str | None) -> None:
    path = root / "common" / "config.js"
    _replace_javascript_value(
        path,
        r"(?m)^(\s*apiKey:\s*)null(,\s*)$",
        json.dumps(api_key or ""),
        "2Captcha extension configuration",
    )
    try:
        source = path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r"(?m)^(\s*autoSolve[A-Za-z0-9_]+:\s*)false(,\s*)$",
            r"\g<1>true\g<2>",
            source,
        )
        if count < 1:
            raise CaptchaSolverError(
                "2Captcha extension auto-solve configuration has an unexpected format"
            )
        path.write_text(updated, encoding="utf-8")
        path.chmod(0o600)
    except (OSError, UnicodeError) as error:
        raise CaptchaSolverError("2Captcha extension configuration failed") from error


_EXTENSION_SPECS = {
    PROVIDER_NOPECHA: ExtensionSpec(
        provider=PROVIDER_NOPECHA,
        version="0.6.1",
        url=(
            "https://github.com/NopeCHALLC/nopecha-extension/releases/download/"
            "0.6.1/firefox_automation.zip"
        ),
        archive_bytes=663_352,
        sha256="5793fd7f95db5bb9ae4a7cc10c4df27a4c2189e666bd0e02fbb8bdbbcb5a8fde",
        extension_id="nopecha@stealthy-auto-browse.local",
        configure=_configure_nopecha,
    ),
    PROVIDER_CAPSOLVER: ExtensionSpec(
        provider=PROVIDER_CAPSOLVER,
        version="1.17.1",
        url=(
            "https://github.com/capsolver/capsolver-browser-extension/releases/download/"
            "v1.17.1/CapSolver.Browser.Extension-firefox-v1.17.1.zip"
        ),
        archive_bytes=1_930_698,
        sha256="7dfde02597e11fbb648ecbd23047999d11c5902cfeef901d95f2753cc71ffbd9",
        extension_id="support@capsolver.com",
        configure=_configure_capsolver,
    ),
    PROVIDER_CAPMONSTER: ExtensionSpec(
        provider=PROVIDER_CAPMONSTER,
        version="1.4.0",
        url=(
            "https://addons.mozilla.org/firefox/downloads/file/5074670/"
            "capmonster_cloud-1.4.0.xpi"
        ),
        archive_bytes=1_952_484,
        sha256="ed16576f17ecf8e7eda2fb1c8190adaceccbede68f6858408a6e095d746896cf",
        extension_id="capmonser-cloud-ext-fire@zennolab.com",
        configure=_configure_capmonster,
    ),
    PROVIDER_NOCAPTCHAAI: ExtensionSpec(
        provider=PROVIDER_NOCAPTCHAAI,
        version="1.4.8",
        url=(
            "https://addons.mozilla.org/firefox/downloads/file/5020019/"
            "nocaptcha_ai_captcha_solver-1.4.8.xpi"
        ),
        archive_bytes=649_496,
        sha256="1e6efc6738bc7523e00d515211ec911cd6a9872ab97371370b04f76162ca6908",
        extension_id="addon@nocaptchaai.com",
        configure=_configure_nocaptchaai,
    ),
    PROVIDER_YESCAPTCHA: ExtensionSpec(
        provider=PROVIDER_YESCAPTCHA,
        version="0.4.3",
        url=(
            "https://addons.mozilla.org/firefox/downloads/file/4846007/"
            "yescaptcha_assistant-0.4.3.xpi"
        ),
        archive_bytes=946_908,
        sha256="c5562070043d8d66fd23231d4ea4166aaf04f0aa1ee37a0aa565e56b6dc5b627",
        extension_id="{defcf59e-d157-44f0-b224-4b1f78f85e46}",
        configure=_configure_yescaptcha,
    ),
    PROVIDER_2CAPTCHA: ExtensionSpec(
        provider=PROVIDER_2CAPTCHA,
        version="3.7.1",
        url=(
            "https://addons.mozilla.org/firefox/downloads/file/4353807/"
            "2captcha_solver-3.7.1.xpi"
        ),
        archive_bytes=452_148,
        sha256="182d1e7225553d14d97a88e6b864562e97e9759509290a31df5d5f88797653c5",
        extension_id="{f8b523da-039a-46c6-8782-cb2cb84f2697}",
        configure=_configure_2captcha,
    ),
}

CAPTCHA_SOLVER_PROVIDERS = frozenset((*_EXTENSION_SPECS, PROVIDER_ANTICAPTCHA))


def _optional_api_key(value: str, source: str) -> str | None:
    key = value.strip()
    if not key:
        return None
    encoded = key.encode("utf-8")
    if len(encoded) > _API_KEY_MAX_BYTES:
        raise CaptchaSolverError(f"{source} is too large")
    if any(unicodedata.category(character) == "Cc" for character in key):
        raise CaptchaSolverError(f"{source} contains control characters")
    return key


def _api_key_from_file(path_value: str, source: str) -> str | None:
    path = Path(path_value)
    try:
        if not path.is_file():
            raise CaptchaSolverError(f"{source} is not a regular file")
        if path.stat().st_size > _API_KEY_MAX_BYTES:
            raise CaptchaSolverError(f"{source} is too large")
        value = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise CaptchaSolverError(f"{source} is not valid UTF-8") from error
    except OSError as error:
        raise CaptchaSolverError(f"{source} could not be read") from error
    return _optional_api_key(value, source)


def _optional_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise CaptchaSolverError(f"{name} must be true or false")


def _generic_environment_present() -> bool:
    return any(
        name in os.environ
        for name in (
            "CAPTCHA_SOLVER_ENABLED",
            "CAPTCHA_SOLVER_PROVIDER",
            "CAPTCHA_SOLVER_API_KEY",
            "CAPTCHA_SOLVER_API_KEY_FILE",
        )
    )


@dataclass(frozen=True)
class CaptchaSolverConfig:
    """Validated configuration for one optional CAPTCHA solver provider."""

    provider: str | None = None
    api_key: str | None = field(default=None, repr=False)
    cache_root: Path = CAPTCHA_SOLVER_CACHE_ROOT

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    @property
    def extension_spec(self) -> ExtensionSpec | None:
        if self.provider is None:
            return None
        return _EXTENSION_SPECS.get(self.provider)

    @classmethod
    def from_environment(cls) -> "CaptchaSolverConfig":
        """Load the optional CAPTCHA solver configuration."""
        if not _generic_environment_present():
            return cls()

        provider = os.environ.get("CAPTCHA_SOLVER_PROVIDER", "").strip().lower()
        enabled_setting = _optional_bool("CAPTCHA_SOLVER_ENABLED")
        direct_key = _optional_api_key(
            os.environ.get("CAPTCHA_SOLVER_API_KEY", ""),
            "CAPTCHA_SOLVER_API_KEY",
        )
        key_file = os.environ.get("CAPTCHA_SOLVER_API_KEY_FILE", "").strip()
        if direct_key and key_file:
            raise CaptchaSolverError(
                "CAPTCHA_SOLVER_API_KEY and CAPTCHA_SOLVER_API_KEY_FILE are mutually exclusive"
            )
        api_key = (
            _api_key_from_file(key_file, "CAPTCHA_SOLVER_API_KEY_FILE")
            if key_file
            else direct_key
        )
        if enabled_setting is False:
            if provider or api_key:
                raise CaptchaSolverError(
                    "CAPTCHA_SOLVER_ENABLED=false conflicts with a provider or API key"
                )
            return cls()
        if not provider:
            raise CaptchaSolverError("CAPTCHA_SOLVER_PROVIDER is required")
        if provider not in CAPTCHA_SOLVER_PROVIDERS:
            allowed = ", ".join(sorted(CAPTCHA_SOLVER_PROVIDERS))
            raise CaptchaSolverError(
                f"CAPTCHA_SOLVER_PROVIDER must be one of: {allowed}"
            )
        if provider != PROVIDER_NOPECHA and not api_key:
            raise CaptchaSolverError(
                f"CAPTCHA_SOLVER_API_KEY or CAPTCHA_SOLVER_API_KEY_FILE is required for {provider}"
            )
        return cls(provider=provider, api_key=api_key)

@dataclass
class PreparedCaptchaSolverAddon:
    """Ephemeral configured extension tree for one browser process."""

    directory: tempfile.TemporaryDirectory[str]
    path: Path
    provider: str
    extension_id: str

    def cleanup(self) -> None:
        self.directory.cleanup()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_archive(spec: ExtensionSpec, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        request = urllib.request.Request(
            spec.url,
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(
            request,
            timeout=_ARCHIVE_DOWNLOAD_TIMEOUT_SECONDS,
        ) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) != spec.archive_bytes:
                raise CaptchaSolverError(
                    f"{spec.provider} extension download size is invalid"
                )
            data = response.read(spec.archive_bytes + 1)
        if len(data) != spec.archive_bytes:
            raise CaptchaSolverError(
                f"{spec.provider} extension download size is invalid"
            )
        temporary.write_bytes(data)
        if _sha256(temporary) != spec.sha256:
            raise CaptchaSolverError(
                f"{spec.provider} extension checksum verification failed"
            )
        os.replace(temporary, destination)
    except CaptchaSolverError:
        raise
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise CaptchaSolverError(
            f"{spec.provider} extension download failed"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _safe_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > _EXTRACTED_MAX_FILES:
        raise CaptchaSolverError("extension archive contains too many files")
    total_bytes = 0
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise CaptchaSolverError("extension archive contains an unsafe path")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise CaptchaSolverError("extension archive contains a symbolic link")
        total_bytes += member.file_size
        if total_bytes > _EXTRACTED_MAX_BYTES:
            raise CaptchaSolverError("extension archive is too large")
    return members


def _manifest_extension_id(manifest: dict[str, Any]) -> str | None:
    browser_settings = manifest.get("browser_specific_settings")
    if isinstance(browser_settings, dict):
        gecko = browser_settings.get("gecko")
        if isinstance(gecko, dict) and isinstance(gecko.get("id"), str):
            return gecko["id"]
    applications = manifest.get("applications")
    if isinstance(applications, dict):
        gecko = applications.get("gecko")
        if isinstance(gecko, dict) and isinstance(gecko.get("id"), str):
            return gecko["id"]
    return None


def _validate_manifest(extension_path: Path, spec: ExtensionSpec) -> None:
    manifest_path = extension_path / _MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise CaptchaSolverError("extension manifest is invalid") from error
    if manifest.get("version") != spec.version:
        raise CaptchaSolverError(f"{spec.provider} extension version is invalid")
    if spec.provider != PROVIDER_NOPECHA:
        if _manifest_extension_id(manifest) != spec.extension_id:
            raise CaptchaSolverError(f"{spec.provider} extension ID is invalid")
    elif not isinstance(manifest.get("nopecha"), dict):
        raise CaptchaSolverError("NopeCHA extension has no automation settings")


def _extract_archive(
    archive_path: Path,
    destination: Path,
    spec: ExtensionSpec,
) -> None:
    staging = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True, mode=0o700)
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_archive_members(archive)
            archive.extractall(staging, members=members)
        _validate_manifest(staging, spec)
        os.replace(staging, destination)
    except CaptchaSolverError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise CaptchaSolverError("extension archive could not be extracted") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _cached_archive(config: CaptchaSolverConfig, spec: ExtensionSpec) -> Path:
    version_root = config.cache_root / spec.provider / spec.version
    version_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = version_root / ".lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        archive_path = version_root / "extension.zip"
        if (
            not archive_path.is_file()
            or archive_path.stat().st_size != spec.archive_bytes
            or _sha256(archive_path) != spec.sha256
        ):
            archive_path.unlink(missing_ok=True)
            _download_archive(spec, archive_path)
    return archive_path


def prepare_captcha_solver_addon(
    config: CaptchaSolverConfig,
) -> PreparedCaptchaSolverAddon | None:
    """Prepare one configured extension, or return none for disabled/API mode."""
    spec = config.extension_spec
    if spec is None:
        return None

    archive_path = _cached_archive(config, spec)
    directory = tempfile.TemporaryDirectory(prefix=f"stealthy-{spec.provider}-")
    runtime_path = Path(directory.name) / "extension"
    try:
        _extract_archive(archive_path, runtime_path, spec)
        spec.configure(runtime_path, config.api_key)
        _validate_manifest(runtime_path, spec)
        return PreparedCaptchaSolverAddon(
            directory=directory,
            path=runtime_path,
            provider=spec.provider,
            extension_id=spec.extension_id,
        )
    except (CaptchaSolverError, OSError, ValueError, TypeError) as error:
        directory.cleanup()
        if isinstance(error, CaptchaSolverError):
            raise
        raise CaptchaSolverError(
            f"{spec.provider} runtime extension could not be prepared"
        ) from error


def _contains_client_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() == "clientkey" or _contains_client_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_client_key(item) for item in value)
    return False


def _validate_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise CaptchaSolverError("task must be an object")
    task_type = task.get("type")
    if not isinstance(task_type, str) or not task_type.strip():
        raise CaptchaSolverError("task.type must be a non-empty string")
    if _contains_client_key(task):
        raise CaptchaSolverError("task must not contain clientKey")
    try:
        encoded = json.dumps(task, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CaptchaSolverError("task must contain only JSON values") from error
    if len(encoded) > _TASK_MAX_BYTES:
        raise CaptchaSolverError("task is too large")
    return task


def _bounded_seconds(
    value: Any,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool):
        raise CaptchaSolverError(f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise CaptchaSolverError(f"{name} must be a number") from error
    if parsed < minimum or parsed > maximum:
        raise CaptchaSolverError(
            f"{name} must be between {minimum:g} and {maximum:g} seconds"
        )
    return parsed


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_API_RESPONSE_MAX_BYTES + 1)
    except (OSError, urllib.error.URLError) as error:
        raise CaptchaSolverError("Anti-Captcha request failed") from error
    if len(raw) > _API_RESPONSE_MAX_BYTES:
        raise CaptchaSolverError("Anti-Captcha response is too large")
    try:
        result = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise CaptchaSolverError("Anti-Captcha returned invalid JSON") from error
    if not isinstance(result, dict):
        raise CaptchaSolverError("Anti-Captcha returned an invalid response")
    error_id = result.get("errorId", 0)
    if not isinstance(error_id, int):
        raise CaptchaSolverError("Anti-Captcha returned an invalid error identity")
    if error_id != 0:
        code = result.get("errorCode")
        description = result.get("errorDescription")
        detail = code if isinstance(code, str) and code else "provider error"
        if isinstance(description, str) and description:
            detail = f"{detail}: {description}"
        raise CaptchaSolverError(f"Anti-Captcha rejected the task: {detail}")
    return result


def solve_captcha_task(
    config: CaptchaSolverConfig,
    task: Any,
    timeout: Any = CAPTCHA_SOLVE_TIMEOUT_DEFAULT_SECONDS,
    poll_interval: Any = CAPTCHA_SOLVE_POLL_INTERVAL_DEFAULT_SECONDS,
) -> dict[str, Any]:
    """Create and poll one Anti-Captcha task through fixed API endpoints."""
    if config.provider != PROVIDER_ANTICAPTCHA or not config.api_key:
        raise CaptchaSolverError(
            "solve_captcha requires CAPTCHA_SOLVER_PROVIDER=anticaptcha"
        )
    validated_task = _validate_task(task)
    timeout_seconds = _bounded_seconds(
        timeout,
        "timeout",
        _SOLVE_TIMEOUT_MIN_SECONDS,
        _SOLVE_TIMEOUT_MAX_SECONDS,
    )
    poll_seconds = _bounded_seconds(
        poll_interval,
        "poll_interval",
        _POLL_INTERVAL_MIN_SECONDS,
        _POLL_INTERVAL_MAX_SECONDS,
    )
    deadline = time.monotonic() + timeout_seconds
    request_timeout = min(timeout_seconds, _ARCHIVE_DOWNLOAD_TIMEOUT_SECONDS)
    created = _post_json(
        _ANTICAPTCHA_CREATE_TASK_URL,
        {"clientKey": config.api_key, "task": validated_task},
        request_timeout,
    )
    task_id = created.get("taskId")
    if not isinstance(task_id, int) or task_id <= 0:
        raise CaptchaSolverError("Anti-Captcha returned no valid task ID")

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptchaSolverError("Anti-Captcha solve timed out")
        time.sleep(min(poll_seconds, remaining))
        result = _post_json(
            _ANTICAPTCHA_GET_TASK_RESULT_URL,
            {"clientKey": config.api_key, "taskId": task_id},
            min(max(remaining, 1.0), _ARCHIVE_DOWNLOAD_TIMEOUT_SECONDS),
        )
        status = result.get("status")
        if status == "processing":
            continue
        if status != "ready" or not isinstance(result.get("solution"), dict):
            raise CaptchaSolverError("Anti-Captcha returned an invalid task result")
        response = dict(result)
        response["provider"] = PROVIDER_ANTICAPTCHA
        response["taskId"] = task_id
        return response
