"""Audio capture.

Two sources are recorded at once and kept on *separate channels*:

    channel 0 = your microphone        -> "You"
    channel 1 = system-audio loopback  -> "Them"

Keeping them separate is what lets the transcript label who spoke without a
diarization model: whichever channel carries the energy during a segment is the
speaker. macOS has no built-in way to capture system output, so channel 1 needs
a loopback driver (BlackHole). Without it you record only your own half of the
call.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
import soxr

from . import config

_BLOCK = 1024          # frames per callback
_MAX_SKEW_SEC = 0.5    # how far a non-clock source may drift before we trim it


@dataclass
class Device:
    index: int
    name: str
    channels: int
    samplerate: float


def list_input_devices() -> list[Device]:
    devices = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            devices.append(
                Device(i, d["name"], d["max_input_channels"], d["default_samplerate"])
            )
    return devices


def default_input() -> Device | None:
    try:
        idx = sd.default.device[0]
    except Exception:
        return None
    if idx is None or idx < 0:
        return None
    for d in list_input_devices():
        if d.index == idx:
            return d
    return None


def find_device(query: str) -> Device | None:
    """Case-insensitive substring match, preferring an exact name match."""
    if not query:
        return None
    devices = list_input_devices()
    for d in devices:
        if d.name.lower() == query.lower():
            return d
    for d in devices:
        if query.lower() in d.name.lower():
            return d
    return None


@dataclass
class _Source:
    role: str
    device: Device
    stream: sd.InputStream | None = None
    q: queue.Queue = field(default_factory=queue.Queue)
    resampler: object | None = None
    buf: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    level: float = 0.0
    overflows: int = 0

    def _callback(self, indata, frames, time_info, status):
        if status and status.input_overflow:
            self.overflows += 1
        # Downmix to mono immediately — we only need one channel per person.
        self.q.put(indata.mean(axis=1).astype(np.float32, copy=True))

    def open(self) -> None:
        self.stream = sd.InputStream(
            device=self.device.index,
            channels=min(2, self.device.channels),
            samplerate=self.device.samplerate,
            blocksize=_BLOCK,
            dtype="float32",
            callback=self._callback,
        )
        rate = int(self.device.samplerate)
        if rate != config.SAMPLE_RATE:
            self.resampler = soxr.ResampleStream(
                rate, config.SAMPLE_RATE, 1, dtype="float32", quality="HQ"
            )

    def drain(self) -> None:
        """Move everything the callback produced into `buf`, at 16 kHz."""
        chunks = []
        while True:
            try:
                chunks.append(self.q.get_nowait())
            except queue.Empty:
                break
        if not chunks:
            return
        block = np.concatenate(chunks)
        if self.resampler is not None:
            block = self.resampler.resample_chunk(block)
        if block.size:
            self.level = float(np.sqrt(np.mean(np.square(block))))
            self.buf = np.concatenate([self.buf, block])

    def take(self, n: int) -> np.ndarray:
        """Pop n frames, zero-padding if the source is running behind."""
        out = np.zeros(n, dtype=np.float32)
        have = min(n, self.buf.size)
        out[:have] = self.buf[:have]
        self.buf = self.buf[have:]
        # Drift control: this source runs off its own clock, so over an hour it
        # will slowly gain or lose samples relative to the clock source.
        max_slack = int(_MAX_SKEW_SEC * config.SAMPLE_RATE)
        if self.buf.size > max_slack:
            self.buf = self.buf[self.buf.size - max_slack:]
        return out


class Recorder:
    """Records the resolved sources to a multi-channel 16 kHz WAV."""

    def __init__(self, out_path: Path, mic: Device | None, loopback: Device | None):
        if mic is None and loopback is None:
            raise RuntimeError("No input device to record from.")
        self.out_path = out_path
        self.sources: list[_Source] = []
        if mic is not None:
            self.sources.append(_Source("me", mic))
        if loopback is not None:
            self.sources.append(_Source("them", loopback))
        self._stop = threading.Event()
        self.frames_written = 0

    @property
    def roles(self) -> list[str]:
        return [s.role for s in self.sources]

    @property
    def levels(self) -> dict[str, float]:
        return {s.role: s.level for s in self.sources}

    @property
    def seconds(self) -> float:
        return self.frames_written / config.SAMPLE_RATE

    def stop(self) -> None:
        self._stop.set()

    def run(self, max_seconds: float | None = None, tick=None) -> Path:
        """Blocks until stop() or max_seconds. `tick` is called ~10x/sec."""
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        for s in self.sources:
            s.open()

        clock = self.sources[0]  # everyone else is aligned against this one
        started = time.monotonic()

        with sf.SoundFile(
            self.out_path, "w",
            samplerate=config.SAMPLE_RATE,
            channels=len(self.sources),
            subtype="PCM_16",
        ) as wav:
            for s in self.sources:
                s.stream.start()
            try:
                while not self._stop.is_set():
                    time.sleep(0.1)
                    for s in self.sources:
                        s.drain()
                    n = clock.buf.size
                    if n:
                        block = np.stack([s.take(n) for s in self.sources], axis=1)
                        wav.write(np.clip(block, -1.0, 1.0))
                        self.frames_written += n
                    if tick:
                        tick(self)
                    if max_seconds and time.monotonic() - started >= max_seconds:
                        break
            finally:
                for s in self.sources:
                    s.stream.stop()
                    s.stream.close()
                # Flush whatever the callbacks left behind.
                for s in self.sources:
                    s.drain()
                n = max((s.buf.size for s in self.sources), default=0)
                if n:
                    block = np.stack([s.take(n) for s in self.sources], axis=1)
                    wav.write(np.clip(block, -1.0, 1.0))
                    self.frames_written += n

        return self.out_path

    def overflow_report(self) -> dict[str, int]:
        return {s.role: s.overflows for s in self.sources}
