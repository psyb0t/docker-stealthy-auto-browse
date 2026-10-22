"""Unit tests for bounded clipboard text handling."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, "/app")

import clipboard


def test_utf8_boundaries() -> None:
    text = "λ" * (clipboard.CLIPBOARD_MAX_BYTES // 2)
    assert len(clipboard._encode_text(text)) == clipboard.CLIPBOARD_MAX_BYTES

    try:
        clipboard._encode_text(f"{text}a")
    except clipboard.ClipboardError as error:
        assert str(error) == (
            f"clipboard text exceeds {clipboard.CLIPBOARD_MAX_BYTES} bytes"
        )
    else:
        raise AssertionError("oversized clipboard text was accepted")


def test_invalid_utf8_is_rejected() -> None:
    try:
        clipboard._decode_text(b"\xff")
    except clipboard.ClipboardError as error:
        assert str(error) == "clipboard does not contain valid UTF-8 text"
    else:
        raise AssertionError("invalid UTF-8 clipboard content was accepted")


def test_subprocess_errors_are_sanitized() -> None:
    failure = subprocess.CompletedProcess([], 1, b"secret", None)
    with patch.object(clipboard.subprocess, "run", return_value=failure):
        try:
            clipboard._run_xclip(("-out",))
        except clipboard.ClipboardError as error:
            assert str(error) == "clipboard operation failed"
            assert "secret" not in str(error)
            assert "sensitive" not in str(error)
        else:
            raise AssertionError("failed xclip command was accepted")


def test_async_results() -> None:
    instance = clipboard.Clipboard()
    with patch.object(clipboard, "_run_xclip", return_value="café".encode()):
        result = asyncio.run(instance.get_text())
    assert result == {"text": "café", "length": 4, "bytes": 5}


def test_log_redaction_covers_nested_scripts() -> None:
    params = {
        "steps": [
            {"action": "set_clipboard", "text": "top secret"},
            {
                "if": {
                    "then": [
                        {"action": "set_clipboard", "text": "nested secret"}
                    ]
                }
            },
        ],
        "yaml": "text: yaml secret",
    }
    redacted = clipboard.redact_clipboard_params("run_script", params)
    assert redacted["steps"][0]["text"] == "[REDACTED]"
    assert redacted["steps"][1]["if"]["then"][0]["text"] == "[REDACTED]"
    assert redacted["yaml"] == "[REDACTED]"
    assert params["steps"][0]["text"] == "top secret"


if __name__ == "__main__":
    test_utf8_boundaries()
    test_invalid_utf8_is_rejected()
    test_subprocess_errors_are_sanitized()
    test_async_results()
    test_log_redaction_covers_nested_scripts()
    print("clipboard unit tests passed")
