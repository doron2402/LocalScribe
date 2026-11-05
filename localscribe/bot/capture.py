"""Records one browser's audio, and only that browser's, through PulseAudio.

Each call gets its own null sink. Chromium plays into it and parec records its
monitor, so concurrent meetings cannot bleed into each other.
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from .. import config


class CaptureError(RuntimeError):
    pass


def sink_name(key: str) -> str:
    return f"meet_{key[:16]}"


def _pactl(*args: str) -> str:
    done = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=15)
    if done.returncode != 0:
        raise CaptureError(f"pactl {args[0]} failed: {done.stderr.strip()}")
    return done.stdout.strip()


class Sink:
    def __init__(self, name: str):
        self.name = name
        self._module: str | None = None

    def __enter__(self) -> Sink:
        self._module = _pactl("load-module", "module-null-sink", f"sink_name={self.name}",
                              f"sink_properties=device.description={self.name}")
        return self

    def __exit__(self, *exc) -> None:
        if self._module:
            try:
                _pactl("unload-module", self._module)
            except (CaptureError, subprocess.SubprocessError):
                pass


class Recording:
    """16 kHz mono WAV, the rate the rest of the pipeline expects."""

    def __init__(self, sink: str, path: Path):
        self.sink, self.path, self._proc = sink, Path(path), None

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = subprocess.Popen([
            "parec", "-d", f"{self.sink}.monitor", "--file-format=wav",
            "--format=s16le", f"--rate={config.SAMPLE_RATE}", "--channels=1",
            str(self.path),
        ], stderr=subprocess.DEVNULL)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if not self._proc or self._proc.poll() is not None:
            return
        # SIGINT, not kill: parec rewrites the WAV header's length on the way out.
        os.kill(self._proc.pid, signal.SIGINT)
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
