"""Stealth browser module using Camoufox."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import mimetypes
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse as _urlparse

from logger import get_logger

log = get_logger(__name__)

try:
    from redis_sync import RedisSync
except ImportError:
    RedisSync = None  # type: ignore

# Path to JS files
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Default user data directory
DEFAULT_USER_DATA_DIR = "/userdata"

# Persisted browser properties file (stores Camoufox config, not raw fingerprint)
BROWSER_PROPS_FILE = Path(DEFAULT_USER_DATA_DIR) / "stealthy-auto-browse-props.json"

VIRTUAL_MEDIA_DIR = Path(os.environ.get("VIRTUAL_MEDIA_DIR", "/media"))
VIRTUAL_MEDIA_ORIGIN = "https://virtual-media.stealthy.invalid"
VIRTUAL_CAMERA_URL = f"{VIRTUAL_MEDIA_ORIGIN}/camera"
VIRTUAL_MICROPHONE_URL = f"{VIRTUAL_MEDIA_ORIGIN}/microphone"
VIRTUAL_MEDIA_STATE_URL = f"{VIRTUAL_MEDIA_ORIGIN}/state"
VIRTUAL_MEDIA_UPLOAD_MAX_BYTES_DEFAULT = 50 * 1024 * 1024
_VIRTUAL_MEDIA_CAMERA = "camera"
_VIRTUAL_MEDIA_MICROPHONE = "microphone"
_VIRTUAL_MEDIA_KINDS = frozenset(
    {_VIRTUAL_MEDIA_CAMERA, _VIRTUAL_MEDIA_MICROPHONE}
)
_VIRTUAL_MEDIA_ROUTE_KINDS = {
    "/camera": _VIRTUAL_MEDIA_CAMERA,
    "/microphone": _VIRTUAL_MEDIA_MICROPHONE,
}
_VIRTUAL_MEDIA_STREAM_KINDS = {
    _VIRTUAL_MEDIA_CAMERA: "video",
    _VIRTUAL_MEDIA_MICROPHONE: "audio",
}
_VIRTUAL_MEDIA_STREAM_SELECTORS = {
    _VIRTUAL_MEDIA_CAMERA: "v",
    _VIRTUAL_MEDIA_MICROPHONE: "a",
}
_VIRTUAL_MEDIA_PROBE_TIMEOUT_SECONDS = 10
_VIRTUAL_MEDIA_CHANGE_EVENT = "stealthyvirtualmediachange"

_VIRTUAL_MEDIA_INIT_SCRIPT = """
({ cameraUrl, microphoneUrl, dynamic, stateUrl, initialState, changeEvent }) => {
    const mediaDevices = navigator.mediaDevices;
    if (!mediaDevices || !mediaDevices.getUserMedia) {
        return;
    }

    const nativeGetUserMedia = mediaDevices.getUserMedia.bind(mediaDevices);
    const keepAlive = [];

    const wantsSource = value => value !== false && value !== undefined;
    const captureTrack = async (url, kind) => {
        const element = document.createElement(kind === "video" ? "video" : "audio");
        element.crossOrigin = "anonymous";
        element.src = url;
        element.autoplay = true;
        element.loop = true;
        element.muted = true;
        element.playsInline = true;
        element.style.cssText = "display:none!important";
        document.documentElement.appendChild(element);

        const capture = element.captureStream || element.mozCaptureStream;
        if (!capture) {
            throw new Error("Firefox does not support media-element stream capture");
        }
        const stream = capture.call(element);
        await element.play();

        const trackGetter = kind === "video" ? "getVideoTracks" : "getAudioTracks";
        for (let attempt = 0; attempt < 100; attempt += 1) {
            const track = stream[trackGetter]()[0];
            if (track) {
                keepAlive.push(element, stream);
                return track;
            }
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        throw new Error(`Virtual ${kind} source produced no track`);
    };

    const fetchVirtualMediaState = async () => {
        const response = await fetch(stateUrl, { cache: "no-store" });
        if (!response.ok) {
            throw new DOMException("Virtual media state is unavailable", "NotFoundError");
        }
        return response.json();
    };

    let latestVirtualMediaState = initialState;
    const refreshVirtualMediaState = async () => {
        latestVirtualMediaState = await fetchVirtualMediaState();
        return latestVirtualMediaState;
    };
    window.addEventListener(changeEvent, event => {
        latestVirtualMediaState = event.detail;
    });
    void refreshVirtualMediaState().catch(error => {
        console.warn("Virtual media initial state refresh failed", error.name);
    });

    const createDynamicVideoTrack = async () => {
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const drawingContext = canvas.getContext("2d");
        const element = document.createElement("video");
        element.crossOrigin = "anonymous";
        element.autoplay = true;
        element.loop = true;
        element.muted = true;
        element.playsInline = true;
        element.style.cssText = "display:none!important";
        document.documentElement.appendChild(element);

        const stream = canvas.captureStream(30);
        const track = stream.getVideoTracks()[0];
        if (!track) {
            throw new Error("Virtual camera canvas produced no track");
        }
        drawingContext.fillStyle = "black";
        drawingContext.fillRect(0, 0, canvas.width, canvas.height);

        let lastRevision = -1;
        const draw = () => {
            if (element.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
                const width = element.videoWidth || 1;
                const height = element.videoHeight || 1;
                if (canvas.width !== width || canvas.height !== height) {
                    canvas.width = width;
                    canvas.height = height;
                }
                drawingContext.drawImage(element, 0, 0, canvas.width, canvas.height);
            }
            requestAnimationFrame(draw);
        };
        draw();

        const selectRevision = state => {
            if (!state.camera) {
                throw new DOMException("No virtual camera source is configured", "NotFoundError");
            }
            if (state.revision === lastRevision) {
                return;
            }
            lastRevision = state.revision;
            element.src = `${cameraUrl}?revision=${encodeURIComponent(state.revision)}`;
            element.load();
            element.play().catch(error => {
                console.warn("Virtual camera playback failed", error.name);
            });
        };
        selectRevision(latestVirtualMediaState);
        window.addEventListener(changeEvent, event => {
            try {
                selectRevision(event.detail);
            } catch (error) {
                console.warn("Virtual camera source update failed", error.name);
            }
        });
        setInterval(async () => {
            try {
                selectRevision(await refreshVirtualMediaState());
            } catch (error) {
                console.warn("Virtual camera source poll failed", error.name);
            }
        }, 250);
        keepAlive.push(canvas, element, stream);
        return track;
    };

    const createDynamicAudioTrack = async () => {
        let lastRevision = -1;
        let activeSource = null;
        let sender;
        const createSource = async state => {
            if (!state.microphone) {
                throw new DOMException("No virtual microphone source is configured", "NotFoundError");
            }
            const element = document.createElement("audio");
            element.crossOrigin = "anonymous";
            element.autoplay = true;
            element.loop = true;
            element.style.cssText = "display:none!important";
            document.documentElement.appendChild(element);
            element.src = `${microphoneUrl}?revision=${encodeURIComponent(state.revision)}`;
            element.load();
            element.play().catch(error => {
                console.warn("Virtual microphone playback failed", error.name);
            });

            try {
                const capture = element.captureStream || element.mozCaptureStream;
                if (!capture) {
                    throw new Error("Firefox does not support media-element stream capture");
                }
                const stream = capture.call(element);
                for (let attempt = 0; attempt < 100; attempt += 1) {
                    const track = stream.getAudioTracks()[0];
                    if (track) {
                        return { element, stream, track };
                    }
                    await new Promise(resolve => setTimeout(resolve, 50));
                }
                throw new Error("Virtual microphone source produced no track");
            } catch (error) {
                element.remove();
                throw error;
            }
        };
        const initialSource = await createSource(latestVirtualMediaState);
        const senderConnection = new RTCPeerConnection();
        const receiverConnection = new RTCPeerConnection();
        const receivedTrack = new Promise(resolve => {
            receiverConnection.addEventListener("track", event => {
                resolve(event.track);
            }, { once: true });
        });
        senderConnection.addEventListener("icecandidate", event => {
            if (event.candidate) {
                receiverConnection.addIceCandidate(event.candidate).catch(error => {
                    console.warn("Virtual microphone receiver candidate failed", error.name);
                });
            }
        });
        receiverConnection.addEventListener("icecandidate", event => {
            if (event.candidate) {
                senderConnection.addIceCandidate(event.candidate).catch(error => {
                    console.warn("Virtual microphone sender candidate failed", error.name);
                });
            }
        });
        sender = senderConnection.addTrack(initialSource.track, initialSource.stream);
        const offer = await senderConnection.createOffer();
        await senderConnection.setLocalDescription(offer);
        await receiverConnection.setRemoteDescription(offer);
        const answer = await receiverConnection.createAnswer();
        await receiverConnection.setLocalDescription(answer);
        await senderConnection.setRemoteDescription(answer);
        const track = await receivedTrack;
        if (!(track instanceof MediaStreamTrack)) {
            throw new Error("Virtual microphone loopback produced no track");
        }
        activeSource = initialSource;
        lastRevision = latestVirtualMediaState.revision;
        const selectRevision = async state => {
            if (state.revision === lastRevision) {
                return;
            }
            const nextSource = await createSource(state);
            await sender.replaceTrack(nextSource.track);
            activeSource.element.pause();
            activeSource = nextSource;
            lastRevision = state.revision;
            keepAlive.push(nextSource.element, nextSource.stream);
        };
        window.addEventListener(changeEvent, event => {
            void selectRevision(event.detail).catch(error => {
                console.warn("Virtual microphone source update failed", error.name);
            });
        });
        setInterval(async () => {
            try {
                await selectRevision(await refreshVirtualMediaState());
            } catch (error) {
                console.warn("Virtual microphone source poll failed", error.name);
            }
        }, 250);
        keepAlive.push(
            initialSource.element,
            initialSource.stream,
            senderConnection,
            receiverConnection,
        );
        return track;
    };

    Object.defineProperty(mediaDevices, "getUserMedia", {
        configurable: true,
        value: async constraints => {
            const requested = constraints || {};
            const useCamera = wantsSource(requested.video);
            const useMicrophone = wantsSource(requested.audio);

            if (!useCamera && !useMicrophone) {
                return nativeGetUserMedia(requested);
            }

            if (useCamera && !cameraUrl) {
                throw new DOMException("No virtual camera source is configured", "NotFoundError");
            }
            if (useMicrophone && !microphoneUrl) {
                throw new DOMException("No virtual microphone source is configured", "NotFoundError");
            }

            if (dynamic) {
                if (useCamera && !latestVirtualMediaState.camera) {
                    throw new DOMException("No virtual camera source is configured", "NotFoundError");
                }
                if (useMicrophone && !latestVirtualMediaState.microphone) {
                    throw new DOMException("No virtual microphone source is configured", "NotFoundError");
                }
            }

            const trackRequests = [];
            if (useCamera) {
                trackRequests.push(
                    dynamic ? createDynamicVideoTrack() : captureTrack(cameraUrl, "video"),
                );
            }
            if (useMicrophone) {
                trackRequests.push(
                    dynamic ? createDynamicAudioTrack() : captureTrack(microphoneUrl, "audio"),
                );
            }

            return new MediaStream(await Promise.all(trackRequests));
        },
    });
}
"""


def _virtual_media_directory() -> Path:
    """Resolve the configured virtual-media root directory."""
    try:
        media_dir = VIRTUAL_MEDIA_DIR.resolve(strict=True)
    except OSError as error:
        raise BrowserError(f"VIRTUAL_MEDIA_DIR could not be resolved: {error}") from error
    if not media_dir.is_dir():
        raise BrowserError("VIRTUAL_MEDIA_DIR must name a directory")
    return media_dir


def _resolve_virtual_media_source(
    source_name: str,
    media_dir: Path | None = None,
) -> Path:
    """Resolve a relative, regular media file without allowing root escape."""
    if not isinstance(source_name, str) or not source_name or source_name != source_name.strip():
        raise BrowserError("virtual media source must be a non-empty relative path")

    requested = Path(source_name)
    if requested.is_absolute() or any(part in {"", ".", ".."} for part in requested.parts):
        raise BrowserError("virtual media source must be a safe relative path")

    root = media_dir or _virtual_media_directory()
    try:
        resolved = (root / requested).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BrowserError("virtual media source could not be resolved") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise BrowserError("virtual media source must be a regular file inside VIRTUAL_MEDIA_DIR")
    return resolved


def _virtual_media_file(variable_name: str) -> Path | None:
    """Resolve one configured virtual-media file within VIRTUAL_MEDIA_DIR."""
    raw_path = os.environ.get(variable_name, "").strip()
    if not raw_path:
        return None

    requested = Path(raw_path)
    if not requested.is_absolute():
        return _resolve_virtual_media_source(raw_path)

    media_dir = _virtual_media_directory()
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise BrowserError(f"{variable_name} could not be resolved: {error}") from error
    if not resolved.is_relative_to(media_dir) or not resolved.is_file():
        raise BrowserError(f"{variable_name} must name a regular file inside {media_dir}")
    return resolved


def _virtual_media_enabled() -> bool:
    """Parse the opt-in dynamic media switch at startup."""
    value = os.environ.get("VIRTUAL_MEDIA_DYNAMIC", "false").strip().lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no", ""}:
        return False
    raise BrowserError("VIRTUAL_MEDIA_DYNAMIC must be true or false")


def _virtual_media_upload_max_bytes() -> int:
    """Parse the bounded dynamic upload size at startup."""
    raw_value = os.environ.get(
        "VIRTUAL_MEDIA_UPLOAD_MAX_BYTES", str(VIRTUAL_MEDIA_UPLOAD_MAX_BYTES_DEFAULT)
    ).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise BrowserError("VIRTUAL_MEDIA_UPLOAD_MAX_BYTES must be a positive integer") from error
    if value < 1:
        raise BrowserError("VIRTUAL_MEDIA_UPLOAD_MAX_BYTES must be a positive integer")
    return value


def _get_default_viewport() -> tuple[int, int]:
    """Get default viewport size from XVFB_RESOLUTION env var (WxH format)."""
    xvfb_res = os.environ.get("XVFB_RESOLUTION", "1920x1080")
    parts = xvfb_res.split("x")
    width = int(parts[0]) if parts else 1920
    height = int(parts[1]) if len(parts) > 1 else 1080
    return width, height


def _load_persisted_config() -> dict[str, Any] | None:
    """Load persisted Camoufox config from file if exists."""
    if not BROWSER_PROPS_FILE.exists():
        return None

    try:
        with open(BROWSER_PROPS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_config(config: dict[str, Any]) -> None:
    """Save Camoufox config to file for persistence."""
    BROWSER_PROPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BROWSER_PROPS_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _update_config_screen(config: dict[str, Any], width: int, height: int) -> None:
    """Update screen/window dims in config to match current resolution."""
    config["screen.width"] = width
    config["screen.height"] = height
    config["screen.availWidth"] = width
    config["screen.availHeight"] = height
    config["window.outerWidth"] = width
    config["window.outerHeight"] = height
    config["window.innerWidth"] = width
    config["window.innerHeight"] = height - 80  # Account for browser chrome
    config["window.screenX"] = 0
    config["window.screenY"] = 0
    config["screen.availLeft"] = 0
    config["screen.availTop"] = 0


def _generate_camoufox_config(screen_width: int, screen_height: int) -> dict[str, Any]:
    """Generate a Camoufox config with realistic Linux Firefox fingerprint."""
    from browserforge.fingerprints import FingerprintGenerator
    from camoufox.fingerprints import from_browserforge
    from camoufox.pkgman import installed_verstr

    # Generate without screen constraints so browserforge succeeds for any resolution.
    # We override fp.screen.* values below to match our actual Xvfb display.
    fp_gen = FingerprintGenerator(browser="firefox", os="linux")
    fp = fp_gen.generate()

    # Adjust screen/window to match our actual display
    fp.screen.width = screen_width
    fp.screen.height = screen_height
    fp.screen.availWidth = screen_width
    fp.screen.availHeight = screen_height
    fp.screen.outerWidth = screen_width
    fp.screen.outerHeight = screen_height
    fp.screen.innerWidth = screen_width
    fp.screen.innerHeight = screen_height - 80  # Account for browser chrome
    fp.screen.screenX = 0
    fp.screen.availTop = 0
    fp.screen.availLeft = 0

    # Convert to Camoufox config format
    ff_version = installed_verstr().split(".", 1)[0]
    return from_browserforge(fp, ff_version)


def _log_browser_postmortem() -> None:
    """Capture crash diagnostics at the moment the health probe fails.

    Best-effort. Each probe is wrapped so a failure to gather one signal
    doesn't suppress the others. Output goes to log.warning so the operator
    sees it even when running with default log level. Most common cause is
    OOM (heavy site like Facebook + persistent profile accumulation); less
    common are renderer segfaults and external SIGKILL (docker oom-killer,
    `docker stop`, `kill -9`).
    """
    # 1. Process inventory — are Camoufox / Firefox processes still alive?
    try:
        proc = subprocess.run(
            ["pgrep", "-af", "camoufox-bin"],
            capture_output=True, text=True, timeout=2,
        )
        alive = proc.stdout.strip() or "<none>"
        log.warning("postmortem: camoufox-bin processes: %s", alive)
    except Exception as e:
        log.warning("postmortem: pgrep failed: %s", e)

    # 2. dmesg — does the kernel report an OOM kill of Camoufox? Requires
    # the container to be able to read kernel logs (most envs allow it
    # read-only). If `dmesg` is missing or unprivileged, skip silently —
    # this is a hint, not a guarantee.
    try:
        proc = subprocess.run(
            ["dmesg", "--ctime"],
            capture_output=True, text=True, timeout=2,
        )
        oom_lines = [
            line for line in proc.stdout.splitlines()[-200:]
            if "killed process" in line.lower()
            or "out of memory" in line.lower()
            or "camoufox" in line.lower()
            or "firefox" in line.lower()
        ]
        if oom_lines:
            log.warning(
                "postmortem: dmesg recent OOM / camoufox lines:\n  %s",
                "\n  ".join(oom_lines[-5:]),
            )
        else:
            log.warning("postmortem: no recent OOM / camoufox lines in dmesg")
    except FileNotFoundError:
        log.warning("postmortem: dmesg not available in container")
    except Exception as e:
        log.warning("postmortem: dmesg failed: %s", e)

    # 3. Memory + load snapshot — how tight was it at crash time?
    try:
        with open("/proc/meminfo") as f:
            meminfo = f.read()
        wanted_keys = ("MemTotal:", "MemAvailable:", "MemFree:", "SwapFree:")
        summary = [
            line for line in meminfo.splitlines()
            if any(line.startswith(k) for k in wanted_keys)
        ]
        log.warning("postmortem: memory: %s", " | ".join(summary))
    except Exception as e:
        log.warning("postmortem: meminfo read failed: %s", e)

    try:
        with open("/proc/loadavg") as f:
            log.warning("postmortem: loadavg: %s", f.read().strip())
    except Exception as e:
        log.warning("postmortem: loadavg read failed: %s", e)


class BrowserError(Exception):
    """Browser error."""


@dataclass
class BrowserState:
    """Current browser state."""

    url: str = ""
    title: str = ""
    content: str = ""
    screenshot_b64: str = ""


@dataclass
class BrowserConfig:
    """Browser configuration."""

    timeout: float = 30.0
    virtual_camera_file: Path | None = None
    virtual_microphone_file: Path | None = None
    virtual_media_dynamic: bool = False
    virtual_media_upload_max_bytes: int = VIRTUAL_MEDIA_UPLOAD_MAX_BYTES_DEFAULT

    @classmethod
    def from_environment(cls) -> "BrowserConfig":
        """Load browser configuration from validated environment variables."""
        virtual_media_dynamic = _virtual_media_enabled()
        return cls(
            virtual_camera_file=_virtual_media_file("VIRTUAL_CAMERA_FILE"),
            virtual_microphone_file=_virtual_media_file("VIRTUAL_MICROPHONE_FILE"),
            virtual_media_dynamic=virtual_media_dynamic,
            virtual_media_upload_max_bytes=_virtual_media_upload_max_bytes(),
        )


class Browser:
    """Stealth browser using Camoufox (Firefox-based, no CDP)."""

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._state = BrowserState()
        self._redis_sync = RedisSync() if RedisSync else None
        self._virtual_media_sources: dict[str, Path | None] = {
            _VIRTUAL_MEDIA_CAMERA: self.config.virtual_camera_file,
            _VIRTUAL_MEDIA_MICROPHONE: self.config.virtual_microphone_file,
        }
        self._virtual_media_revision = 0

    @property
    def state(self) -> BrowserState:
        """Current browser state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if browser is running (process started; not a health probe)."""
        return self._browser is not None

    @property
    def page(self) -> Any:
        """Current page object for direct access."""
        return self._page

    def virtual_media_state(self) -> dict[str, Any]:
        """Return active virtual-media flags and dynamic source metadata."""
        return {
            _VIRTUAL_MEDIA_CAMERA: (
                self._virtual_media_sources[_VIRTUAL_MEDIA_CAMERA] is not None
            ),
            _VIRTUAL_MEDIA_MICROPHONE: (
                self._virtual_media_sources[_VIRTUAL_MEDIA_MICROPHONE] is not None
            ),
            "dynamic": self.config.virtual_media_dynamic,
            "revision": self._virtual_media_revision,
            "sources": {
                kind: source.name if source else None
                for kind, source in self._virtual_media_sources.items()
            },
        }

    async def notify_virtual_media_source_change(self) -> None:
        """Push a source revision to open pages without exposing file paths."""
        if self._context is None:
            return
        payload = {
            "eventName": _VIRTUAL_MEDIA_CHANGE_EVENT,
            "state": self.virtual_media_state(),
        }
        for page in self._context.pages:
            try:
                await page.evaluate(
                    """payload => {
                        window.dispatchEvent(
                            new CustomEvent(payload.eventName, { detail: payload.state }),
                        );
                    }""",
                    payload,
                )
            except Exception as error:
                log.warning(
                    "virtual media source update notification failed",
                    extra={"reason": "page_notification_failed"},
                    exc_info=error,
                )

    def set_virtual_media_source(
        self,
        kind: str,
        source_name: str,
    ) -> dict[str, Any]:
        """Select an approved file source without replacing active page tracks."""
        self._require_dynamic_virtual_media()
        normalized_kind = self._virtual_media_kind(kind)
        source = _resolve_virtual_media_source(source_name)
        self._virtual_media_sources[normalized_kind] = source
        self._virtual_media_revision += 1
        log.info(
            "virtual media source selected",
            extra={"kind": normalized_kind, "revision": self._virtual_media_revision},
        )
        return self.virtual_media_state()

    def upload_virtual_media(
        self,
        kind: str,
        filename: str,
        content_b64: str,
        activate: bool = False,
    ) -> dict[str, Any]:
        """Store bounded base64 media under the configured root and optionally select it."""
        self._require_dynamic_virtual_media()
        normalized_kind = self._virtual_media_kind(kind)
        media_dir = _virtual_media_directory()
        if not os.access(media_dir, os.W_OK):
            raise BrowserError("VIRTUAL_MEDIA_DIR must be writable to upload media")
        destination = self._virtual_media_upload_destination(
            normalized_kind,
            filename,
            media_dir,
        )
        content = self._decode_virtual_media_upload(content_b64)
        self._write_virtual_media_upload(
            destination,
            content,
            normalized_kind,
        )

        result: dict[str, Any] = {"filename": destination.name}
        if activate:
            self._virtual_media_sources[normalized_kind] = destination
            self._virtual_media_revision += 1
            result["state"] = self.virtual_media_state()
        log.info(
            "virtual media uploaded",
            extra={"kind": normalized_kind, "activated": activate},
        )
        return result

    def _require_dynamic_virtual_media(self) -> None:
        """Reject runtime source control when the startup opt-in is disabled."""
        if not self.config.virtual_media_dynamic:
            raise BrowserError("dynamic virtual media is disabled")

    @staticmethod
    def _virtual_media_kind(kind: str) -> str:
        """Validate the finite virtual-media source kind enum."""
        if kind not in _VIRTUAL_MEDIA_KINDS:
            raise BrowserError(
                "virtual media kind must be camera or microphone"
            )
        return kind

    @staticmethod
    def _virtual_media_upload_destination(
        kind: str,
        filename: str,
        media_dir: Path,
    ) -> Path:
        """Derive a unique contained target from a safe, typed media filename."""
        if not isinstance(filename, str) or not filename or filename != filename.strip():
            raise BrowserError("virtual media filename must be a non-empty basename")

        requested = Path(filename)
        if requested.name != filename or filename in {".", ".."}:
            raise BrowserError("virtual media filename must be a safe basename")

        suffix = requested.suffix.lower()
        if not suffix:
            raise BrowserError("virtual media filename must include a media extension")

        stream_kind = _VIRTUAL_MEDIA_STREAM_KINDS[kind]
        declared_mime, _ = mimetypes.guess_type(requested.name)
        if not declared_mime or not declared_mime.startswith(f"{stream_kind}/"):
            raise BrowserError(
                f"virtual media filename must declare a {stream_kind} media type"
            )

        destination = media_dir / f"{uuid.uuid4().hex}{suffix}"
        resolved_destination = destination.resolve(strict=False)
        if not resolved_destination.is_relative_to(media_dir):
            raise BrowserError("virtual media filename must remain inside VIRTUAL_MEDIA_DIR")
        return resolved_destination

    def _decode_virtual_media_upload(self, content_b64: str) -> bytes:
        """Strictly decode a bounded base64 upload before writing it to disk."""
        if not isinstance(content_b64, str):
            raise BrowserError("virtual media content must be a base64 string")

        maximum_encoded_length = (
            (self.config.virtual_media_upload_max_bytes + 2) // 3
        ) * 4
        if len(content_b64) > maximum_encoded_length:
            raise BrowserError("virtual media upload exceeds configured size limit")

        try:
            content = base64.b64decode(content_b64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise BrowserError("virtual media content must be strict base64") from error
        if len(content) > self.config.virtual_media_upload_max_bytes:
            raise BrowserError("virtual media upload exceeds configured size limit")
        return content

    @staticmethod
    def _validate_virtual_media_upload(kind: str, temporary_path: Path) -> None:
        """Require uploaded bytes to contain the requested media stream kind."""
        stream_kind = _VIRTUAL_MEDIA_STREAM_KINDS[kind]
        stream_selector = _VIRTUAL_MEDIA_STREAM_SELECTORS[kind]
        try:
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    f"{stream_selector}:0",
                    "-show_entries",
                    "stream=codec_type",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(temporary_path),
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=_VIRTUAL_MEDIA_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BrowserError("virtual media upload validation failed") from error
        if probe.returncode != 0 or probe.stdout.strip() != stream_kind:
            raise BrowserError(
                f"virtual media upload must contain a {stream_kind} stream"
            )

    @classmethod
    def _write_virtual_media_upload(
        cls,
        destination: Path,
        content: bytes,
        kind: str,
    ) -> None:
        """Validate then atomically store media at a private, contained target."""
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=".virtual-media-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                os.fchmod(temporary_file.fileno(), 0o600)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            cls._validate_virtual_media_upload(kind, temporary_path)
            os.replace(temporary_path, destination)
            _resolve_virtual_media_source(destination.name, destination.parent)
        except OSError as error:
            raise BrowserError("virtual media upload could not be stored") from error
        finally:
            if temporary_path and temporary_path.exists():
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    log.warning(
                        "virtual media temporary upload cleanup failed",
                        extra={"reason": "temporary_upload_cleanup"},
                    )

    async def is_healthy(self) -> bool:
        """Probe whether the browser context + page are still usable.

        Camoufox/Firefox can die mid-session (OOM, segfault, killed) while
        the Python app stays alive. Playwright leaves the dead Page object
        in self._page and every subsequent call fails with
        "Connection closed while reading from the driver" or
        "Target page, context or browser has been closed".

        `self._context.pages` is just a cached Python list — it returns
        without round-tripping to Firefox, so it can't tell us the process
        died. We do an actual RPC (`context.cookies()`) to force Playwright
        to talk to the driver. If the driver is gone, this raises.

        Returns False if anything in the chain is dead so the caller can
        relaunch.
        """
        if self._context is None or self._browser is None:
            return False
        try:
            # Round-trip to the driver. Cheap, side-effect-free, fails fast
            # if Firefox is dead with "Connection closed" / "Target ... closed".
            await self._context.cookies()
        except Exception as e:
            log.warning("browser: health probe failed: %s", e)
            return False
        if self._page is not None:
            try:
                if self._page.is_closed():
                    # Page is dead but context may still be fine — caller
                    # should pick another page from the pool.
                    self._page = None
            except Exception:
                return False
        return True

    async def ensure_healthy(self) -> bool:
        """If the browser died, tear down + relaunch.

        Returns True if the browser is healthy (or was successfully
        relaunched), False if relaunch failed. Logs every recovery attempt
        so failed runs aren't silent.
        """
        if await self.is_healthy():
            return True
        log.warning(
            "browser: unhealthy state detected — tearing down and relaunching",
        )
        # Capture as much post-mortem context as we can BEFORE tearing down.
        # Crashes are usually OOM (Facebook etc. + accumulated persistent
        # profile state), but they can also be Firefox segfaults / asserts.
        _log_browser_postmortem()
        try:
            await self.stop()
        except Exception as e:
            log.warning("browser: stop during recovery failed: %s", e)
        try:
            await self.start()
        except Exception as e:
            log.error("browser: relaunch failed: %s", e)
            return False
        log.info("browser: relaunched successfully")
        return True

    def _on_page_crash(self, page: Any) -> None:
        """Playwright fires this when a page's renderer process crashes.

        Logged at WARNING so it shows up in operational logs even if the
        recovery path successfully relaunches afterwards. Tells the
        operator WHICH page died (URL) which the health probe alone can't.
        """
        url = ""
        try:
            url = page.url
        except Exception:
            pass
        log.warning("browser: page crashed url=%s", url)

    def _on_context_close(self) -> None:
        """Playwright fires this when the browser context is gone — Camoufox
        process exited (clean or crashed). Logged so the death event is
        visible in ops logs rather than only surfacing as the next request's
        'Connection closed' error."""
        log.warning("browser: context closed — Camoufox process exited")

    async def start(self) -> None:
        """Start browser."""
        if self.is_running:
            return

        if self._redis_sync:
            await self._redis_sync.start()
        await self._launch_browser()
        if self._redis_sync and self._context:
            await self._redis_sync.set_context(self._context)

    async def stop(self) -> None:
        """Stop browser and cleanup."""
        if self._redis_sync:
            self._redis_sync.clear_context()

        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        if self._redis_sync:
            await self._redis_sync.stop()

        self._state = BrowserState()

    async def goto(self, url: str, wait_until: str = "networkidle") -> BrowserState:
        """Navigate to URL and update state."""
        if not self.is_running:
            await self.start()

        page = await self._get_page()
        timeout_ms = int(self.config.timeout * 1000)

        await page.goto(url, timeout=timeout_ms, wait_until=wait_until)
        await self._update_state()
        return self._state

    async def screenshot(self, full_page: bool = False, quality: int = 80) -> str:
        """Take screenshot, return base64 string."""
        if not self._page:
            return ""

        try:
            data = await self._page.screenshot(
                type="jpeg",
                quality=quality,
                full_page=full_page,
            )
            b64 = base64.b64encode(data).decode()
            self._state.screenshot_b64 = b64
            return b64
        except Exception:
            return ""

    async def refresh(self) -> BrowserState:
        """Refresh current page."""
        if not self._page:
            return self._state

        await self._page.reload()
        await self._update_state()
        return self._state

    async def back(self) -> BrowserState:
        """Go back."""
        if not self._page:
            return self._state

        await self._page.go_back()
        await self._update_state()
        return self._state

    async def forward(self) -> BrowserState:
        """Go forward."""
        if not self._page:
            return self._state

        await self._page.go_forward()
        await self._update_state()
        return self._state

    async def click(self, selector: str) -> None:
        """Click element."""
        if not self._page:
            return

        await self._page.click(selector)
        await self._update_state()

    async def fill(self, selector: str, value: str) -> None:
        """Fill input field."""
        if not self._page:
            return

        await self._page.fill(selector, value)

    async def type(self, selector: str, text: str, delay: float = 0.05) -> None:
        """Type text with delay between keystrokes."""
        if not self._page:
            return

        await self._page.type(selector, text, delay=int(delay * 1000))

    async def wait_for(self, selector: str, state: str = "visible") -> None:
        """Wait for element."""
        if not self._page:
            return

        timeout_ms = int(self.config.timeout * 1000)
        await self._page.wait_for_selector(selector, state=state, timeout=timeout_ms)

    async def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript."""
        if not self._page:
            return None

        return await self._page.evaluate(expression)

    async def get_interactive_elements(self, visible_only: bool = True) -> list[dict]:
        """Get all interactive elements on the page.

        Returns list of element dicts with keys:
            i: index
            tag: HTML tag name
            id: element ID or None
            text: visible text content (truncated to 60 chars)
            selector: CSS selector or XPath
            x, y: center coordinates
            w, h: dimensions
            visible: whether in viewport
        """
        if not self._page:
            return []

        js_path = os.path.join(_SCRIPT_DIR, "get_elements.js")
        with open(js_path) as f:
            js_code = f.read()

        return await self._page.evaluate(js_code, visible_only)

    async def _launch_browser(self) -> None:
        """Launch Camoufox with proper C++ level fingerprint injection."""
        from browserforge.fingerprints import Screen
        from camoufox.utils import launch_options
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        # Get window size from XVFB_RESOLUTION
        width, height = _get_default_viewport()

        # Load or generate Camoufox config
        config = _load_persisted_config()
        if config is None:
            config = _generate_camoufox_config(width, height)
        else:
            # Update screen dimensions to match current XVFB_RESOLUTION
            _update_config_screen(config, width, height)
        _save_config(config)

        # Use system locale or default to en-US
        locale = os.environ.get("LANG", "en_US.UTF-8").split(".")[0].replace("_", "-")
        if locale == "C" or not locale:
            locale = "en-US"

        # Get timezone from TZ env var (set via docker -e TZ=Europe/Bucharest)
        timezone_id = os.environ.get("TZ")

        try:
            # Build launch options with proper fingerprint injection
            # This generates env vars with CAMOU_CONFIG_* for C++ level spoofing
            # Use permissive screen constraints so browserforge's internal
            # fingerprint generation doesn't fail for small Xvfb resolutions.
            # Our persisted config values take precedence via merge_into.
            screen = Screen(
                min_width=1024,
                max_width=1920,
                min_height=768,
                max_height=1080,
            )
            opts = launch_options(
                config=config,  # Pass our persisted config directly
                screen=screen,
                os="linux",
                headless=False,
                locale=locale,
                humanize=True,  # Human-like mouse movement
                i_know_what_im_doing=True,  # We're using our persisted config
            )

            # Add persistent context settings
            opts["user_data_dir"] = DEFAULT_USER_DATA_DIR

            # Handle viewport
            use_viewport = os.environ.get("USE_VIEWPORT", "").lower() == "true"
            if use_viewport:
                opts["viewport"] = {"width": width, "height": height}
            else:
                opts["no_viewport"] = True

            # Set timezone if explicitly configured
            if timezone_id and timezone_id != "UTC":
                opts["timezone_id"] = timezone_id

            # Proxy support
            proxy_url = os.environ.get("PROXY_URL", "")
            if proxy_url:
                p = _urlparse(proxy_url)
                proxy: dict[str, str] = {
                    "server": f"{p.scheme}://{p.hostname}:{p.port}"
                }
                if p.username:
                    proxy["username"] = p.username
                if p.password:
                    proxy["password"] = p.password
                opts["proxy"] = proxy

            # Accept file downloads
            opts["accept_downloads"] = True
            if (
                self.config.virtual_media_dynamic
                or self.config.virtual_camera_file
                or self.config.virtual_microphone_file
            ):
                opts["firefox_user_prefs"] = {
                    "media.captureStream.enabled": True,
                    "media.devices.insecure.enabled": True,
                    "media.getusermedia.insecure.enabled": True,
                }

            self._context = await self._playwright.firefox.launch_persistent_context(
                **opts
            )
            self._browser = self._context
            await self._configure_virtual_media()

            # Crash diagnostics — Playwright fires these events when the
            # browser / page processes die. Without them the only signal is
            # the next request returning "Connection closed while reading
            # from the driver", which leaves the operator guessing whether
            # it was OOM / segfault / external SIGKILL / etc.
            try:
                self._context.on("close", self._on_context_close)
                self._context.on(
                    "page",
                    lambda p: p.on("crash", lambda: self._on_page_crash(p)),
                )
                for existing_page in self._context.pages:
                    existing_page.on(
                        "crash", lambda p=existing_page: self._on_page_crash(p)
                    )
            except Exception as e:
                log.warning("browser: failed to wire crash diagnostics: %s", e)

            # Resize window to fill Xvfb screen using xdotool
            await asyncio.sleep(1)  # Wait for window to appear
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", "Camoufox"],
                capture_output=True,
                text=True,
            )
            for wid in result.stdout.strip().split("\n"):
                if not wid:
                    continue
                subprocess.run(["xdotool", "windowmove", wid, "0", "0"])
                subprocess.run(["xdotool", "windowsize", wid, str(width), str(height)])

            # Grant Firefox content widget initial keyboard focus.
            # Under Openbox, a freshly mapped Firefox window gives X focus to
            # the chrome (URL bar) by default, so OS-level keys from PyAutoGUI
            # don't reach the page until something focuses content. A single
            # click into the content area transfers Firefox's internal focus
            # to the content widget, and that focus persists across goto,
            # new_tab, switch_tab. Click well below the chrome toolbar
            # (~80px) on a coordinate that has no clickable element on a
            # fresh about:blank or first page.
            subprocess.run(["xdotool", "mousemove", "5", "200"])
            subprocess.run(["xdotool", "click", "1"])
        except Exception as e:
            await self.stop()
            raise BrowserError(f"Failed to launch browser: {e}")

    async def _configure_virtual_media(self) -> None:
        """Install file-backed getUserMedia streams before page navigation."""
        if not self._context:
            return
        if (
            not self.config.virtual_media_dynamic
            and not self._virtual_media_sources[_VIRTUAL_MEDIA_CAMERA]
            and not self._virtual_media_sources[_VIRTUAL_MEDIA_MICROPHONE]
        ):
            return

        async def fulfill_virtual_media(route: Any) -> None:
            request_path = _urlparse(route.request.url).path
            if request_path == "/state":
                await route.fulfill(
                    body=json.dumps(self.virtual_media_state()),
                    content_type="application/json",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "no-store",
                    },
                )
                return

            kind = _VIRTUAL_MEDIA_ROUTE_KINDS.get(request_path)
            source = self._virtual_media_sources.get(kind) if kind else None
            if source is None:
                await route.abort()
                return
            try:
                media_dir = _virtual_media_directory()
                source_name = source.relative_to(media_dir).as_posix()
                source = _resolve_virtual_media_source(source_name, media_dir)
            except (BrowserError, ValueError):
                await route.abort()
                return
            content_type = mimetypes.guess_type(source.name)[0]
            await route.fulfill(
                path=str(source),
                content_type=content_type or "application/octet-stream",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        await self._context.route(f"{VIRTUAL_MEDIA_ORIGIN}/**", fulfill_virtual_media)
        init_config = {
            "cameraUrl": (
                VIRTUAL_CAMERA_URL
                if (
                    self.config.virtual_media_dynamic
                    or self._virtual_media_sources[_VIRTUAL_MEDIA_CAMERA]
                )
                else None
            ),
            "microphoneUrl": (
                VIRTUAL_MICROPHONE_URL
                if (
                    self.config.virtual_media_dynamic
                    or self._virtual_media_sources[_VIRTUAL_MEDIA_MICROPHONE]
                )
                else None
            ),
            "dynamic": self.config.virtual_media_dynamic,
            "stateUrl": VIRTUAL_MEDIA_STATE_URL,
            "initialState": self.virtual_media_state(),
            "changeEvent": _VIRTUAL_MEDIA_CHANGE_EVENT,
        }
        await self._context.add_init_script(
            script=f"({_VIRTUAL_MEDIA_INIT_SCRIPT})({json.dumps(init_config)});",
        )
        log.info("virtual media configured", extra=self.virtual_media_state())

    async def _get_page(self) -> Any:
        """Get or create page. Auto-relaunches browser if it died."""
        # Health check first — if Camoufox crashed, restart before doing
        # anything else so the caller gets a real working page rather than
        # a Playwright handle pointing at a dead process.
        if not await self.ensure_healthy():
            raise BrowserError("browser is dead and could not be relaunched")

        if self._page:
            try:
                if not self._page.is_closed():
                    return self._page
            except Exception:
                pass
            self._page = None

        # Reuse existing page from context if available
        pages = self._context.pages
        if pages:
            self._page = pages[0]
            return self._page

        self._page = await self._context.new_page()
        return self._page

    def focus_tab_window(self, index: int) -> None:
        """Raise + focus the X window for the page at `index`.

        Two problems this solves, both stemming from Playwright's Firefox
        backend opening each new_page() as a SEPARATE OS window (not a tab in
        a shared window), and page.bring_to_front() being a no-op on Firefox:

        1. VISUAL — switching the active page otherwise leaves the display
           (screenshots / recordings / VNC) showing whatever window was last
           raised. `xdotool windowactivate` raises the correct window.
        2. KEYBOARD — after raising a window, its Firefox content widget does
           NOT hold X keyboard focus (same Openbox chrome-focus quirk the
           launch routine works around). Without a focus gesture, OS-level
           input (send_key / system_type / system_click) lands nowhere on
           the switched-to tab. The focus does NOT survive navigation or
           switching away, so it must be re-established on the live page
           every switch — a one-time gesture at tab creation is not enough.

        Camoufox windows get incrementing X window IDs in creation order,
        matching context.pages order. Sort visible Camoufox window IDs
        ascending and index-match to the page index.

        The focus gesture is a plain LEFT-click at screen (5, 200). A click
        into the content viewport transfers X keyboard focus to the content
        widget. The problem is that (5, 200) — after the chrome offset — lands
        on top-left page content, which is exactly where site logos / nav
        links live; a plain click there would fire the DOM `click` (Firefox
        dispatches click to the nearest common ancestor of mousedown/mouseup
        regardless of a small pixel move, so a "drag" doesn't help) and
        navigate the tab. To make the click harmless the CALLER first injects
        a full-viewport transparent max-z-index overlay so the click lands on
        the overlay (an inert div) instead of any link/button, then removes it
        immediately — focus transfers, nothing is activated. See
        _FOCUS_CLICK_OVERLAY_* and the tab handlers in main.py.

        Rejected alternatives: a bare left-click activates whatever is at
        (5, 200); a left "drag" (moved mouseup) still fires the click on the
        common-ancestor element; a right-click renders the native context menu
        (no pref / userChrome.css suppresses it under Camoufox) and a
        right-drag both renders the menu and fails to focus; a scroll gesture
        doesn't transfer X keyboard focus at all; keyboard-only (Tab / F6) has
        no focus anchor on a freshly-raised window.

        Best-effort: logs a warning and returns on any failure rather than
        breaking the tab operation.
        """
        try:
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--class", "Camoufox"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warning(
                "focus_tab_window: xdotool search failed", extra={"error": str(e)}
            )
            return

        wids = sorted(
            int(w) for w in result.stdout.split() if w.strip().isdigit()
        )
        if index < 0 or index >= len(wids):
            log.warning(
                "focus_tab_window: index out of range for window list",
                extra={"index": index, "window_count": len(wids)},
            )
            return

        wid = str(wids[index])
        try:
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", wid],
                capture_output=True,
                timeout=5,
            )
            # Move + size the raised window to fill the screen. Only the
            # initial (launch) window gets positioned to 0,0 and sized to the
            # full display at startup; windows opened later by new_tab come up
            # slightly smaller (e.g. 1918x1055 vs a 1920x1080 screen), so a
            # raised new-tab would leave a strip of the previous window visible
            # at the bottom edge — which screen recording (fixed x11grab
            # region) would capture. Filling the window makes the recording
            # follow the active tab pixel-for-pixel.
            geo = subprocess.run(
                ["xdotool", "getdisplaygeometry"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            dims = geo.stdout.split()
            if len(dims) == 2:
                subprocess.run(["xdotool", "windowmove", wid, "0", "0"], timeout=5)
                subprocess.run(
                    ["xdotool", "windowsize", wid, dims[0], dims[1]], timeout=5
                )
            # Plain left-click to transfer keyboard focus to the content
            # widget. The caller has injected a full-viewport transparent
            # overlay so this click lands on an inert div, not any link/button
            # underneath — focus transfers without activating page content.
            subprocess.run(["xdotool", "mousemove", "5", "200"], timeout=5)
            subprocess.run(["xdotool", "click", "1"], timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning(
                "focus_tab_window: activate/focus failed",
                extra={"index": index, "error": str(e)},
            )

    async def _update_state(self) -> None:
        """Update state from current page."""
        if not self._page:
            return

        try:
            self._state.url = self._page.url
            self._state.title = await self._page.title()
            self._state.content = await self._page.inner_text("body")
        except Exception:
            pass

    # --- Redis-backed state sync methods ---

    async def set_cookie_synced(self, cookie: dict[str, Any]) -> None:
        """Set a cookie with Redis sync if enabled."""
        if self._redis_sync and self._context:
            await self._redis_sync.set_cookie(cookie, self._context)
        elif self._context:
            await self._context.add_cookies([cookie])

    async def get_cookies_synced(self, urls: list[str] | None = None) -> list[dict]:
        """Get cookies with Redis sync if enabled."""
        if self._redis_sync and self._context:
            return await self._redis_sync.get_cookies(self._context, urls)
        elif self._context:
            return await self._context.cookies(urls)
        return []

    async def delete_cookies_synced(self) -> None:
        """Delete all cookies with Redis sync if enabled."""
        if self._redis_sync and self._context:
            await self._redis_sync.delete_cookies(self._context)
        elif self._context:
            await self._context.clear_cookies()

    async def __aenter__(self) -> "Browser":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Async context manager exit."""
        await self.stop()
