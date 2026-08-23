"""Unit tests for the pinned browser extension installer."""

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "install_extensions.py"
SPEC = importlib.util.spec_from_file_location("install_extensions", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load extension installer")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class FakeResponse:
    """Minimal context manager for a fixed extension response body."""

    def __init__(self, content: bytes) -> None:
        self.content = content

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.content


def test_download_extension_validates_url_and_checksum() -> None:
    content = b"extension bytes"
    extension = {
        "name": "Test extension",
        "url": "https://addons.mozilla.org/firefox/downloads/file/1/test.xpi",
        "sha256": hashlib.sha256(content).hexdigest(),
    }

    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "extension.xpi"
        with patch.object(
            installer.urllib.request,
            "urlopen",
            return_value=FakeResponse(content),
        ):
            installer.download_extension(extension, str(destination))

        assert destination.read_bytes() == content


def test_download_extension_rejects_untrusted_url_and_checksum() -> None:
    content = b"extension bytes"
    extension = {
        "name": "Test extension",
        "url": "http://example.invalid/extension.xpi",
        "sha256": hashlib.sha256(content).hexdigest(),
    }

    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "extension.xpi"
        try:
            installer.download_extension(extension, str(destination))
        except ValueError as error:
            assert "invalid extension URL" in str(error)
        else:
            raise AssertionError("expected invalid extension URL")

        extension["url"] = (
            "https://addons.mozilla.org/firefox/downloads/file/1/test.xpi"
        )
        extension["sha256"] = "0" * 64
        with patch.object(
            installer.urllib.request,
            "urlopen",
            return_value=FakeResponse(content),
        ):
            try:
                installer.download_extension(extension, str(destination))
            except RuntimeError as error:
                assert "checksum mismatch" in str(error)
            else:
                raise AssertionError("expected checksum mismatch")


def main_test() -> None:
    test_download_extension_validates_url_and_checksum()
    test_download_extension_rejects_untrusted_url_and_checksum()
    print('{"result": "extension installer tests passed"}')


if __name__ == "__main__":
    main_test()
