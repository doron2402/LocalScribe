"""From a finished recording to a note in Drive."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .. import retention
from . import google_api, meetings, settings
from .attend import speakers_path
from .speakers import lookup

log = logging.getLogger("localscribe.bot")


def note_markdown(m: meetings.Meeting, summary: str) -> str:
    who = ", ".join(m.attendees)
    return (f"# {m.title}\n\n**Date:** {m.start_dt:%Y-%m-%d %H:%M} UTC\n\n"
            f"**Invited:** {who}\n\n{summary.strip()}\n")


def readers_for(m: meetings.Meeting) -> list[str]:
    """Colleagues who were invited. External guests are never auto-shared notes."""
    return meetings.internal_people(m, settings.DOMAINS)


def summarize_recording(audio: Path, backend: str | None = None):
    """-> (transcript, summary); summary is None when nothing was said."""
    from ..summarize import SummaryError, summarize
    from ..transcribe import transcribe, write_transcript

    path = speakers_path(audio)
    timeline = json.loads(path.read_text()) if path.exists() else []
    t = transcribe(audio, speaker_of=lookup([tuple(e) for e in timeline]))
    write_transcript(t)
    if not t.segments:
        return t, None
    try:
        return t, summarize(t, backend=backend)
    except SummaryError as e:
        # Better a rough note than no note: the point is that nothing gets lost.
        log.warning("summarizer failed (%s); using the extractive fallback", e)
        return t, summarize(t, backend="extractive")


def discard(audio: Path) -> None:
    retention.delete_recording(audio)
    speakers_path(audio).unlink(missing_ok=True)


def process(m: meetings.Meeting, audio: Path, http=None, backend: str | None = None) -> tuple[str, dict]:
    """-> (status, fields). Audio is only deleted once the note is safely in Drive."""
    from ..summarize import write_summary

    audio = Path(audio)
    transcript, summary = summarize_recording(audio, backend)
    if transcript.duration < settings.MIN_RECORDING_SECONDS or summary is None:
        discard(audio)
        return "empty", {"duration": transcript.duration}

    write_summary(transcript, summary, backend or "bot")
    http = http or google_api.session(settings.SERVICE_ACCOUNT_FILE, settings.BOT_EMAIL,
                                      google_api.DRIVE_SCOPE)
    doc = google_api.file_note(
        http, settings.DRIVE_FOLDER_ID, m.key, m.title,
        datetime.fromisoformat(m.start), note_markdown(m, summary), readers_for(m),
    )
    discard(audio)
    return "done", {"doc": doc.get("webViewLink", doc["id"]), "duration": transcript.duration}
