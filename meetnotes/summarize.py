"""Summarization backends.

ollama     — a local LLM, nothing leaves the machine (the default)
anthropic  — the Claude API; only the *text* is sent, never the audio
extractive — no model at all: keyword scoring + action-item patterns
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .transcribe import Transcript

CHUNK_WORDS = 1800
CHUNK_OVERLAP = 150

SYSTEM_PROMPT = (
    "You summarize meeting transcripts. The transcript comes from automatic "
    "speech recognition, so expect misheard words, and speaker labels ('You', "
    "'Them') that are occasionally wrong — a trailing '?' marks a guess. "
    "Never invent facts, names, numbers or commitments that are not in the "
    "transcript. If something is unclear, say so instead of guessing."
)

SUMMARY_TEMPLATE = """Summarize this meeting transcript.

Meeting: {label}
Date: {started_at}
Duration: {duration}

Write GitHub-flavored markdown with exactly these sections, in this order:

## TL;DR
Two or three sentences on what this meeting was actually about and where it landed.

## Key points
Bullets. Substance only — skip pleasantries and scheduling chatter.

## Decisions
What was actually decided. Write "None recorded." if nothing was.

## Action items
One bullet each, as `- [ ] **owner** — task (due date if mentioned)`. Use the
speaker label as the owner when no name is given. Write "None recorded." if none.

## Open questions
Things left unresolved. Write "None recorded." if none.

Transcript:
---
{transcript}
---"""

CHUNK_TEMPLATE = """This is part {i} of {n} of a meeting transcript. Extract only
what is in this part, as terse bullets under these headings: Points, Decisions,
Actions, Questions. No preamble.

---
{transcript}
---"""

REDUCE_TEMPLATE = """Below are notes taken from consecutive parts of one meeting.
Merge them into a single summary. Deduplicate, drop anything trivial, and keep
the chronological sense of how the discussion moved.

Meeting: {label}
Date: {started_at}
Duration: {duration}

Use exactly these sections: ## TL;DR, ## Key points, ## Decisions,
## Action items (as `- [ ] **owner** — task`), ## Open questions.
Write "None recorded." under any section with nothing in it.

Notes:
---
{notes}
---"""


_BULLET_RE = re.compile(r"^\s*(?:[-*\u2022]|\d+\.)\s+(.*)$")


def normalize_actions(summary: str) -> str:
    """Force the action list into checkboxes.

    Smaller local models follow the section headings reliably but drift on the
    bullet format, so the shape is fixed here rather than by re-prompting.
    """
    out, in_actions = [], False
    for line in summary.splitlines():
        if line.strip().startswith("#"):
            in_actions = "action item" in line.lower()
            out.append(line)
            continue
        m = _BULLET_RE.match(line) if in_actions else None
        if not m:
            out.append(line)
            continue
        body = m.group(1).strip()
        if body.startswith("[ ]") or body.startswith("[x]"):
            out.append(f"- {body}")
            continue
        # "Owner: task" / "Owner - task" -> "- [ ] **Owner** — task"
        owner = re.match(r"^\*{0,2}([A-Z][\w .'-]{0,28}?)\*{0,2}\s*[:\u2014-]\s+(.*)$", body)
        if owner:
            body = f"**{owner.group(1).strip()}** \u2014 {owner.group(2).strip()}"
        out.append(f"- [ ] {body}")
    return "\n".join(out)


class SummaryError(RuntimeError):
    pass


# ---------------------------------------------------------------- backends


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SummaryError(f"{url} returned {e.code}: {e.read().decode()[:400]}") from e
    except urllib.error.URLError as e:
        raise SummaryError(f"Could not reach {url}: {e.reason}") from e


def ollama_available() -> bool:
    try:
        with urllib.request.urlopen(f"{config.OLLAMA_HOST}/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{config.OLLAMA_HOST}/api/tags", timeout=3) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return []


def _ollama(prompt: str, model: str) -> str:
    data = _post_json(
        f"{config.OLLAMA_HOST}/api/chat",
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": 8192},
        },
        headers={},
    )
    return data["message"]["content"].strip()


def _anthropic(prompt: str, model: str) -> str:
    if not config.ANTHROPIC_API_KEY:
        raise SummaryError("ANTHROPIC_API_KEY is not set.")
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "model": model,
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    return "".join(b.get("text", "") for b in data["content"]).strip()


# ------------------------------------------------------- extractive fallback

_ACTION_RE = re.compile(
    r"\b(i'?ll|we'?ll|i will|we will|let'?s|can you|could you|please|"
    r"need to|needs to|going to|gonna|action item|follow up|follow-up|"
    r"take care of|by (monday|tuesday|wednesday|thursday|friday|tomorrow|"
    r"next week|end of (day|week)))\b",
    re.I,
)
_QUESTION_RE = re.compile(r"\?\s*$")
_DECISION_RE = re.compile(
    r"\b(we (decided|agreed|are going with|will go with)|decision is|"
    r"let'?s go with|final(ly|ized)? on|settled on|sounds good, let'?s)\b", re.I
)
_STOPWORDS = set(
    "a an the and or but if then than that this these those is are was were be "
    "been being am do does did doing have has had having i you he she it we they "
    "me him her us them my your his its our their of in on at to for with from by "
    "about as into like through after over between out up down off so just very "
    "can could should would will shall may might must not no yes ok okay yeah "
    "right sure think know really actually basically kind sort thing things stuff "
    "gonna wanna get got go going say said see want need one two also well now "
    "here there what when where who how why".split()
)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip().split()) >= 4]


def _extractive(t: Transcript) -> str:
    sentences_by_speaker = [
        (seg.speaker, s) for seg in t.segments for s in _sentences(seg.text)
    ]
    sentences = [s for _, s in sentences_by_speaker]
    if not sentences:
        return "## TL;DR\n\nNothing intelligible was transcribed.\n"

    freq: dict[str, int] = {}
    for s in sentences:
        for w in re.findall(r"[a-z][a-z'-]+", s.lower()):
            if w not in _STOPWORDS and len(w) > 2:
                freq[w] = freq.get(w, 0) + 1
    if freq:
        peak = max(freq.values())
        freq = {w: c / peak for w, c in freq.items()}

    def score(s: str) -> float:
        words = re.findall(r"[a-z][a-z'-]+", s.lower())
        if not words:
            return 0.0
        return sum(freq.get(w, 0.0) for w in words) / (len(words) ** 0.5)

    ranked = sorted(range(len(sentences)), key=lambda i: score(sentences[i]), reverse=True)
    top = sorted(ranked[: min(8, len(sentences))])

    actions = [f"- [ ] **{sp}** — {s}" for sp, s in sentences_by_speaker if _ACTION_RE.search(s)]
    decisions = [f"- {s}" for _, s in sentences_by_speaker if _DECISION_RE.search(s)]
    questions = [f"- {s}" for _, s in sentences_by_speaker if _QUESTION_RE.search(s)]

    def section(title: str, items: list[str], limit: int) -> str:
        body = "\n".join(items[:limit]) if items else "None recorded."
        return f"## {title}\n\n{body}\n"

    return "\n".join([
        "## TL;DR\n",
        " ".join(sentences[i] for i in top[:3]) + "\n",
        section("Key points", [f"- {sentences[i]}" for i in top], 8),
        section("Decisions", decisions, 6),
        section("Action items", actions, 12),
        section("Open questions", questions, 6),
        "\n> Extractive summary (no language model). Sentences are quoted "
        "verbatim from the transcript, so read them as raw material, not prose.\n",
    ])


# ------------------------------------------------------------------- driver


def _chunks(text: str, size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP):
    words = text.split()
    if len(words) <= size:
        return [text]
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i: i + size]))
        i += size - overlap
    return out


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


def summarize(
    t: Transcript,
    backend: str | None = None,
    model: str | None = None,
    on_progress=None,
) -> str:
    backend = backend or config.SUMMARY_BACKEND

    if backend == "extractive":
        return _extractive(t)

    if backend == "ollama":
        model = model or config.OLLAMA_MODEL

        def call(prompt: str) -> str:
            return _ollama(prompt, model)

        if not ollama_available():
            raise SummaryError(
                f"Ollama is not running at {config.OLLAMA_HOST}. "
                "Start it with `ollama serve`, or pass --backend extractive."
            )
    elif backend == "anthropic":
        model = model or config.ANTHROPIC_MODEL

        def call(prompt: str) -> str:
            return _anthropic(prompt, model)

    else:
        raise SummaryError(f"Unknown backend '{backend}'.")

    meta = {
        "label": t.label,
        "started_at": t.started_at or "unknown",
        "duration": _fmt_duration(t.duration),
    }
    body = t.text
    parts = _chunks(body)

    if len(parts) == 1:
        if on_progress:
            on_progress("summarizing", 1, 1)
        return normalize_actions(call(SUMMARY_TEMPLATE.format(transcript=body, **meta)))

    notes = []
    for i, part in enumerate(parts, 1):
        if on_progress:
            on_progress("reading", i, len(parts))
        notes.append(call(CHUNK_TEMPLATE.format(i=i, n=len(parts), transcript=part)))
    if on_progress:
        on_progress("merging", len(parts), len(parts))
    return normalize_actions(call(REDUCE_TEMPLATE.format(notes="\n\n".join(notes), **meta)))


def write_summary(t: Transcript, summary: str, backend: str, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or config.SUMMARY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{Path(t.audio_path).stem}.md"
    header = [
        f"# {t.label}",
        "",
        f"- Date: {t.started_at or 'unknown'}",
        f"- Duration: {_fmt_duration(t.duration)}",
        f"- Summarized by: {backend}",
        "",
        "---",
        "",
    ]
    path.write_text("\n".join(header) + summary.strip() + "\n")
    return path
