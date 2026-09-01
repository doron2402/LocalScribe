"""Configuration: a few env vars, everything else has a sane default."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env reader so we don't pull in python-dotenv for 20 lines."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


DATA_DIR = Path(_env("LOCALSCRIBE_DATA_DIR", "~/LocalScribe")).expanduser()
AUDIO_DIR = DATA_DIR / "audio"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
SUMMARY_DIR = DATA_DIR / "summaries"

# Audio device names. Substring match, case-insensitive.
MIC_DEVICE = _env("LOCALSCRIBE_MIC_DEVICE", "")            # "" -> system default input
LOOPBACK_DEVICE = _env("LOCALSCRIBE_LOOPBACK_DEVICE", "BlackHole")

SAMPLE_RATE = 16_000  # what Whisper wants; no resample step later

# Speech-to-text engine: auto | faster-whisper | mlx
# auto picks mlx on Apple Silicon when mlx-whisper is installed (it runs on the
# Metal GPU, about 2x faster), and faster-whisper everywhere else.
ENGINE = _env("LOCALSCRIBE_ENGINE", "auto")

# Whisper. large-v3-turbo is the best speed/accuracy tradeoff on Apple Silicon
# and copes well with non-native accents. Smaller: medium.en, small.en, base.en.
WHISPER_MODEL = _env("LOCALSCRIBE_WHISPER_MODEL", "large-v3-turbo")
WHISPER_LANG = _env("LOCALSCRIBE_WHISPER_LANG", "en") or None
WHISPER_COMPUTE = _env("LOCALSCRIBE_WHISPER_COMPUTE", "int8")

# Summarizer: ollama (local LLM) | anthropic (cloud) | extractive (no LLM)
SUMMARY_BACKEND = _env("LOCALSCRIBE_SUMMARY_BACKEND", "ollama")
OLLAMA_HOST = _env("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = _env("LOCALSCRIBE_OLLAMA_MODEL", "llama3.1:8b")
# Start the Ollama daemon on demand rather than making it the user's problem.
# Only ever applies to a local host.
OLLAMA_AUTOSTART = _env("LOCALSCRIBE_OLLAMA_AUTOSTART", "1").lower() not in ("0", "false", "no")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = _env("LOCALSCRIBE_ANTHROPIC_MODEL", "claude-sonnet-5")


def ensure_dirs() -> None:
    for d in (AUDIO_DIR, TRANSCRIPT_DIR, SUMMARY_DIR):
        d.mkdir(parents=True, exist_ok=True)
