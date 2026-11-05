"""Turning calendar events into meetings worth joining. Pure logic, no I/O."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# Event fields come from whoever sent the invite, including people outside the
# company, so the only thing the browser may ever open is a real Meet room.
_MEET_URL = re.compile(r"^https://meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:[/?#].*)?$")


@dataclass
class Meeting:
    code: str
    url: str
    title: str
    start: str   # ISO 8601, UTC
    end: str
    attendees: list[str] = field(default_factory=list)   # everyone who hasn't declined
    external: bool = False

    @property
    def key(self) -> str:
        """Stable across calendars: everyone's copy of an event shares it."""
        return hashlib.sha1(f"{self.code}|{self.start}".encode()).hexdigest()

    @property
    def start_dt(self) -> datetime:
        return datetime.fromisoformat(self.start)

    @property
    def end_dt(self) -> datetime:
        return datetime.fromisoformat(self.end)

    def to_dict(self) -> dict:
        return asdict(self)


def meet_url(event: dict) -> str | None:
    """The event's Meet link, or None if it has no genuine one."""
    candidates = [event.get("hangoutLink")]
    entry_points = (event.get("conferenceData") or {}).get("entryPoints") or []
    candidates += [e.get("uri") for e in entry_points if e.get("entryPointType") == "video"]
    for uri in candidates:
        if isinstance(uri, str) and _MEET_URL.match(uri):
            return uri
    return None


def meet_code(url: str) -> str | None:
    m = _MEET_URL.match(url)
    return m.group(1) if m else None


def _utc(when: dict | None) -> str | None:
    """All-day events have only a date and are never meetings, so they return None."""
    value = (when or {}).get("dateTime")
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def parse_event(
    event: dict,
    domains: list[str],
    bot_email: str = "",
    opt_out_tag: str = "[no-notes]",
) -> Meeting | None:
    """A Meeting if this event should be attended, else None.

    Skipped: cancelled, all-day, private and opted-out events, events with no
    Meet room, anything the calendar's owner declined, and anything with fewer
    than two people. The last rule matters more than it looks: plenty of solo
    calendar blocks carry an auto-added Meet link.
    """
    if event.get("status") == "cancelled" or event.get("visibility") in ("private", "confidential"):
        return None

    title = (event.get("summary") or "").strip()
    text = f"{title}\n{event.get('description') or ''}".lower()
    if opt_out_tag and opt_out_tag in text:
        return None

    url = meet_url(event)
    start, end = _utc(event.get("start")), _utc(event.get("end"))
    if not url or not start or not end:
        return None

    people = []
    for a in event.get("attendees") or []:
        email = (a.get("email") or "").lower()
        if a.get("resource") or not email or email == bot_email:
            continue
        if a.get("self") and a.get("responseStatus") == "declined":
            return None
        if a.get("responseStatus") != "declined":
            people.append(email)
    if len(people) < 2:
        return None

    return Meeting(
        code=meet_code(url), url=url, title=title or "Untitled meeting",
        start=start, end=end, attendees=sorted(set(people)),
        external=any(_domain(p) not in domains for p in people),
    )


def merge(meetings: list[Meeting]) -> list[Meeting]:
    """One Meeting per real call, with attendees pooled across everyone's copy."""
    merged: dict[str, Meeting] = {}
    for m in meetings:
        have = merged.get(m.key)
        if have is None:
            merged[m.key] = m
        else:
            have.attendees = sorted(set(have.attendees) | set(m.attendees))
            have.external = have.external or m.external
    return sorted(merged.values(), key=lambda m: (m.start, m.code))


def wanted(m: Meeting, skip_external: bool = False, skip_one_on_one: bool = False) -> bool:
    if skip_external and m.external:
        return False
    if skip_one_on_one and len(m.attendees) <= 2:
        return False
    return True


def due(m: Meeting, now: datetime, lead_seconds: int = 60) -> bool:
    """In the join window: from just before the start until the scheduled end."""
    return (m.start_dt - now).total_seconds() <= lead_seconds and now < m.end_dt


def internal_people(m: Meeting, domains: list[str]) -> list[str]:
    return [p for p in m.attendees if _domain(p) in domains]


def slug(text: str, limit: int = 60) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].rstrip("-")) or "meeting"
