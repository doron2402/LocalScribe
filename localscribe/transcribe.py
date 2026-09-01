"""Offline speech-to-text plus speaker attribution. Nothing leaves the box."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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


def _cancel_leak(audio: np.ndarray, roles: list[str]) -> np.ndarray:
    """Subtract the speaker bleed that the microphone picks up.

    Channel 0 always carries a quiet copy of whatever the speakers played, so on
    a call where you mostly listen, that bleed is the loudest thing your mic
    ever recorded — and per-channel normalization would then scale it up to
    parity and credit every word to you. Estimating the leak with least squares
    and removing it keeps the comparison honest.
    """
    if audio.shape[1] < 2 or "me" not in roles or "them" not in roles:
        return audio
    i_me, i_them = roles.index("me"), roles.index("them")
    them = audio[:, i_them]
    denom = float(them @ them)
    if denom <= 0:
        return audio
    alpha = float(np.clip((them @ audio[:, i_me]) / denom, 0.0, 0.9))
    if alpha < 1e-3:
        return audio
    cleaned = audio.copy()
    cleaned[:, i_me] = audio[:, i_me] - alpha * them
    return cleaned


def _norms(audio: np.ndarray) -> np.ndarray:
    """Per-channel loudness reference, floored so a near-silent channel cannot
    be normalized up into a contender."""
    norms = np.percentile(np.abs(audio), 95, axis=0)
    return np.maximum(norms, 0.15 * norms.max()) + _EPS


def _channel_scores(
    audio: np.ndarray, start: float, end: float, norms: np.ndarray
) -> np.ndarray:
    """Per-channel loudness over [start, end), normalized for device gain."""
    center = (start + end) / 2
    half = max((end - start) / 2, 0.125)   # never judge on less than 250 ms
    a = max(int((center - half) * config.SAMPLE_RATE), 0)
    b = min(int((center + half) * config.SAMPLE_RATE), audio.shape[0])
    if b <= a:
        return np.zeros(audio.shape[1])
    rms = np.sqrt(np.mean(np.square(audio[a:b, :]), axis=0))
    return rms / norms


def _label(roles: list[str], idx: int, confident: bool) -> str:
    names = {"me": "You", "them": "Them"}
    name = names.get(roles[idx], roles[idx])
    return name if confident else f"{name}?"


def _split_by_speaker(
    words, audio: np.ndarray, roles: list[str], norms: np.ndarray
) -> list[tuple[str, float, float, list[str]]]:
    """Group consecutive words into runs by whichever channel was loudest.

    Whisper's own segment boundaries ignore who is talking, so a single segment
    routinely spans a turn change. Deciding per word and regrouping keeps the
    turns intact.
    """
    runs: list[list] = []
    for w in words:
        scores = _channel_scores(audio, w.start, w.end, norms)
        order = np.argsort(scores)[::-1]
        top = int(order[0])
        confident = scores[top] > 1e-3 and scores[order[1]] < 0.6 * scores[top]
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
    audio: np.ndarray, roles: list[str], start: float, end: float, norms: np.ndarray
) -> str:
    """Segment-level fallback for when word timestamps are unavailable."""
    if audio.ndim == 1 or len(roles) < 2:
        return {"me": "You", "them": "Them"}.get(roles[0] if roles else "me", "Speaker")
    scores = _channel_scores(audio, start, end, norms)
    order = np.argsort(scores)[::-1]
    top = int(order[0])
    if scores[top] < 1e-3:
        return "Speaker"
    return _label(roles, top, scores[order[1]] < 0.75 * scores[top])


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
    scored = _cancel_leak(data, roles)
    norms = _norms(scored)

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
            runs = _split_by_speaker(s.words, scored, roles, norms)
        elif s.text:
            runs = [(_attribute(scored, roles, s.start, s.end, norms), s.start, s.end, s.text)]
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
