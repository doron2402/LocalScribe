#!/usr/bin/env bash
#
# One command to get localscribe running: Python env, system-audio loopback,
# speech model, local LLM. Safe to re-run — every step checks first.
#
#   ./scripts/setup.sh                  # everything
#   ./scripts/setup.sh --no-llm         # skip Ollama (use --backend extractive)
#   ./scripts/setup.sh --no-audio       # skip BlackHole (needs sudo + a reboot)
#   ./scripts/setup.sh --whisper small.en --llm qwen2.5:7b
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

WHISPER_MODEL="${LOCALSCRIBE_WHISPER_MODEL:-large-v3-turbo}"
LLM_MODEL="${LOCALSCRIBE_OLLAMA_MODEL:-llama3.1:8b}"
DO_LLM=1; DO_AUDIO=1; DO_MODEL=1
NEEDS_REBOOT=0
NOTES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-llm)     DO_LLM=0 ;;
    --no-audio)   DO_AUDIO=0 ;;
    --no-model)   DO_MODEL=0 ;;
    --whisper)    WHISPER_MODEL="$2"; shift ;;
    --llm)        LLM_MODEL="$2"; shift ;;
    -h|--help)    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

bold() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; NOTES+=("$*"); }

# ---------------------------------------------------------------- 1. Python
bold "Python environment"
PY=""
for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                 /opt/homebrew/bin/python3.11 "$(command -v python3 || true)"; do
  [[ -x "$candidate" ]] || continue
  # On Apple Silicon an x86_64 Python drags CTranslate2 through Rosetta and
  # transcription takes roughly three times as long.
  if [[ "$(uname -m)" == "arm64" && "$("$candidate" -c 'import platform;print(platform.machine())')" != "arm64" ]]; then
    continue
  fi
  PY="$candidate"; break
done
if [[ -z "$PY" ]]; then
  echo "No suitable Python 3.10+ found." >&2
  [[ "$(uname -m)" == "arm64" ]] && echo "On Apple Silicon: brew install python@3.13" >&2
  exit 1
fi
ok "using $PY ($("$PY" -c 'import platform;print(platform.machine())'))"

# Metal-accelerated speech-to-text is Apple-Silicon only, and about 3x faster.
EXTRAS="dev"
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  EXTRAS="dev,mlx"
fi

if command -v uv >/dev/null; then
  uv venv --python "$PY" --allow-existing >/dev/null
  uv pip install -q -e ".[$EXTRAS]"
else
  "$PY" -m venv --upgrade-deps .venv 2>/dev/null || "$PY" -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -e ".[$EXTRAS]"
fi
ok "installed into .venv (extras: $EXTRAS)"

# ------------------------------------------------------- 2. system audio
if [[ $DO_AUDIO -eq 1 && "$(uname -s)" == "Darwin" ]]; then
  bold "System-audio loopback (BlackHole)"
  if [[ -d /Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver ]]; then
    ok "already installed"
  elif command -v brew >/dev/null; then
    echo "    The installer needs your password."
    if brew install --cask blackhole-2ch; then
      ok "installed"
      NEEDS_REBOOT=1
    else
      warn "BlackHole install failed — run: brew install --cask blackhole-2ch"
    fi
  else
    warn "Homebrew missing. Install BlackHole from https://existential.audio/blackhole/"
  fi
fi

# ------------------------------------------------------------ 3. Whisper
if [[ $DO_MODEL -eq 1 ]]; then
  bold "Speech model ($WHISPER_MODEL)"
  echo "    Downloading if absent — large-v3-turbo is about 1.6 GB."
  if .venv/bin/python - "$WHISPER_MODEL" <<'WARM'
import sys

from localscribe import engines

model = sys.argv[1]
engine = engines.resolve()
# One second of silence: downloads the weights, and on the mlx engine also
# pays the one-off Metal kernel compilation that would otherwise land on
# the user's first real meeting.
engines.run([0.0] * 16000, model, "en", True, engine=engine)
print(f"    warmed {model} on {engine}")
WARM
  then ok "$WHISPER_MODEL ready"
  else warn "Could not fetch $WHISPER_MODEL — it will download on first use."
  fi
fi

# --------------------------------------------------------------- 4. Ollama
if [[ $DO_LLM -eq 1 ]]; then
  bold "Local summarizer (Ollama + $LLM_MODEL)"
  if ! command -v ollama >/dev/null; then
    if command -v brew >/dev/null; then
      brew install ollama && ok "installed"
    else
      warn "Homebrew missing — see https://ollama.com/download"
    fi
  else
    ok "ollama present"
  fi

  if command -v ollama >/dev/null; then
    if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      if command -v brew >/dev/null; then
        brew services start ollama >/dev/null 2>&1 || true
      fi
      curl -sf --retry 30 --retry-delay 1 --retry-connrefused \
        http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || true
    fi
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      ok "server running (starts at login)"
      if curl -sf http://127.0.0.1:11434/api/tags | grep -q "\"$LLM_MODEL\""; then
        ok "$LLM_MODEL already pulled"
      else
        echo "    Pulling $LLM_MODEL (a few GB)…"
        ollama pull "$LLM_MODEL" && ok "$LLM_MODEL ready"
      fi
    else
      warn "Ollama did not start — run 'ollama serve', or use --backend extractive"
    fi
  fi
fi

# ------------------------------------------------------------- 5. PATH link
bold "Command line"
LINK_DIR=""
for d in "$HOME/.local/bin" "$HOME/bin"; do
  case ":$PATH:" in *":$d:"*) LINK_DIR="$d"; break ;; esac
done
if [[ -n "$LINK_DIR" ]]; then
  mkdir -p "$LINK_DIR"
  ln -sf "$PWD/bin/localscribe" "$LINK_DIR/localscribe"
  ok "linked into $LINK_DIR — just run: localscribe"
else
  warn "No writable PATH directory found. Run it as ./bin/localscribe, or link it:"
  echo "        ln -sf $PWD/bin/localscribe /usr/local/bin/localscribe"
fi

# ----------------------------------------------------------------- 6. check
bold "Checking the install"
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
.venv/bin/localscribe doctor || true

bold "Done"
if [[ ${#NOTES[@]} -gt 0 ]]; then
  echo "  Unfinished:"
  for n in "${NOTES[@]}"; do echo "    - $n"; done
fi
if [[ $NEEDS_REBOOT -eq 1 ]]; then
  cat <<'MSG'

  BlackHole needs a reboot. Afterwards, route your audio through it:
    1. Open Audio MIDI Setup (/Applications/Utilities)
    2. + -> Create Multi-Output Device
    3. Tick your headphones AND BlackHole 2ch
    4. Set that Multi-Output Device as the Mac's sound output
  Then re-run: .venv/bin/localscribe doctor
MSG
fi
cat <<'MSG'

  Record your first meeting:
    localscribe record --label "Standup"
MSG
