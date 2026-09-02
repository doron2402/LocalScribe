"""Deleting old recordings.

Audio is the part that both piles up and carries the most exposure — it is
other people's voices, and it is two orders of magnitude larger than the notes
taken from it. So it expires on a timer while the transcripts and summaries,
which are the reason you recorded anything, are kept unless you say otherwise.

Deletion is permanent and there is no undo, so it is deliberately narrow: only
inside LocalScribe's own directories, only files whose extensions it writes,
never through a symlink, and never anything it cannot age.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

# Only these are ever removed, and only from the matching directory.
_CATEGORIES = {
    "audio": {".wav", ".json"},
    "transcripts": {".md", ".json"},
    "summaries": {".md"},
}

DAY = 86_400


@dataclass
class Sweep:
    """What a prune did, or would do."""

    removed: list[Path] = field(default_factory=list)
    bytes_freed: int = 0
    dry_run: bool = False

    @property
    def count(self) -> int:
        return len(self.removed)

    def __bool__(self) -> bool:
        return bool(self.removed)


def _directory(category: str) -> Path:
    return {
        "audio": config.AUDIO_DIR,
        "transcripts": config.TRANSCRIPT_DIR,
        "summaries": config.SUMMARY_DIR,
    }[category]


def policy(overrides: dict[str, int] | None = None) -> dict[str, int]:
    """Days to keep each category. Zero or less means keep forever."""
    days = {
        "audio": config.RETENTION_DAYS,
        "transcripts": config.RETENTION_TRANSCRIPTS,
        "summaries": config.RETENTION_SUMMARIES,
    }
    if overrides:
        days.update({k: v for k, v in overrides.items() if v is not None})
    return days


def expired(category: str, days: int, now: float | None = None) -> list[Path]:
    """Files in one category older than `days`, oldest first."""
    if days <= 0:
        return []
    directory = _directory(category)
    if not directory.is_dir():
        return []

    cutoff = (now if now is not None else time.time()) - days * DAY
    allowed = _CATEGORIES[category]
    out = []
    for path in sorted(directory.iterdir()):
        # A symlink could point anywhere; refuse rather than follow it out of
        # the data directory.
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in allowed:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                out.append(path)
        except OSError:
            continue
    return out


def prune(overrides: dict[str, int] | None = None, dry_run: bool = False,
          now: float | None = None) -> Sweep:
    sweep = Sweep(dry_run=dry_run)
    for category, days in policy(overrides).items():
        for path in expired(category, days, now=now):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if not dry_run:
                try:
                    path.unlink()
                except OSError:
                    continue
            sweep.removed.append(path)
            sweep.bytes_freed += size
    return sweep


def describe(sweep: Sweep) -> str:
    if not sweep:
        return ""
    megabytes = sweep.bytes_freed / 1e6
    verb = "would delete" if sweep.dry_run else "deleted"
    return f"{verb} {sweep.count} file{'s' if sweep.count != 1 else ''} ({megabytes:.1f} MB)"
