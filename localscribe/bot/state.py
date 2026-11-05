"""What has happened to each meeting, so a restart neither rejoins nor loses one."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

# recording -> recorded -> done
# Terminal otherwise: empty (nothing said), no_show (never let in), failed.
# A "recorded" entry still has its audio on disk and is retried after a restart.
FINISHED = {"done", "empty", "no_show", "failed"}


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text())

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._data.get(key)
            return dict(entry) if entry else None

    def items(self) -> list[tuple[str, dict]]:
        with self._lock:
            return [(k, dict(v)) for k, v in self._data.items()]

    def set(self, key: str, status: str, **fields) -> None:
        with self._lock:
            entry = self._data.setdefault(key, {})
            entry.update(fields, status=status,
                         updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            self._save()

    def prune(self, days: int = 30) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            self._data = {
                k: v for k, v in self._data.items()
                if v.get("status") not in FINISHED or v.get("updated_at", "") >= cutoff
            }
            self._save()

    def _save(self) -> None:
        # Attendee lists and titles live here, so it is owner-only like the notes.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)
