"""Naming speakers. The bot hears one mixed stream, so Whisper can't tell people
apart; Meet's captions can, so they supply who was talking and when."""
from __future__ import annotations

UNKNOWN = "Speaker"


def compress(events: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Keep only the moments the speaker changed."""
    out: list[tuple[float, str]] = []
    for at, name in sorted(events, key=lambda e: e[0]):
        name = name.strip()
        if name and (not out or out[-1][1] != name):
            out.append((at, name))
    return out


def speaker_at(timeline: list[tuple[float, str]], start: float, end: float) -> str:
    """Whoever held the floor longest during [start, end]. Nobody is guessed at."""
    totals: dict[str, float] = {}
    for i, (at, name) in enumerate(timeline):
        until = timeline[i + 1][0] if i + 1 < len(timeline) else float("inf")
        overlap = min(end, until) - max(start, at)
        if overlap > 0:
            totals[name] = totals.get(name, 0.0) + overlap
    return max(totals, key=totals.get) if totals else UNKNOWN


def lookup(timeline: list[tuple[float, str]]):
    """A speaker_of(start, end) function for transcribe()."""
    timeline = compress(timeline)
    return lambda start, end: speaker_at(timeline, start, end)
