"""Offline speech-to-text plus speaker attribution. Nothing leaves the box."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config, engines

_EPS = 1e-9


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str


@dataclass
class Transcript:
    audio_path: str
    label: str
    started_at: str
    duration: float
    language: str
    segments: list[Segment]
    engine: str = ""
    model: str = ""

    @property
    def text(self) -> str:
        return "\n".join(f"[{_ts(s.start)}] {s.speaker}: {s.text}" for s in self.segments)

    @property
    def plain_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    def to_json(self) -> str:
        d = asdict(self)
        d["segments"] = [asdict(s) if not isinstance(s, dict) else s for s in self.segments]
        return json.dumps(d, indent=2)


def _ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def load_model(name: str | None = None, compute_type: str | None = None):
    """Warm the faster-whisper model cache (used by setup and tests)."""
    return engines._load_faster_whisper(
        name or config.WHISPER_MODEL, compute_type or config.WHISPER_COMPUTE
    )


def sidecar_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".json")


def read_sidecar(audio_path: Path) -> dict:
    p = sidecar_path(audio_path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _speaker_labels(roles: list[str]) -> dict[str, str]:
    return {"me": "You", "them": "Them"}


_FRAME = 0.02     # seconds per analysis frame
_SMOOTH = 0.20    # seconds of smoothing; must exceed the room's reverb tail


@dataclass
class Bleed:
    """What the microphone picks up from the speakers, as an energy ratio.

    Subtracting the loopback waveform from the mic barely helps: by the time
    sound has crossed the room it has been convolved with the room, so a delayed
    copy cancels almost nothing (measured: 0.1 dB). Energy ratios survive that.

    The asymmetry that makes this work is physical — the loopback channel can
    only ever contain the far end, never you. So loud loopback means they are
    talking, and the only question left is whether *you* are talking too, which
    is answered by whether your mic carries more than the bleed can explain.
    """

    gain: float        # mic energy per unit of loopback energy, when only they talk
    mic_floor: float   # background level of each channel
    them_floor: float
    mic_env: np.ndarray = field(default_factory=lambda: np.zeros(0))
    them_env: np.ndarray = field(default_factory=lambda: np.zeros(0))
    single: bool = False   # mic-only recording: everything is you


def _frame_rms(channel: np.ndarray) -> np.ndarray:
    size = max(int(_FRAME * config.SAMPLE_RATE), 1)
    usable = (len(channel) // size) * size
    if usable == 0:
        return np.zeros(1)
    return np.sqrt(np.mean(channel[:usable].reshape(-1, size) ** 2, axis=1))


def _envelope(channel: np.ndarray) -> np.ndarray:
    """Frame energy smoothed over ~200 ms.

    Instantaneous frames are useless for comparing the two channels: the mic
    copy of the speakers arrives a few milliseconds late and then rings on
    through the room's reverb, so a frame where the loopback has already gone
    quiet still has energy in the mic. Measured on a real recording, the
    per-frame ratio between the channels spans a factor of thirty. Smoothing
    past the reverb tail collapses that spread.
    """
    frames = _frame_rms(channel)
    width = max(int(_SMOOTH / _FRAME), 1)
    if frames.size < width:
        return frames
    kernel = np.ones(width) / width
    return np.convolve(frames, kernel, mode="same")


def measure_bleed(audio: np.ndarray, roles: list[str]) -> Bleed:
    if audio.shape[1] < 2 or "me" not in roles or "them" not in roles:
        return Bleed(0.0, 0.0, 0.0, single=True)

    i_me, i_them = roles.index("me"), roles.index("them")
    mic, them = _envelope(audio[:, i_me]), _envelope(audio[:, i_them])
    n = min(len(mic), len(them))
    mic, them = mic[:n], them[:n]

    # A digital loopback is exactly silent between sounds, so a purely relative
    # floor would treat any nonzero sample as speech.
    mic_floor = max(float(np.percentile(mic, 10)), 1e-4)
    them_floor = max(float(np.percentile(them, 10)), 1e-4)

    # Frames where the far end is clearly talking. Among those, the quietest
    # mic-to-loopback ratios are the ones where *only* they were talking, which
    # is exactly the bleed. A high percentile would be contaminated by the
    # moments you spoke over them.
    active = them > max(them_floor * 4, float(np.percentile(them, 60)))
    if active.sum() < 5:
        return Bleed(0.0, mic_floor, them_floor)
    ratios = mic[active] / (them[active] + _EPS)
    # Frames where you also spoke sit above the bleed, never below, so the bulk
    # of the distribution is bleed and its upper tail is you.
    return Bleed(float(np.percentile(ratios, 40)), mic_floor, them_floor, mic, them)


def _window_rms(audio: np.ndarray, start: float, end: float) -> np.ndarray:
    centre, half = (start + end) / 2, max((end - start) / 2, 0.06)
    a = max(int((centre - half) * config.SAMPLE_RATE), 0)
    b = min(int((centre + half) * config.SAMPLE_RATE), audio.shape[0])
    if b <= a:
        return np.zeros(audio.shape[1])
    return np.sqrt(np.mean(np.square(audio[a:b, :]), axis=0))


def _env_level(env: np.ndarray, start: float, end: float) -> float:
    if env.size == 0:
        return 0.0
    a = max(int(start / _FRAME), 0)
    b = min(max(int(end / _FRAME) + 1, a + 1), env.size)
    return float(np.mean(env[a:b]))


def _who(audio: np.ndarray, roles: list[str], bleed: Bleed,
         start: float, end: float) -> tuple[int | None, bool]:
    """-> (channel index, confident). None means nobody identifiable."""
    if bleed.single:
        return (0, True) if _window_rms(audio, start, end)[0] > bleed.mic_floor else (None, False)

    i_me, i_them = roles.index("me"), roles.index("them")
    if bleed.mic_env.size:
        mic = _env_level(bleed.mic_env, start, end)
        them = _env_level(bleed.them_env, start, end)
    else:
        rms = _window_rms(audio, start, end)
        mic, them = float(rms[i_me]), float(rms[i_them])

    they_speak = them > bleed.them_floor * 3
    explained = bleed.gain * them          # what bleed alone would put in the mic
    excess = mic - explained
    you_speak = excess > max(bleed.mic_floor * 3, 0.6 * explained)

    if they_speak and you_speak:
        return (i_me if excess > explained else i_them), False   # talking over each other
    if they_speak:
        return i_them, True
    if you_speak or mic > bleed.mic_floor * 3:
        return i_me, True
    return None, False


def _label(roles: list[str], idx: int, confident: bool) -> str:
    names = {"me": "You", "them": "Them"}
    name = names.get(roles[idx], roles[idx])
    return name if confident else f"{name}?"


def _split_by_speaker(
    words, audio: np.ndarray, roles: list[str], bleed: Bleed
) -> list[tuple[str, float, float, list[str]]]:
    """Group consecutive words into runs by whichever channel was loudest.

    Whisper's own segment boundaries ignore who is talking, so a single segment
    routinely spans a turn change. Deciding per word and regrouping keeps the
    turns intact.
    """
    runs: list[list] = []
    for w in words:
        top, confident = _who(audio, roles, bleed, w.start, w.end)
        if top is None:
            if runs:   # a gap inside someone's turn is still their turn
                runs[-1][2] = w.end
                runs[-1][3].append(w.word)
                runs[-1][4].append(False)
            continue
        if runs and runs[-1][0] == top:
            # Stay with the current speaker unless the switch is convincing;
            # single-word flips are usually crosstalk, not a real turn.
            runs[-1][2] = w.end
            runs[-1][3].append(w.word)
            runs[-1][4].append(confident)
        elif runs and not confident and (w.end - w.start) < 0.4:
            runs[-1][2] = w.end
            runs[-1][3].append(w.word)
            runs[-1][4].append(False)
        else:
            runs.append([top, w.start, w.end, [w.word], [confident]])

    out = []
    for top, start, end, words_, flags in runs:
        text = "".join(words_).strip()
        if not text:
            continue
        confident = sum(flags) >= max(1, len(flags) // 2)
        out.append((_label(roles, top, confident), start, end, text))
    return out


def _attribute(
    audio: np.ndarray, roles: list[str], bleed: Bleed, start: float, end: float
) -> str:
    """Segment-level fallback for when word timestamps are unavailable."""
    top, confident = _who(audio, roles, bleed, start, end)
    if top is None:
        return "Speaker"
    return _label(roles, top, confident)


def transcribe(
    audio_path: Path,
    model_name: str | None = None,
    language: str | None = None,
    engine: str | None = None,
    on_segment=None,
) -> Transcript:
    audio_path = Path(audio_path)
    meta = read_sidecar(audio_path)

    data, rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    if rate != config.SAMPLE_RATE:
        import soxr
        data = soxr.resample(data, rate, config.SAMPLE_RATE, quality="HQ")
        if data.ndim == 1:
            data = data[:, None]

    roles = meta.get("channel_roles") or (["me", "them"][: data.shape[1]])
    mono = data.mean(axis=1)   # what Whisper hears: everything, mixed

    # Speaker attribution reads a leak-cancelled copy instead, normalized per
    # channel so device gain doesn't decide who was talking.
    bleed = measure_bleed(data, roles)

    model_name = model_name or config.WHISPER_MODEL
    lang = language if language is not None else config.WHISPER_LANG
    multichannel = data.shape[1] > 1 and len(roles) > 1

    raw_segments, detected, used_engine = engines.run(
        mono, model_name, lang,
        want_words=multichannel,   # needed to place turn changes precisely
        engine=engine,
    )

    segments: list[Segment] = []

    def emit(seg: Segment) -> None:
        # Stitch a run onto the previous one when the same person just keeps
        # talking; otherwise the transcript reads as choppy half-sentences.
        if (
            segments
            and segments[-1].speaker == seg.speaker
            and seg.start - segments[-1].end < 1.0
            and segments[-1].end - segments[-1].start < 30
        ):
            segments[-1].text = f"{segments[-1].text} {seg.text}".strip()
            segments[-1].end = seg.end
        else:
            segments.append(seg)
        if on_segment:
            on_segment(seg)

    for s in raw_segments:
        if multichannel and s.words:
            runs = _split_by_speaker(s.words, data, roles, bleed)
        elif s.text:
            runs = [(_attribute(data, roles, bleed, s.start, s.end), s.start, s.end, s.text)]
        else:
            runs = []
        for speaker, start, end, text in runs:
            emit(Segment(round(start, 2), round(end, 2), speaker, text))

    return Transcript(
        audio_path=str(audio_path),
        label=meta.get("label", audio_path.stem),
        started_at=meta.get("started_at", ""),
        duration=round(len(mono) / config.SAMPLE_RATE, 1),
        language=detected or (lang or "unknown"),
        segments=segments,
        engine=used_engine,
        model=model_name,
    )


def write_transcript(t: Transcript, out_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = out_dir or config.TRANSCRIPT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(t.audio_path).stem
    md_path = out_dir / f"{base}.md"
    json_path = out_dir / f"{base}.json"

    header = [
        f"# Transcript — {t.label}",
        "",
        f"- Recorded: {t.started_at or 'unknown'}",
        f"- Duration: {_ts(t.duration)}",
        f"- Language: {t.language}",
        f"- Transcribed by: {t.engine or 'unknown'} ({t.model or 'unknown'})",
        f"- Audio: `{t.audio_path}`",
        "",
        "---",
        "",
    ]
    md_path.write_text("\n".join(header) + t.text + "\n")
    json_path.write_text(t.to_json())
    return md_path, json_path


def load_transcript(json_path: Path) -> Transcript:
    d = json.loads(Path(json_path).read_text())
    d["segments"] = [Segment(**s) for s in d["segments"]]
    return Transcript(**d)
