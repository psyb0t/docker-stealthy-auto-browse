"""Bounded access to the X11 text clipboard."""

from __future__ import annotations

import asyncio
import base64
import subprocess
from pathlib import Path
from typing import Any

CLIPBOARD_MAX_BYTES = 1024 * 1024
CLIPBOARD_FILE_COUNT_MAX = 32
CLIPBOARD_FILE_TOTAL_BYTES_MAX = 1024 * 1024 * 1024
_CLIPBOARD_TIMEOUT_SECONDS = 5
_XCLIP_COMMAND = ("xclip", "-selection", "clipboard")
CLIPBOARD_FORMAT_TEXT = "text"
CLIPBOARD_FORMAT_HTML = "html"
CLIPBOARD_FORMAT_PNG = "image/png"
CLIPBOARD_FORMAT_FILES = "files"
CLIPBOARD_FORMATS = (
    CLIPBOARD_FORMAT_TEXT,
    CLIPBOARD_FORMAT_HTML,
    CLIPBOARD_FORMAT_PNG,
    CLIPBOARD_FORMAT_FILES,
)
_XCLIP_TARGETS = {
    CLIPBOARD_FORMAT_TEXT: "UTF8_STRING",
    CLIPBOARD_FORMAT_HTML: "text/html",
    CLIPBOARD_FORMAT_PNG: "image/png",
    CLIPBOARD_FORMAT_FILES: "text/uri-list",
}


class ClipboardError(Exception):
    """Clipboard operation failed without exposing clipboard contents."""


def redact_clipboard_params(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Copy request parameters while removing clipboard text from log data."""

    def redact(value: Any) -> Any:
        if isinstance(value, list):
            return [redact(item) for item in value]
        if not isinstance(value, dict):
            return value
        redacted = {key: redact(item) for key, item in value.items()}
        if value.get("action") == "set_clipboard":
            for key in ("text", "content_base64", "files"):
                if key in value:
                    redacted[key] = "[REDACTED]"
        return redacted

    redacted_params = redact(params)
    if action == "set_clipboard":
        for key in ("text", "content_base64", "files"):
            if key in params:
                redacted_params[key] = "[REDACTED]"
    if action == "run_script" and "yaml" in params:
        redacted_params["yaml"] = "[REDACTED]"
    return redacted_params


def _encode_text(text: Any) -> bytes:
    if not isinstance(text, str):
        raise ClipboardError("text must be a string")
    encoded = text.encode("utf-8")
    if len(encoded) > CLIPBOARD_MAX_BYTES:
        raise ClipboardError(f"clipboard text exceeds {CLIPBOARD_MAX_BYTES} bytes")
    return encoded


def _decode_text(content: bytes) -> str:
    if len(content) > CLIPBOARD_MAX_BYTES:
        raise ClipboardError(f"clipboard text exceeds {CLIPBOARD_MAX_BYTES} bytes")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ClipboardError("clipboard does not contain valid UTF-8 text") from error


def _run_xclip(arguments: tuple[str, ...], content: bytes | None = None) -> bytes:
    stdout = subprocess.PIPE if content is None else subprocess.DEVNULL
    try:
        result = subprocess.run(
            [*_XCLIP_COMMAND, *arguments],
            input=content,
            stdout=stdout,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_CLIPBOARD_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClipboardError("clipboard operation failed") from error
    if result.returncode != 0:
        raise ClipboardError("clipboard operation failed")
    return result.stdout or b""


class Clipboard:
    """Read and write bounded data through the shared X11 clipboard."""

    async def set_text(self, text: str) -> dict[str, int]:
        encoded = _encode_text(text)
        await asyncio.to_thread(_run_xclip, ("-in",), encoded)
        return {"length": len(text), "bytes": len(encoded)}

    async def get_text(self) -> dict[str, str | int]:
        content = await asyncio.to_thread(_run_xclip, ("-out",))
        text = _decode_text(content)
        return {"text": text, "length": len(text), "bytes": len(content)}

    async def set(self, clipboard_format: str, value: Any) -> dict[str, Any]:
        target = _clipboard_target(clipboard_format)
        content = _encode_clipboard_value(clipboard_format, value)
        await asyncio.to_thread(_run_xclip, ("-in", "-target", target), content)
        return {"format": clipboard_format, "bytes": len(content)}

    async def get(self, clipboard_format: str) -> dict[str, Any]:
        target = _clipboard_target(clipboard_format)
        content = await asyncio.to_thread(_run_xclip, ("-out", "-target", target))
        if len(content) > CLIPBOARD_MAX_BYTES:
            raise ClipboardError(f"clipboard data exceeds {CLIPBOARD_MAX_BYTES} bytes")
        if clipboard_format == CLIPBOARD_FORMAT_PNG:
            return {
                "format": clipboard_format,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "bytes": len(content),
            }
        text = _decode_text(content)
        if clipboard_format == CLIPBOARD_FORMAT_FILES:
            files = [line for line in text.splitlines() if line and not line.startswith("#")]
            return {"format": clipboard_format, "files": files, "bytes": len(content)}
        return {
            "format": clipboard_format,
            "text": text,
            "length": len(text),
            "bytes": len(content),
        }


def _clipboard_target(clipboard_format: str) -> str:
    if clipboard_format not in CLIPBOARD_FORMATS:
        raise ClipboardError(f"format must be one of: {', '.join(CLIPBOARD_FORMATS)}")
    return _XCLIP_TARGETS[clipboard_format]


def _encode_clipboard_value(clipboard_format: str, value: Any) -> bytes:
    if clipboard_format in (CLIPBOARD_FORMAT_TEXT, CLIPBOARD_FORMAT_HTML):
        if not isinstance(value, str):
            raise ClipboardError("text must be a string")
        return _encode_text(value)
    if clipboard_format == CLIPBOARD_FORMAT_PNG:
        if not isinstance(value, str):
            raise ClipboardError("content_base64 must be a string")
        try:
            content = base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ClipboardError("content_base64 must be valid base64") from error
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ClipboardError("clipboard image must be PNG")
        if len(content) > CLIPBOARD_MAX_BYTES:
            raise ClipboardError(f"clipboard data exceeds {CLIPBOARD_MAX_BYTES} bytes")
        return content
    if not isinstance(value, list) or not value:
        raise ClipboardError("files must be a non-empty list")
    if len(value) > CLIPBOARD_FILE_COUNT_MAX:
        raise ClipboardError(
            f"files supports at most {CLIPBOARD_FILE_COUNT_MAX} entries"
        )
    uris: list[str] = []
    total_bytes = 0
    for file_path in value:
        if not isinstance(file_path, str) or "\x00" in file_path:
            raise ClipboardError("every clipboard file must be an absolute path")
        path = Path(file_path)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ClipboardError("every clipboard file must be an existing regular file")
        total_bytes += path.stat().st_size
        if total_bytes > CLIPBOARD_FILE_TOTAL_BYTES_MAX:
            raise ClipboardError("clipboard files exceed the aggregate size limit")
        uris.append(path.as_uri())
    return _encode_text("\r\n".join(uris) + "\r\n")
