"""The long-running part: watch calendars, send a process to each meeting."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import google_api, meetings, pipeline, settings
from .attend import audio_path
from .state import State

log = logging.getLogger("localscribe.bot")


def calendar_users() -> list[str]:
    if settings.CALENDAR_USERS:
        return settings.CALENDAR_USERS
    http = google_api.session(settings.SERVICE_ACCOUNT_FILE, settings.ADMIN_EMAIL,
                              google_api.DIRECTORY_SCOPE)
    users: list[str] = []
    for domain in settings.DOMAINS:
        users += google_api.list_directory_users(http, domain)
    return users


def upcoming(users: list[str], now: datetime) -> list[meetings.Meeting]:
    found = []
    until = now + timedelta(seconds=settings.JOIN_LEAD_SECONDS + settings.POLL_SECONDS)
    for user in users:
        try:
            http = google_api.session(settings.SERVICE_ACCOUNT_FILE, user, google_api.CALENDAR_SCOPE)
            events = google_api.list_events(http, user, now, until)
        except Exception as e:
            # One person's calendar must not cost everyone else their meetings.
            log.warning("could not read a calendar: %s", type(e).__name__)
            continue
        for event in events:
            m = meetings.parse_event(event, settings.DOMAINS, settings.BOT_EMAIL, settings.OPT_OUT_TAG)
            if m:
                found.append(m)
    return meetings.merge(found)


def select(found: list[meetings.Meeting], state: State, now: datetime) -> list[meetings.Meeting]:
    return [
        m for m in found
        if state.get(m.key) is None
        and meetings.due(m, now, settings.JOIN_LEAD_SECONDS)
        and meetings.wanted(m, settings.SKIP_EXTERNAL, settings.SKIP_ONE_ON_ONE)
    ]


class Scheduler:
    def __init__(self, state: State | None = None):
        self.state = state or State(settings.STATE_FILE)
        self.slots = threading.Semaphore(settings.MAX_CONCURRENT)
        # Whisper uses every core it can get, so transcribe one meeting at a time.
        self.transcriber = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcribe")

    def recover(self) -> None:
        """Finish what a restart interrupted. A recording with no live browser is over."""
        for key, entry in self.state.items():
            if entry["status"] not in ("recording", "recorded"):
                continue
            audio = entry.get("audio")
            if audio and Path(audio).exists():
                self._enqueue(key, entry)
            else:
                self.state.set(key, "failed", detail="interrupted before audio was saved")

    def _enqueue(self, key: str, entry: dict) -> None:
        self.state.set(key, "recorded", audio=entry["audio"])
        self.transcriber.submit(self._process, key, entry)

    def _process(self, key: str, entry: dict) -> None:
        m = meetings.Meeting(**entry["meeting"])
        try:
            status, fields = pipeline.process(m, Path(entry["audio"]))
            self.state.set(key, status, **fields)
            log.info("%s: %s", m.code, status)
        except Exception as e:
            # Audio stays on disk; recover() retries it after the next restart.
            log.exception("%s: processing failed", m.code)
            self.state.set(key, "recorded", detail=type(e).__name__)

    def _attend(self, m: meetings.Meeting) -> None:
        limit = ((m.end_dt - datetime.now(timezone.utc)).total_seconds()
                 + settings.OVERRUN_SECONDS + settings.ADMIT_TIMEOUT_SECONDS + 120)
        try:
            done = subprocess.run(
                [sys.executable, "-m", "localscribe.bot.cli", "attend", json.dumps(m.to_dict())],
                stdout=subprocess.PIPE, text=True, timeout=limit,
            )
            lines = done.stdout.strip().splitlines()
            result = json.loads(lines[-1]) if lines else {}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            result = {"outcome": "failed", "detail": type(e).__name__}
        finally:
            self.slots.release()

        if result.get("outcome") == "recorded":
            self._enqueue(m.key, {"meeting": m.to_dict(), "audio": result["audio"]})
        elif result.get("outcome") == "no_show":
            self.state.set(m.key, "no_show", detail=result.get("detail", ""))
            log.warning("%s: %s", m.code, result.get("detail", "no show"))
        else:
            self.state.set(m.key, "failed", detail=result.get("detail", "the join process crashed"))
            log.error("%s: the join process failed", m.code)

    def tick(self, users: list[str], now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        for m in select(upcoming(users, now), self.state, now):
            if not self.slots.acquire(blocking=False):
                log.warning("%s: at the %d-meeting limit; will retry next tick", m.code,
                            settings.MAX_CONCURRENT)
                continue
            self.state.set(m.key, "recording", meeting=m.to_dict(), audio=str(audio_path(m)))
            threading.Thread(target=self._attend, args=(m,), daemon=True).start()
            log.info("%s: joining", m.code)

    def run_forever(self) -> None:
        self.recover()
        users, refreshed = [], 0.0
        while True:
            try:
                if time.monotonic() - refreshed > 3600 or not users:
                    users, refreshed = calendar_users(), time.monotonic()
                self.tick(users)
                self.state.prune()
            except Exception:
                log.exception("scheduler tick failed")
            time.sleep(settings.POLL_SECONDS)
