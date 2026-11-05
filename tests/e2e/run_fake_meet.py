"""End to end inside the bot image: real Chromium, PulseAudio and Whisper, fake Meet
and fake Drive. Usage, with ana.wav and ben.wav in the mounted page directory:

    docker run --rm --shm-size=2g -v <dir>:/page \\
      -e LOCALSCRIBE_BOT_DOMAINS=example.com -e LOCALSCRIBE_BOT_MIN_RECORDING_SECONDS=5 \\
      -e LOCALSCRIBE_BOT_PROFILE_DIR=/tmp/none -e LOCALSCRIBE_WHISPER_MODEL=base.en \\
      <image> exec python /page/run_fake_meet.py
"""
import functools
import http.server
import json
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import soundfile as sf

from localscribe import config
from localscribe.bot import meetings, pipeline
from localscribe.bot.attend import attend, speakers_path
from localscribe.summarize import _extractive  # noqa: F401  (fails early if the install is broken)

notices = []


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        notices.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


class FakeDrive:
    def __init__(self):
        self.uploads, self.shares = [], []

    def _resp(self, body):
        return SimpleNamespace(status_code=200, content=b"x", json=lambda: body,
                               request=SimpleNamespace(method="X", url="u"))

    def get(self, url, params=None):
        return self._resp({"files": []})

    def post(self, url, params=None, json=None, data=None, headers=None):
        if data:
            self.uploads.append(data.decode())
        if json and "emailAddress" in json:
            self.shares.append(json["emailAddress"])
        return self._resp({"id": "doc", "webViewLink": "https://docs.example/doc"})


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail if not ok else ''}", flush=True)
    return ok


def main() -> int:
    handler = functools.partial(Handler, directory="/page")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8099), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    now = datetime.now(timezone.utc)
    m = meetings.Meeting(
        code="abc-defg-hij", url="http://127.0.0.1:8099/fake_meet.html", title="Launch Review",
        start=now.isoformat(timespec="seconds"),
        end=(now + timedelta(minutes=5)).isoformat(timespec="seconds"),
        attendees=["ana@example.com", "guest@other.com"], external=True,
    )
    done = attend(m)
    results = [check("joined and recorded", done.outcome == "recorded", done.detail)]
    if done.outcome != "recorded":
        return 1
    results.append(check("recording notice posted in chat", len(notices) == 1, notices))

    timeline = json.loads(speakers_path(done.audio).read_text())
    print("timeline:", timeline)
    print("wav seconds:", sf.info(str(done.audio)).duration)

    drive = FakeDrive()
    status, fields = pipeline.process(m, done.audio, http=drive, backend="extractive")
    print("status:", status, json.dumps(fields))
    transcript = next(config.TRANSCRIPT_DIR.glob("*.md")).read_text()
    print("--- transcript\n" + transcript)
    print("--- uploaded note\n" + (drive.uploads[0] if drive.uploads else "(none)"))

    lowered = transcript.lower()
    results += [
        check("note filed", status == "done"),
        check("speech came through the browser", "migration" in lowered and "friday" in lowered),
        check("first speaker owns her opening words", "Ana Rivera: We agreed" in transcript),
        check("second speaker owns his commitment",
              re.search(r"Ben Cohen: (Sounds good, )?I will take ownership", transcript) is not None),
        check("nobody left unnamed", "Speaker:" not in transcript),
        check("note uploaded as a doc", len(drive.uploads) == 1 and "Launch Review" in drive.uploads[0]),
        check("shared with colleague only", drive.shares == ["ana@example.com"], drive.shares),
        check("audio deleted after filing", not Path(done.audio).exists()),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
