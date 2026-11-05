"""Bot configuration. Environment only, so secrets never land in the repo."""
from __future__ import annotations

import os
from pathlib import Path

from .. import config


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"LOCALSCRIBE_BOT_{name}", default)


def _flag(name: str, default: str) -> bool:
    return _env(name, default).lower() not in ("0", "false", "no", "")


def _list(name: str) -> list[str]:
    return [v.strip().lower() for v in _env(name).split(",") if v.strip()]


# Google. A service account with domain-wide delegation reads calendars and
# writes to Drive; the bot's own Google user is what appears in the meeting.
SERVICE_ACCOUNT_FILE = _env("SERVICE_ACCOUNT_FILE", "/secrets/service-account.json")
BOT_EMAIL = _env("EMAIL").lower()
ADMIN_EMAIL = _env("ADMIN_EMAIL").lower()      # impersonated to list the directory
DOMAINS = _list("DOMAINS")                     # e.g. example.com; who counts as internal
CALENDAR_USERS = _list("CALENDAR_USERS")       # empty -> every user in the directory
DRIVE_FOLDER_ID = _env("DRIVE_FOLDER_ID")      # where notes are filed

# What to join.
OPT_OUT_TAG = _env("OPT_OUT_TAG", "[no-notes]").lower()
SKIP_EXTERNAL = _flag("SKIP_EXTERNAL", "0")
SKIP_ONE_ON_ONE = _flag("SKIP_ONE_ON_ONE", "0")

# Told to the room the moment the bot is in. Empty disables it.
NOTICE = _env(
    "NOTICE",
    "This is an automated notetaker. It is recording this meeting and will file a "
    "summary for the attendees. Remove it from the call if you would rather not "
    "be recorded.",
)

# Timing, in seconds.
POLL_SECONDS = int(_env("POLL_SECONDS", "60"))
JOIN_LEAD_SECONDS = int(_env("JOIN_LEAD_SECONDS", "60"))
ADMIT_TIMEOUT_SECONDS = int(_env("ADMIT_TIMEOUT_SECONDS", "600"))
ALONE_GRACE_SECONDS = int(_env("ALONE_GRACE_SECONDS", "180"))
OVERRUN_SECONDS = int(_env("OVERRUN_SECONDS", "1800"))   # hard stop past scheduled end
MIN_RECORDING_SECONDS = int(_env("MIN_RECORDING_SECONDS", "60"))
MAX_CONCURRENT = int(_env("MAX_CONCURRENT", "4"))

STATE_DIR = config.DATA_DIR / "bot"
STATE_FILE = STATE_DIR / "state.json"
PROFILE_DIR = Path(_env("PROFILE_DIR", str(STATE_DIR / "chrome-profile")))
