"""Speech-to-text engines.

Two of them, normalized to one shape so the speaker-attribution code doesn't
care which ran:

    faster-whisper  CTranslate2 on the CPU. Runs anywhere, needs no GPU.
    mlx             Apple MLX on the Metal GPU. Roughly twice as fast on
                    Apple Silicon; not available anywhere else.

Measured on 262s of audio with base.en, matched greedy settings: CTranslate2
on CPU 5.7s, MLX on the GPU 2.9s. The host language is not the variable —
whisper.cpp, the C++ engine a Go port would bind, takes 10.2s on the same CPU
and 2.2s on the same GPU.
"""
from __future__ import annotations

import importlib.util
import platform
from dataclasses import dataclass, field

import numpy as np

from . import config

ENGINES = ("auto", "faster-whisper", "mlx")

# faster-whisper takes short names; MLX takes Hugging Face repos.
_MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}

_MODEL_CACHE: dict[tuple, object] = {}


@dataclass
class Word:
    start: float
    end: float
    word: str


@dataclass
class RawSegment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


class EngineError(RuntimeError):
    pass


# ------------------------------------------------------------- availability

def mlx_available() -> bool:
    """MLX needs Apple Silicon; there is no CPU fallback in the package."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False
    return importlib.util.find_spec("mlx_whisper") is not None


def resolve(requested: str | None = None) -> str:
    engine = requested or config.ENGINE
    if engine == "auto":
        return "mlx" if mlx_available() else "faster-whisper"
    if engine not in ENGINES:
        raise EngineError(f"Unknown engine '{engine}'. Choose from: {', '.join(ENGINES)}.")
    if engine == "mlx" and not mlx_available():
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise EngineError("The mlx engine needs Apple Silicon. Use --engine faster-whisper.")
        raise EngineError(
            "mlx-whisper is not installed. Run:  uv pip install -e '.[mlx]'\n"
            "Or use --engine faster-whisper."
        )
    return engine


def mlx_repo(model_name: str) -> str:
    if "/" in model_name:      # already a Hugging Face repo
        return model_name
    try:
        return _MLX_REPOS[model_name]
    except KeyError:
        raise EngineError(
            f"No MLX build is mapped for '{model_name}'. Pass a Hugging Face repo "
            f"directly (e.g. mlx-community/whisper-{model_name}-mlx), or use "
            f"--engine faster-whisper."
        ) from None


# ---------------------------------------------------------------- backends

def _load_faster_whisper(model_name: str, compute_type: str):
    from faster_whisper import WhisperModel

    key = ("faster-whisper", model_name, compute_type)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    return _MODEL_CACHE[key]


def _run_faster_whisper(audio, model_name, language, want_words, compute_type):
    model = _load_faster_whisper(model_name, compute_type)
    segments, info = model.transcribe(
        audio,
        language=language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
        word_timestamps=want_words,
        condition_on_previous_text=False,  # keeps it from looping on silence
    )

    def stream():
        for s in segments:
            yield RawSegment(
                start=float(s.start),
                end=float(s.end),
                text=s.text.strip(),
                words=[Word(float(w.start), float(w.end), w.word) for w in (s.words or [])],
            )

    return stream(), info.language


def _speech_regions(audio) -> list[tuple[float, float]]:
    """Speech spans in seconds, via faster-whisper's bundled Silero VAD."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    from . import config

    samples = get_speech_timestamps(
        np.asarray(audio, dtype=np.float32),
        VadOptions(min_silence_duration_ms=500),
    )
    return [
        (s["start"] / config.SAMPLE_RATE, s["end"] / config.SAMPLE_RATE)
        for s in samples
    ]


def _overlaps(start: float, end: float, regions: list[tuple[float, float]]) -> bool:
    return any(start < r_end and end > r_start for r_start, r_end in regions)


def _run_mlx(audio, model_name, language, want_words, _compute_type):
    from mlx_whisper.transcribe import transcribe as mlx_transcribe

    # mlx-whisper has no VAD of its own, and Whisper invents text over silence —
    # a recording that ends in a quiet minute comes back with a tail of "the.
    # the. the". Keep only what the voice detector agrees is speech.
    try:
        regions = _speech_regions(audio)
    except Exception:
        regions = []

    result = mlx_transcribe(
        np.asarray(audio, dtype=np.float32),
        path_or_hf_repo=mlx_repo(model_name),
        language=language,
        word_timestamps=want_words,
        condition_on_previous_text=False,
        verbose=None,
    )

    def stream():
        for s in result.get("segments", []):
            start, end = float(s["start"]), float(s["end"])
            if regions and not _overlaps(start, end, regions):
                continue
            words = [
                Word(float(w["start"]), float(w["end"]), w["word"])
                for w in s.get("words", [])
                if not regions or _overlaps(float(w["start"]), float(w["end"]), regions)
            ]
            if s.get("words") and not words:
                continue
            yield RawSegment(start=start, end=end, text=s["text"].strip(), words=words)

    return stream(), result.get("language") or language


_BACKENDS = {"faster-whisper": _run_faster_whisper, "mlx": _run_mlx}


def run(audio, model_name: str, language: str | None, want_words: bool,
        engine: str | None = None, compute_type: str | None = None):
    """-> (iterator of RawSegment, detected language, engine actually used)"""
    resolved = resolve(engine)
    segments, lang = _BACKENDS[resolved](
        audio, model_name, language, want_words,
        compute_type or config.WHISPER_COMPUTE,
    )
    return segments, lang, resolved
