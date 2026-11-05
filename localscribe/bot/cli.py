"""`localscribe-bot`: run the scheduler, sign in, attend one call, check on things."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from . import meetings, settings


def cmd_run(args) -> int:
    from .scheduler import Scheduler

    missing = [name for name, value in (
        ("LOCALSCRIBE_BOT_EMAIL", settings.BOT_EMAIL),
        ("LOCALSCRIBE_BOT_DOMAINS", settings.DOMAINS),
        ("LOCALSCRIBE_BOT_DRIVE_FOLDER_ID", settings.DRIVE_FOLDER_ID),
    ) if not value]
    if not settings.CALENDAR_USERS and not settings.ADMIN_EMAIL:
        missing.append("LOCALSCRIBE_BOT_ADMIN_EMAIL or LOCALSCRIBE_BOT_CALENDAR_USERS")
    if missing:
        print(f"Missing settings: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not settings.PROFILE_DIR.exists():
        print("The bot has not signed in yet. Run `localscribe-bot login` first.", file=sys.stderr)
        return 2
    Scheduler().run_forever()
    return 0


def cmd_login(args) -> int:
    """One manual sign-in. Google's challenges can't and shouldn't be scripted."""
    from playwright.sync_api import sync_playwright

    from .meet import CHROME_ARGS

    settings.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(settings.PROFILE_DIR), headless=False, args=CHROME_ARGS,
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://accounts.google.com/")
        print("Sign in as the bot in the browser, then close that window.")
        page.wait_for_event("close", timeout=0)
        ctx.close()
    return 0


def _print_result(done) -> None:
    print(json.dumps({"outcome": done.outcome, "audio": str(done.audio or ""), "detail": done.detail}))


def cmd_attend(args) -> int:
    """Internal: the scheduler's per-meeting process. Last stdout line is its result."""
    from .attend import attend

    _print_result(attend(meetings.Meeting(**json.loads(args.meeting))))
    return 0


def cmd_join(args) -> int:
    """Send the bot to one call right now, and file its note unless told not to."""
    from . import pipeline
    from .attend import attend

    code = "test-page" if args.test_page else meetings.meet_code(args.url)
    if not code:
        print("That is not a meet.google.com room link.", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    m = meetings.Meeting(
        code=code, url=args.url, title=args.title,
        start=now.isoformat(timespec="seconds"),
        end=(now + timedelta(minutes=args.minutes)).isoformat(timespec="seconds"),
        attendees=[e.lower() for e in args.share],
    )
    done = attend(m)
    _print_result(done)
    if done.outcome != "recorded":
        return 1
    if args.no_upload:
        return 0
    status, fields = pipeline.process(m, done.audio)
    print(json.dumps({"status": status, **fields}))
    return 0


def cmd_status(args) -> int:
    from .state import State

    entries = sorted(State(settings.STATE_FILE).items(), key=lambda kv: kv[1].get("updated_at", ""))
    for _, e in entries[-args.limit:]:
        title = (e.get("meeting") or {}).get("title", "?")
        print(f"{e.get('updated_at', '?'):25} {e['status']:10} {title}  {e.get('doc') or e.get('detail', '')}")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)
    parser = argparse.ArgumentParser(prog="localscribe-bot", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="watch calendars and attend meetings").set_defaults(fn=cmd_run)
    sub.add_parser("login", help="sign the bot's browser in to Google").set_defaults(fn=cmd_login)

    sp = sub.add_parser("attend", help=argparse.SUPPRESS)
    sp.add_argument("meeting")
    sp.set_defaults(fn=cmd_attend)

    sp = sub.add_parser("join", help="send the bot to one call now")
    sp.add_argument("url")
    sp.add_argument("--title", default="Ad-hoc meeting")
    sp.add_argument("--minutes", type=int, default=60, help="scheduled length")
    sp.add_argument("--share", action="append", default=[], help="email to share the note with")
    sp.add_argument("--no-upload", action="store_true", help="record only, keep the audio")
    sp.add_argument("--test-page", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(fn=cmd_join)

    sp = sub.add_parser("status", help="what happened to recent meetings")
    sp.add_argument("--limit", type=int, default=25)
    sp.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
