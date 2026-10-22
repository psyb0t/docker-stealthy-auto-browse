"""Bounded access to the X11 text clipboard."""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

CLIPBOARD_MAX_BYTES = 1024 * 1024
_CLIPBOARD_TIMEOUT_SECONDS = 5
_XCLIP_COMMAND = ("xclip", "-selection", "clipboard")


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
        if value.get("action") == "set_clipboard" and "text" in value:
            redacted["text"] = "[REDACTED]"
        return redacted

    redacted_params = redact(params)
    if action == "set_clipboard" and "text" in params:
        redacted_params["text"] = "[REDACTED]"
    if action == "run_script" and "yaml" in params:
        redacted_params["yaml"] = "[REDACTED]"
    return redacted_params


def _encode_text(text: str) -> bytes:
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
    """Read and write UTF-8 text through the shared X11 clipboard."""

    async def set_text(self, text: str) -> dict[str, int]:
        encoded = _encode_text(text)
        await asyncio.to_thread(_run_xclip, ("-in",), encoded)
        return {"length": len(text), "bytes": len(encoded)}

    async def get_text(self) -> dict[str, str | int]:
        content = await asyncio.to_thread(_run_xclip, ("-out",))
        text = _decode_text(content)
        return {"text": text, "length": len(text), "bytes": len(content)}
