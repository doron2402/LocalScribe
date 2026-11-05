"""One meeting, start to finish, as one process: join, record, leave."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from .. import config
from . import capture, meetings, settings
from .meet import MeetTab
from .speakers import compress

log = logging.getLogger("localscribe.bot")


@dataclass
class Ended:
    outcome: str        # recorded | no_show
    audio: Path | None = None
    detail: str = ""


class AloneTimer:
    """Decides when to go. Unknown counts never trigger it: the UI may just have changed."""

    def __init__(self, grace: float):
        self.grace, self._since = grace, None

    def expired(self, people: int | None, now: float) -> bool:
        if people is None or people > 1:
            self._since = None
            return False
        if self._since is None:
            self._since = now
        return now - self._since >= self.grace


def audio_stem(m: meetings.Meeting) -> str:
    return f"{meetings.slug(m.title)}_{m.start_dt:%Y-%m-%d_%H%M}_{m.key[:8]}"


def audio_path(m: meetings.Meeting) -> Path:
    return config.AUDIO_DIR / f"{audio_stem(m)}.wav"


def speakers_path(audio: Path) -> Path:
    return Path(audio).with_suffix(".speakers.json")


def align(events: list[tuple[float, str]], stopped_at: float, wav_seconds: float) -> list[tuple[float, str]]:
    """Unix times -> seconds into the WAV.

    The recorder takes an unknown moment to start producing audio, so the WAV's
    own length, counted back from when it stopped, is the only trustworthy
    anchor. Anything said before the file began counts as its first instant.
    """
    start = stopped_at - wav_seconds
    return [(round(max(at - start, 0.0), 2), name) for at, name in events]


def _stay(m: meetings.Meeting, tab, rec, hard_stop: float) -> list[tuple[float, str]]:
    """Sit in the call until it's over. -> speaker changes, in unix time."""
    events = []
    alone = AloneTimer(settings.ALONE_GRACE_SECONDS)
    reason = "scheduled limit"
    while time.time() < hard_stop:
        time.sleep(1)
        events += tab.drain_speakers()
        state = tab.status()
        if state != "in_call":
            reason = state
            break
        if alone.expired(tab.people_count(), time.monotonic()):
            reason = "alone"
            break
        if not rec.alive():
            reason = "recorder stopped"
            log.error("%s: the audio recorder died mid-call", m.code)
            break
    events += tab.drain_speakers()
    tab.leave()
    log.info("%s: left (%s)", m.code, reason)
    return events


def attend(m: meetings.Meeting, tab_factory=MeetTab) -> Ended:
    config.ensure_dirs()
    audio = audio_path(m)
    hard_stop = m.end_dt.timestamp() + settings.OVERRUN_SECONDS

    with capture.Sink(capture.sink_name(m.key)) as sink:
        tab = tab_factory(settings.PROFILE_DIR, sink.name)
        rec = capture.Recording(sink.name, audio)
        try:
            tab.open()
            state = tab.join(m.url, settings.ADMIT_TIMEOUT_SECONDS)
            if state != "in_call":
                return Ended("no_show", detail=f"could not join: {state}")

            started = datetime.now(timezone.utc)
            rec.start()
            tab.watch_speakers()
            if not tab.post_notice(settings.NOTICE):
                log.warning("%s: could not post the recording notice in chat", m.code)
            if not tab.enable_captions():
                log.warning("%s: captions unavailable; speakers will not be named", m.code)
            events = _stay(m, tab, rec, hard_stop)
        finally:
            stopped_at = time.time()
            rec.stop()
            tab.close()

    timeline = align(events, stopped_at, sf.info(str(audio)).duration)
    speakers_path(audio).write_text(json.dumps(compress(timeline)))
    audio.with_suffix(".json").write_text(json.dumps({
        "label": m.title,
        "started_at": started.isoformat(timespec="seconds"),
        "channel_roles": ["them"],
        "system_audio": "meeting-bot",
    }, indent=2))
    return Ended("recorded", audio)
