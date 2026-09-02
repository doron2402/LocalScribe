# LocalScribe

[![CI](https://github.com/doron2402/LocalScribe/actions/workflows/ci.yml/badge.svg)](https://github.com/doron2402/LocalScribe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**English** · [עברית](README.he.md) · [Español](README.es.md) · [简体中文](README.zh-CN.md) · [Français](README.fr.md)

Records a meeting on your Mac, transcribes it, and summarizes it — all on the
machine. No audio ever leaves the box, and with the default summarizer no text
does either.

```
mic ─────────┐
             ├─► 16 kHz stereo WAV ─► Whisper ─► transcript ─► local LLM ─► summary.md
system ──────┘   (ch0 = you,          (offline)               (Ollama)
  audio tap       ch1 = them)
```

## Quick start

```bash
git clone git@github.com:doron2402/LocalScribe.git
cd LocalScribe
./scripts/setup.sh
```

Then record something:

```bash
localscribe record --label "Standup"
```

Talk, press `Ctrl-C` when the meeting ends, and wait a few seconds. You get:

```
~/LocalScribe/audio/standup_2026-09-01_1000.wav          the recording
~/LocalScribe/transcripts/standup_2026-09-01_1000.md     who said what
~/LocalScribe/summaries/standup_2026-09-01_1000.md       TL;DR, decisions, action items
```

Three things worth knowing before your first real meeting:

- **Run `localscribe doctor`.** It tells you what is missing and how to fix it.
- **Both sides of the call are captured automatically.** On macOS 14.4 or
  newer that needs no driver, no password and no reboot — see
  [System audio](#system-audio-important).
- **The first run downloads a ~1.6 GB speech model.** `setup.sh` fetches it up
  front so it doesn't happen mid-meeting.

No server to start, nothing running in the background, no API key, no network.

## Open-source pieces

| Job | Package | License |
|---|---|---|
| Audio capture | [sounddevice](https://github.com/spatialaudio/python-sounddevice) / PortAudio | MIT |
| WAV I/O | [soundfile](https://github.com/bastibe/python-soundfile) / libsndfile | BSD-3 |
| Resampling | [soxr](https://github.com/dofuuz/python-soxr) | LGPL-2.1 |
| Speech-to-text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + CTranslate2 | MIT |
| Speech-to-text (GPU) | [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) | MIT |
| Summarization | [Ollama](https://github.com/ollama/ollama) + Llama 3.1 | MIT / Llama license |
| System audio | Core Audio process taps via [pyobjc](https://github.com/ronaldoussoren/pyobjc) | MIT |
| System audio (macOS ≤ 13) | [BlackHole](https://github.com/ExistentialAudio/BlackHole) | GPL-3.0 |

## What setup.sh does

It sets up the virtualenv, checks how this Mac can capture system audio
(installing BlackHole only if the machine is too old for audio taps), downloads
the Whisper model, installs Ollama and pulls the summarizer model, links a
`localscribe` command onto your PATH, then runs the tests and `doctor`. It is
safe to re-run; every step checks first.

```bash
./scripts/setup.sh --no-llm                    # skip Ollama entirely
./scripts/setup.sh --no-audio                  # skip the system-audio check entirely
./scripts/setup.sh --whisper small.en          # a smaller, faster speech model
```

On Apple Silicon the script insists on an **arm64** Python — an x86_64 one runs
CTranslate2 through Rosetta and transcription takes about 3x longer.

### System audio (important)
<a id="system-audio-important"></a>

To record the people you are talking to, LocalScribe has to capture what comes
*out* of your speakers, not just what goes into your microphone.

On **macOS 14.4 and newer** it does that with a Core Audio process tap: the
recorder taps the system's output directly and wraps it in a temporary input
device that exists only while you are recording. No driver, no admin password,
no reboot, and nothing left behind afterwards. It just works, and
`localscribe doctor` will say so.

The first time you record, macOS may ask for permission under **Privacy &
Security → Screen & System Audio Recording**. Grant it once.

On **macOS 13 or older**, taps don't exist and you need a loopback driver
instead. `setup.sh` installs [BlackHole](https://existential.audio/blackhole/)
(it needs your password and a reboot), and then:

1. Open **Audio MIDI Setup** (`/Applications/Utilities`).
2. `+` → **Create Multi-Output Device**, tick your speakers/headphones **and**
   BlackHole 2ch.
3. Set that Multi-Output Device as the Mac's sound output.

Either way you still hear the call normally.

```bash
localscribe record --system-audio tap      # insist on the Core Audio tap
localscribe record --system-audio device   # insist on BlackHole or similar
localscribe record --system-audio off      # microphone only
```

## Does it need a server?

No. LocalScribe is a one-shot command: it records, transcribes, summarizes,
writes two markdown files and exits. Nothing listens on a port, nothing runs in
the background between meetings, and none of it needs the network.

The one exception is the local summarizer. Ollama is a daemon on
`127.0.0.1:11434`, and LocalScribe starts it on demand if it isn't already up —
so it is not something you have to remember either. Set
`LOCALSCRIBE_OLLAMA_AUTOSTART=0` to manage it yourself, or use
`--backend extractive` and there is no daemon at all.

## Use

`scripts/setup.sh` links a `localscribe` command onto your PATH. Otherwise run
`./bin/localscribe` from the checkout — same thing, no virtualenv to activate.

```bash
localscribe doctor                       # check the setup
localscribe devices                      # list input devices

# Record until Ctrl-C, then transcribe and summarize
localscribe record --label "Latency sync"

# Auto-stop after a set time
localscribe record --label "Standup" --duration 20m

# Re-run an existing recording (e.g. after changing the prompt or model)
localscribe process ~/LocalScribe/audio/standup_2026-08-31_1000.wav

# Re-summarize a transcript without re-transcribing
localscribe summarize ~/LocalScribe/transcripts/standup_2026-08-31_1000.json

localscribe list                         # what you've recorded so far
```

Output lands in `~/LocalScribe/{audio,transcripts,summaries}`.

## Keeping and deleting

Recordings are deleted after **30 days**. The transcripts and summaries made
from them are kept — they are tiny, and they are the reason you recorded
anything. Audio is what piles up (roughly 60 MB an hour) and what carries other
people's voices, so it is the part on a timer.

Old files are swept at the start of every `record` and `process`, so the policy
runs without a cron job. You can also do it by hand:

```bash
localscribe prune --dry-run              # show what would go, delete nothing
localscribe prune                        # delete it
localscribe prune --all --days 90        # expire the notes too, after 90 days
```

Change the defaults in `.env`:

```bash
LOCALSCRIBE_RETENTION_DAYS=7             # audio; 0 keeps it forever
LOCALSCRIBE_RETENTION_TRANSCRIPTS=90     # 0 by default, meaning keep
LOCALSCRIBE_RETENTION_SUMMARIES=0
```

Deletion is permanent — there is no trash and no undo. It is deliberately narrow
about what it will touch: only inside LocalScribe's own directories, only the
file types it writes, and never through a symlink, so a stray file you put there
is safe.

## Who said what

The two audio sources are kept on separate channels — your mic on the left, the
loopback on the right — so localscribe labels speakers by comparing per-word
energy across channels instead of running a diarization model. That gives you
**You** vs **Them**, not individual names, but it is exact, free, and needs no
gated Hugging Face model. A trailing `?` (`Them?`) marks a word where both
channels were live and the call was close.

To get real names, pipe the transcript through the summarizer — an LLM
generally works them out from how people address each other.

## Engines

Two speech engines, same output. `auto` (the default) picks `mlx` when it is
installed on Apple Silicon and `faster-whisper` everywhere else.

```bash
localscribe process recording.wav --engine mlx             # Metal GPU
localscribe process recording.wav --engine faster-whisper  # CPU, runs anywhere
```

262 seconds of audio, `base.en`, full pipeline including per-word speaker
attribution, on an M-series Mac:

| Engine | Compute | Wall |
|---|---|---|
| faster-whisper (CTranslate2) | CPU int8 | 11.9s |
| **mlx-whisper** | **Metal GPU** | **4.2s** |

The first `mlx` run in a fresh install takes an extra ~30s while Metal compiles
its kernels. That is one-off and cached; `scripts/setup.sh` pays it for you so
it doesn't land on your first real meeting.

Worth knowing if you are considering a rewrite in a compiled language: the host
language is not what makes this fast. On the same machine and audio, whisper.cpp
— the C++ engine a Go or Rust port would bind through cgo — takes **10.2s on the
CPU** and **2.2s on the GPU**, against CTranslate2's 5.7s and MLX's 2.9s for the
same work. The Python that orchestrates all this costs under 0.1s of the 11.9s
above; a full pipeline run and its bare speech-to-text call are the same number
within noise. The lever is the GPU, not the language.

## Choosing a Whisper model

| Model | Size | Speed (M-series, int8) | When |
|---|---|---|---|
| `base.en` | 140 MB | ~15x realtime | quick checks, clean audio |
| `small.en` | 460 MB | ~8x realtime | decent, English only |
| `medium.en` | 1.5 GB | ~3x realtime | good |
| `large-v3-turbo` | 1.6 GB | ~4x realtime | **default** — best with accents |

```bash
localscribe process recording.wav --model small.en
```

Speeds above are for the CPU engine; the `mlx` engine is roughly 3x faster than
each. A one-hour meeting on `large-v3-turbo` is about 15 minutes on the CPU and
about 5 on the GPU.

## Summarizers

- `--backend ollama` (default) — a local LLM. Fully offline.
- `--backend anthropic` — the Claude API. Only the transcript **text** is sent,
  never audio. Needs `ANTHROPIC_API_KEY`.
- `--backend extractive` — no model at all: keyword-ranked sentences plus
  regex-matched action items. Rough, but instant and dependency-free. Also the
  automatic fallback when the chosen backend is unreachable.

Transcripts longer than ~1800 words are summarized map-reduce style: each chunk
is read separately, then the notes are merged.

## Development

```bash
.venv/bin/pytest          # 60 tests, no audio hardware or model downloads
.venv/bin/ruff check .
```

The tests cover the parts that fail silently rather than loudly: speaker
attribution (turn splitting, mic bleed, gain differences, crosstalk), the
recorder's clock-drift handling, engine selection and its fallbacks, and the
summary post-processing.

```
localscribe/
├── localscribe/
│   ├── audio.py        capture, resampling, two-clock alignment
│   ├── engines.py      faster-whisper (CPU) and mlx (Metal GPU), one shape
│   ├── transcribe.py   speech-to-text + per-word speaker attribution
│   ├── summarize.py    ollama / anthropic / extractive backends
│   ├── config.py       env overrides, all optional
│   └── cli.py          record, process, summarize, devices, doctor, list
├── bin/localscribe       launcher: runs the venv's CLI from anywhere
├── scripts/setup.sh    one-command install
└── tests/
```

## Translations

[עברית](README.he.md) · [Español](README.es.md) · [简体中文](README.zh-CN.md) · [Français](README.fr.md)

This English README is the canonical one. Translations are kept in sync by hand,
so if one disagrees with this file, this file is right. Corrections and new
languages are welcome — copy `README.md`, translate the prose, and leave the
commands, paths and flags exactly as they are.

## Before you record other people

A recording captures everyone on the call. Consent rules differ by jurisdiction
and several are two-party-consent — tell the room the recorder is running.
