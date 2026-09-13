"""Unit tests for ffmpeg recording readiness."""

from __future__ import annotations

import io
import json
import signal
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/app")

import recorder


class FakeProcess:
    def __init__(self, returncode: int | None = None, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = io.BytesIO(stderr)
        self.signals: list[int] = []
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, sent_signal: int) -> None:
        self.signals.append(sent_signal)
        self.returncode = -sent_signal

    def wait(self, timeout: float | None = None) -> int:
        assert timeout is not None
        return self.returncode or 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -signal.SIGKILL


class FakeClock:
    def __init__(self, output_path: Path | None = None) -> None:
        self.current = 0.0
        self.output_path = output_path
        self.sleep_count = 0

    def monotonic(self) -> float:
        return self.current

    def sleep(self, duration: float) -> None:
        self.current += duration
        self.sleep_count += 1
        if self.output_path is not None and self.sleep_count == 3:
            self.output_path.write_bytes(b"ftyp")


def test_delayed_output_becomes_ready() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output_path = Path(directory) / "recording.mp4"
        clock = FakeClock(output_path)
        process = FakeProcess()
        with (
            patch.object(recorder.time, "monotonic", clock.monotonic),
            patch.object(recorder.time, "sleep", clock.sleep),
        ):
            recorder._wait_for_ffmpeg_output(process, str(output_path))

        assert clock.sleep_count == 3
        assert process.signals == []


def test_process_exit_before_output_reports_stderr() -> None:
    process = FakeProcess(returncode=1, stderr=b"cannot open display")
    try:
        recorder._wait_for_ffmpeg_output(process, "/missing/output.mp4")
    except recorder.RecorderError as error:
        assert "rc=1" in str(error)
        assert "cannot open display" in str(error)
    else:
        raise AssertionError("expected early ffmpeg exit")


def test_startup_timeout_terminates_process_and_removes_partial_output() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output_path = Path(directory) / "recording.mp4"
        output_path.touch()
        clock = FakeClock()
        process = FakeProcess(stderr=b"startup stalled")
        with (
            patch.object(recorder, "_FFMPEG_STARTUP_TIMEOUT_SECONDS", 0.1),
            patch.object(recorder.time, "monotonic", clock.monotonic),
            patch.object(recorder.time, "sleep", clock.sleep),
        ):
            try:
                recorder._wait_for_ffmpeg_output(process, str(output_path))
            except recorder.RecorderError as error:
                assert "within 0.1 seconds" in str(error)
                assert "startup stalled" in str(error)
            else:
                raise AssertionError("expected ffmpeg startup timeout")

        assert process.signals == [signal.SIGINT]
        assert not output_path.exists()


def test_start_does_not_publish_active_state_before_output_is_ready() -> None:
    process = FakeProcess()
    instance = recorder.Recorder()
    with (
        patch.object(recorder, "_ensure_recordings_dir"),
        patch.object(recorder.subprocess, "Popen", return_value=process),
        patch.object(
            recorder,
            "_wait_for_ffmpeg_output",
            side_effect=recorder.RecorderError("not ready"),
        ),
    ):
        try:
            instance.start()
        except recorder.RecorderError as error:
            assert str(error) == "not ready"
        else:
            raise AssertionError("expected readiness failure")

    assert instance.active is False
    assert instance.status() == {"active": False}


def main_test() -> None:
    test_delayed_output_becomes_ready()
    test_process_exit_before_output_reports_stderr()
    test_startup_timeout_terminates_process_and_removes_partial_output()
    test_start_does_not_publish_active_state_before_output_is_ready()
    print(json.dumps({"result": "recorder readiness tests passed"}))


if __name__ == "__main__":
    main_test()
