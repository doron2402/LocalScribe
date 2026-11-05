"""Driving a Google Meet tab. Everything brittle about Meet's UI is in this file."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path

CHROME_ARGS = [
    "--use-fake-ui-for-media-stream",   # answers the permission prompts; no fake devices
    "--autoplay-policy=no-user-gesture-required",
    "--disable-background-timer-throttling",   # the speaker watcher runs on a timer
    "--password-store=basic",           # so a copied profile keeps its sign-in
    "--disable-blink-features=AutomationControlled",
]

# Meet's wording, lowercased. These are what breaks when Google changes the UI.
_ENDED = (
    "you left the meeting", "the call ended", "you've been removed", "you were removed",
    "your host ended the meeting", "the meeting has ended", "returned to home screen",
)
_REFUSED = (
    "you can't join this video call", "denied your request", "someone in the call denied",
    "no one responded to your request", "you aren't allowed to join",
    "this meeting code doesn't exist", "check your meeting code", "invalid video call name",
)
_JOIN = re.compile(r"^(join now|ask to join|join anyway|switch here)$", re.I)
_LEAVE = re.compile(r"^leave call$", re.I)
_MUTE = re.compile(r"^turn off (microphone|camera)", re.I)
_DISMISS = re.compile(r"^(got it|dismiss|continue without microphone and camera)$", re.I)
_CHAT = re.compile("chat with everyone", re.I)


def classify(text: str, in_call: bool) -> str:
    """-> in_call | ended | refused | waiting. Banners beat stale in-call controls."""
    lowered = text.lower()
    if any(p in lowered for p in _REFUSED):
        return "refused"
    if any(p in lowered for p in _ENDED):
        return "ended"
    return "in_call" if in_call else "waiting"


def parse_people_count(label: str | None) -> int | None:
    """Reads the count off the People button, e.g. 'People - 3 joined' or '3'."""
    numbers = re.findall(r"\d+", label or "")
    return int(numbers[0]) if numbers else None


# Runs in the page and timestamps there, because anything slow on the Python side
# would otherwise shift who gets credited with what. Each caption row names its
# speaker on its first line.
_WATCH_SPEAKERS_JS = r"""
() => {
  if (window.__speakers) return;
  window.__speakers = [];
  let last = null;
  setInterval(() => {
    const region = document.querySelector('[role="region"][aria-label*="aption" i]');
    if (!region) return;
    const rows = [...region.children].filter(r => r.innerText && r.innerText.includes('\n'));
    if (!rows.length) return;
    const name = rows[rows.length - 1].innerText.split('\n')[0].trim();
    if (name && name !== last) {
      last = name;
      window.__speakers.push([Date.now() / 1000, name]);
    }
  }, 100);
}
"""
_DRAIN_SPEAKERS_JS = "() => (window.__speakers || []).splice(0)"


def _browser_env(sink: str) -> dict:
    keep = ("DISPLAY", "HOME", "PATH", "XDG_RUNTIME_DIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PULSE_SINK"] = sink
    return env


class MeetTab:
    def __init__(self, profile: Path, sink: str, display_name: str = "Notetaker"):
        self.profile, self.sink, self.display_name = Path(profile), sink, display_name
        self._pw = self._context = self.page = self._copy = None

    def open(self) -> None:
        from playwright.sync_api import sync_playwright

        # A Chromium profile can't be open twice, and meetings overlap.
        self._copy = Path(tempfile.mkdtemp(prefix="meet-profile-"))
        if self.profile.exists():
            shutil.copytree(self.profile, self._copy, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("Singleton*", "*Cache*", "lockfile"))
        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            str(self._copy), headless=False, args=CHROME_ARGS,
            env=_browser_env(self.sink),
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1280, "height": 800},
        )
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def close(self) -> None:
        for closer in (lambda: self._context and self._context.close(),
                       lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        if self._copy:
            shutil.rmtree(self._copy, ignore_errors=True)

    # ------------------------------------------------------------- joining

    def _click_all(self, pattern: re.Pattern) -> None:
        buttons = self.page.get_by_role("button", name=pattern)
        for i in range(buttons.count()):
            try:
                buttons.nth(i).click(timeout=1500)
            except Exception:
                pass

    def in_call(self) -> bool:
        return self.page.get_by_role("button", name=_LEAVE).count() > 0

    def status(self) -> str:
        try:
            text = self.page.locator("body").inner_text(timeout=5000)
            return classify(text, self.in_call())
        except Exception as e:
            # A closed tab is an ended call. Anything else is just a slow render.
            return "ended" if "closed" in str(e).lower() else "waiting"

    def join(self, url: str, admit_timeout: float) -> str:
        """-> in_call | refused | ended | timeout. Audio must not start before in_call."""
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        name_box = self.page.get_by_role("textbox", name=re.compile("your name", re.I))
        asked = False
        deadline = time.monotonic() + admit_timeout
        while time.monotonic() < deadline:
            self._click_all(_DISMISS)
            state = self.status()
            if state != "waiting":
                return state
            if not asked:
                self._click_all(_MUTE)
                if name_box.count():
                    name_box.first.fill(self.display_name)
                join = self.page.get_by_role("button", name=_JOIN)
                if join.count():
                    join.first.click(timeout=3000)
                    asked = True
            time.sleep(1)
        return "timeout"

    # ------------------------------------------------------------ in the call

    def post_notice(self, message: str) -> bool:
        """Tell the room it is being recorded. -> whether it went out."""
        if not message:
            return True
        try:
            self.page.get_by_role("button", name=_CHAT).first.click(timeout=5000)
            box = self.page.get_by_role("textbox", name=re.compile("send a message", re.I)).first
            box.fill(message, timeout=5000)
            box.press("Enter")
            self.page.get_by_role("button", name=_CHAT).first.click(timeout=3000)
            return True
        except Exception:
            return False

    def enable_captions(self) -> bool:
        """Captions are local to this tab: other people don't see them switch on."""
        try:
            self.page.get_by_role("button", name=re.compile("turn on captions", re.I)).first.click(timeout=5000)
            return True
        except Exception:
            return False

    def watch_speakers(self) -> None:
        self.page.evaluate(_WATCH_SPEAKERS_JS)

    def drain_speakers(self) -> list[tuple[float, str]]:
        """Speaker changes since the last call, as (unix time, name)."""
        try:
            return [(at, name) for at, name in self.page.evaluate(_DRAIN_SPEAKERS_JS)]
        except Exception:
            return []

    def people_count(self) -> int | None:
        try:
            button = self.page.get_by_role("button", name=re.compile(r"^(people|show everyone)", re.I)).first
            return parse_people_count(button.get_attribute("aria-label", timeout=1000))
        except Exception:
            return None

    def leave(self) -> None:
        try:
            self.page.get_by_role("button", name=_LEAVE).first.click(timeout=3000)
        except Exception:
            pass
