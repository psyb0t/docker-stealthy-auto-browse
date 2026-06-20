"""Screen recording via ffmpeg x11grab.

Captures Xvfb (DISPLAY=:99) to mp4. Mouse cursor inclusion controlled by
the show_cursor flag on start (-draw_mouse 1 or 0; defaults to on). One
active recording at a time. Output to /recordings (mount). Slug provided
at stop time so the user names the file after the run, not before. Tmp
file is renamed atomically into place on stop.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import uuid
from typing import Any

from logger import get_logger

log = get_logger(__name__)

RECORDINGS_DIR = "/recordings"
TMP_PREFIX = ".tmp-"

# Recording modes are descriptive — they're recorded into the response and
# logs but don't affect the capture region. The caller (main.py) supplies
# the actual offset_x/offset_y, which lets viewport mode use the calibrated
# window_offset instead of a hardcoded chrome height.
_VALID_MODES = {"window", "viewport", "desktop"}

# Default video knobs — favour low CPU + reasonable file size for browser
# footage. Override per-call via start_recording params if needed.
DEFAULT_FPS = 15
DEFAULT_PRESET = "ultrafast"
DEFAULT_CRF = 28

# Sanitization: slugs must be filesystem-safe. Strict allowlist.
_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")


class RecorderError(Exception):
    """Recorder error (start/stop failure)."""


def _xvfb_resolution() -> tuple[int, int]:
    """Read Xvfb resolution from XVFB_RESOLUTION env (WxH)."""
    raw = os.environ.get("XVFB_RESOLUTION", "1920x1080")
    parts = raw.split("x")
    if len(parts) != 2:
        return 1920, 1080
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 1920, 1080


def _ensure_recordings_dir() -> None:
    """Verify /recordings exists and is writable. Fail fast otherwise."""
    if not os.path.isdir(RECORDINGS_DIR):
        raise RecorderError(
            f"{RECORDINGS_DIR} is not a directory — mount it with "
            f"-v ./recordings:{RECORDINGS_DIR}"
        )
    if not os.access(RECORDINGS_DIR, os.W_OK):
        raise RecorderError(
            f"{RECORDINGS_DIR} is not writable by the current user"
        )


def _validate_slug(slug: str) -> str:
    """Validate slug against strict allowlist. Returns the slug or raises."""
    if not _SLUG_RE.match(slug):
        raise RecorderError(
            "slug must match [a-zA-Z0-9][a-zA-Z0-9_-]{0,62} — got "
            f"{slug!r}"
        )
    return slug


def _resolve_collision(slug: str) -> str:
    """If /recordings/{slug}.mp4 exists, return next free {slug}-2, -3, …"""
    candidate = os.path.join(RECORDINGS_DIR, f"{slug}.mp4")
    if not os.path.exists(candidate):
        return candidate
    idx = 2
    while True:
        candidate = os.path.join(RECORDINGS_DIR, f"{slug}-{idx}.mp4")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def cleanup_orphan_tmp_files(max_age_s: int = 3600) -> int:
    """Delete stale {TMP_PREFIX}*.mp4 files left behind by previous crashes.

    Returns count removed. Called at startup from entrypoint / app boot.
    """
    if not os.path.isdir(RECORDINGS_DIR):
        return 0
    now = time.time()
    removed = 0
    for name in os.listdir(RECORDINGS_DIR):
        if not name.startswith(TMP_PREFIX):
            continue
        path = os.path.join(RECORDINGS_DIR, name)
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age < max_age_s:
            continue
        try:
            os.unlink(path)
            removed += 1
            log.info("recorder: removed orphan tmp file %s", name)
        except OSError as e:
            log.warning("recorder: failed to remove orphan %s: %s", name, e)
    return removed


class Recorder:
    """Single-active-recording ffmpeg controller."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._tmp_path: str | None = None
        self._recording_id: str | None = None
        self._mode: str | None = None
        self._started_at: float | None = None
        self._fps: int = DEFAULT_FPS

    @property
    def active(self) -> bool:
        return self._proc is not None

    def status(self) -> dict[str, Any]:
        """Snapshot of current recording state."""
        if not self.active:
            return {"active": False}
        return {
            "active": True,
            "recording_id": self._recording_id,
            "mode": self._mode,
            "started_at": self._started_at,
            "elapsed_s": round(time.time() - (self._started_at or 0), 2),
            "tmp_path": self._tmp_path,
        }

    def start(
        self,
        mode: str = "window",
        fps: int = DEFAULT_FPS,
        offset_x: int = 0,
        offset_y: int = 0,
        show_cursor: bool = True,
    ) -> dict[str, Any]:
        """Begin recording. Returns descriptor with recording_id + tmp_path.

        offset_x / offset_y crop the capture region from the top-left of
        the Xvfb screen. Caller (main.py) supplies these per mode — viewport
        passes the calibrated window_offset; window/desktop pass (0, 0).

        show_cursor controls ffmpeg's -draw_mouse flag. Default True keeps
        the OS-level cursor visible (useful for demos / debugging). Set
        False to record pure page pixels without the cursor sprite.
        """
        if self.active:
            raise RecorderError(
                "another recording is already active — call stop_recording "
                "first or run a second instance"
            )
        if mode not in _VALID_MODES:
            raise RecorderError(
                f"unknown mode {mode!r} — must be one of "
                f"{sorted(_VALID_MODES)}"
            )
        if fps < 1 or fps > 60:
            raise RecorderError(f"fps must be in [1, 60] — got {fps}")
        if offset_x < 0 or offset_y < 0:
            raise RecorderError(
                f"offset must be non-negative — got ({offset_x}, {offset_y})"
            )

        _ensure_recordings_dir()

        screen_w, screen_h = _xvfb_resolution()
        if offset_x >= screen_w or offset_y >= screen_h:
            raise RecorderError(
                f"offset ({offset_x}, {offset_y}) is outside the "
                f"{screen_w}x{screen_h} display"
            )
        capture_w = screen_w - offset_x
        capture_h = screen_h - offset_y
        # ffmpeg libx264 requires even dimensions
        if capture_w % 2:
            capture_w -= 1
        if capture_h % 2:
            capture_h -= 1

        recording_id = uuid.uuid4().hex[:12]
        tmp_path = os.path.join(
            RECORDINGS_DIR, f"{TMP_PREFIX}{recording_id}.mp4"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel", "warning",
            "-f", "x11grab",
            "-framerate", str(fps),
            "-draw_mouse", "1" if show_cursor else "0",
            "-video_size", f"{capture_w}x{capture_h}",
            "-i", f":99.0+{offset_x},{offset_y}",
            "-c:v", "libx264",
            "-preset", DEFAULT_PRESET,
            "-crf", str(DEFAULT_CRF),
            "-pix_fmt", "yuv420p",
            tmp_path,
        ]

        log.info("recorder: starting %s recording -> %s", mode, tmp_path)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (OSError, FileNotFoundError) as e:
            raise RecorderError(f"failed to spawn ffmpeg: {e}") from e

        # Give ffmpeg a beat to fail fast on bad input (missing X11, etc).
        time.sleep(0.2)
        if proc.poll() is not None:
            err = b""
            if proc.stderr is not None:
                err = proc.stderr.read() or b""
            raise RecorderError(
                f"ffmpeg exited immediately (rc={proc.returncode}): "
                f"{err.decode('utf-8', 'replace').strip()}"
            )

        self._proc = proc
        self._tmp_path = tmp_path
        self._recording_id = recording_id
        self._mode = mode
        self._started_at = time.time()
        self._fps = fps

        return {
            "recording_id": recording_id,
            "mode": mode,
            "fps": fps,
            "show_cursor": show_cursor,
            "tmp_path": tmp_path,
            "capture_size": {"width": capture_w, "height": capture_h},
        }

    def stop(self, slug: str) -> dict[str, Any]:
        """Stop the active recording and rename tmp file to {slug}.mp4."""
        if not self.active:
            raise RecorderError("no active recording — call start_recording first")

        _validate_slug(slug)
        assert self._proc is not None
        assert self._tmp_path is not None
        assert self._started_at is not None

        proc = self._proc
        tmp_path = self._tmp_path
        started_at = self._started_at

        # Clean ffmpeg shutdown: SIGINT finalizes mp4 moov atom. SIGKILL
        # would leave a non-playable file.
        log.info("recorder: stopping recording %s", self._recording_id)
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("recorder: ffmpeg did not exit after SIGINT; killing")
            proc.kill()
            proc.wait(timeout=5)

        # Reset state regardless of file outcome so the next start works.
        self._proc = None
        self._tmp_path = None
        recording_id = self._recording_id
        mode = self._mode
        self._recording_id = None
        self._mode = None
        self._started_at = None

        if not os.path.exists(tmp_path):
            raise RecorderError(
                f"tmp file {tmp_path} missing after ffmpeg exit "
                f"(rc={proc.returncode}) — recording lost"
            )

        final_path = _resolve_collision(slug)
        try:
            os.rename(tmp_path, final_path)
        except OSError as e:
            raise RecorderError(
                f"failed to rename {tmp_path} -> {final_path}: {e}"
            ) from e

        size_bytes = os.path.getsize(final_path)
        duration_s = round(time.time() - started_at, 2)

        log.info(
            "recorder: saved %s (%d bytes, %.2fs)",
            final_path, size_bytes, duration_s,
        )
        return {
            "recording_id": recording_id,
            "mode": mode,
            "slug": os.path.splitext(os.path.basename(final_path))[0],
            "path": final_path,
            "duration_s": duration_s,
            "size_bytes": size_bytes,
        }

    def abort(self) -> None:
        """Kill any active recording without renaming. For shutdown paths."""
        if not self.active:
            return
        assert self._proc is not None
        try:
            self._proc.send_signal(signal.SIGINT)
            self._proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        if self._tmp_path and os.path.exists(self._tmp_path):
            try:
                os.unlink(self._tmp_path)
            except OSError:
                pass
        self._proc = None
        self._tmp_path = None
        self._recording_id = None
        self._mode = None
        self._started_at = None
