"""The bot's decisions, with no browser, audio device or Google account."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from localscribe.bot import google_api, meetings, speakers
from localscribe.bot.attend import AloneTimer, align
from localscribe.bot.meet import classify, parse_people_count
from localscribe.bot.state import State
from localscribe.transcribe import _split_by_timeline

DOMAINS = ["example.com"]
URL = "https://meet.google.com/abc-defg-hij"


def event(**over):
    base = {
        "status": "confirmed", "summary": "Standup", "hangoutLink": URL,
        "start": {"dateTime": "2026-11-05T09:00:00-05:00"},
        "end": {"dateTime": "2026-11-05T09:30:00-05:00"},
        "attendees": [{"email": "a@example.com", "self": True, "responseStatus": "accepted"},
                      {"email": "b@example.com", "responseStatus": "needsAction"}],
    }
    return {**base, **over}


def test_parses_a_normal_meeting_to_utc():
    m = meetings.parse_event(event(), DOMAINS)
    assert m.code == "abc-defg-hij"
    assert m.start == "2026-11-05T14:00:00+00:00"
    assert m.attendees == ["a@example.com", "b@example.com"]
    assert not m.external


@pytest.mark.parametrize("over", [
    {"status": "cancelled"},
    {"visibility": "private"},
    {"summary": "Board prep [no-notes]"},
    {"description": "please no recording [NO-NOTES]"},
    {"hangoutLink": None},
    {"start": {"date": "2026-11-05"}},
    {"attendees": [{"email": "a@example.com", "self": True, "responseStatus": "accepted"}]},
    {"attendees": []},
    {"attendees": [{"email": "a@example.com", "self": True, "responseStatus": "declined"},
                   {"email": "b@example.com", "responseStatus": "accepted"}]},
], ids=["cancelled", "private", "tag-title", "tag-desc", "no-link", "all-day", "solo", "empty", "declined"])
def test_skips(over):
    assert meetings.parse_event(event(**over), DOMAINS) is None


@pytest.mark.parametrize("url", [
    "https://evil.example/abc-defg-hij",
    "https://meet.google.com.evil.example/abc-defg-hij",
    "http://meet.google.com/abc-defg-hij",
    "https://meet.google.com/lookup/x",
    "javascript:alert(1)",
])
def test_invite_controlled_links_never_reach_the_browser(url):
    assert meetings.parse_event(event(hangoutLink=url), DOMAINS) is None


def test_bot_and_rooms_do_not_count_as_people():
    e = event(attendees=[
        {"email": "a@example.com", "self": True, "responseStatus": "accepted"},
        {"email": "bot@example.com", "responseStatus": "accepted"},
        {"email": "room@resource.calendar.google.com", "resource": True},
    ])
    assert meetings.parse_event(e, DOMAINS, bot_email="bot@example.com") is None


def test_external_guest_is_flagged_and_never_given_the_note():
    e = event(attendees=[{"email": "a@example.com", "self": True, "responseStatus": "accepted"},
                         {"email": "guest@other.com", "responseStatus": "accepted"}])
    m = meetings.parse_event(e, DOMAINS)
    assert m.external
    assert meetings.internal_people(m, DOMAINS) == ["a@example.com"]


def test_everyones_copy_merges_into_one_meeting():
    a = meetings.parse_event(event(), DOMAINS)
    b = meetings.parse_event(event(attendees=[
        {"email": "b@example.com", "self": True, "responseStatus": "accepted"},
        {"email": "c@example.com", "responseStatus": "accepted"}]), DOMAINS)
    merged = meetings.merge([a, b])
    assert len(merged) == 1
    assert merged[0].attendees == ["a@example.com", "b@example.com", "c@example.com"]


def test_each_occurrence_of_a_recurring_room_is_its_own_meeting():
    a = meetings.parse_event(event(), DOMAINS)
    b = meetings.parse_event(event(start={"dateTime": "2026-11-06T09:00:00-05:00"},
                                   end={"dateTime": "2026-11-06T09:30:00-05:00"}), DOMAINS)
    assert a.key != b.key and len(meetings.merge([a, b])) == 2


def test_join_window():
    m = meetings.parse_event(event(), DOMAINS)

    def at(hour, minute, second=0):
        return datetime(2026, 11, 5, hour, minute, second, tzinfo=timezone.utc)

    assert not meetings.due(m, at(13, 58))
    assert meetings.due(m, at(13, 59, 30))
    assert meetings.due(m, at(14, 20))       # late is still worth joining
    assert not meetings.due(m, at(14, 30))


def test_filters_are_off_unless_asked_for():
    m = meetings.parse_event(event(), DOMAINS)
    m.external = True
    assert meetings.wanted(m)
    assert not meetings.wanted(m, skip_external=True)
    assert not meetings.wanted(m, skip_one_on_one=True)


@pytest.mark.parametrize("text,in_call,want", [
    ("Ready to join? Join now", False, "waiting"),
    ("Asking to be let in...", False, "waiting"),
    ("Standup  Leave call", True, "in_call"),
    ("You left the meeting. Return to home screen", False, "ended"),
    ("You've been removed from the meeting", False, "ended"),
    ("Your host ended the meeting for everyone", True, "ended"),
    ("You can't join this video call", False, "refused"),
    ("No one responded to your request to join", False, "refused"),
])
def test_classify(text, in_call, want):
    assert classify(text, in_call) == want


@pytest.mark.parametrize("label,want", [("People - 3 joined", 3), ("3", 3), ("People", None), (None, None)])
def test_people_count(label, want):
    assert parse_people_count(label) == want


def test_alone_timer_needs_a_sustained_known_count():
    t = AloneTimer(grace=100)
    assert not t.expired(1, 0)
    assert not t.expired(1, 50)
    assert not t.expired(2, 60)      # someone came back: start over
    assert not t.expired(1, 70)
    assert not t.expired(None, 180)  # unknown count must never end a recording
    assert not t.expired(1, 190)
    assert t.expired(1, 290)


def test_speaker_with_the_most_overlap_wins():
    timeline = speakers.compress([(0, "Ana"), (1, "Ana"), (10, "Ben"), (30, "Ana")])
    assert timeline == [(0, "Ana"), (10, "Ben"), (30, "Ana")]
    assert speakers.speaker_at(timeline, 2, 8) == "Ana"
    assert speakers.speaker_at(timeline, 8, 25) == "Ben"
    assert speakers.speaker_at(timeline, 40, 50) == "Ana"


def test_timeline_is_anchored_to_the_wav_not_to_when_we_asked_it_to_start():
    # Stopped at t=1000 with 60s of audio, so the file began at t=940.
    assert align([(950.0, "Ana"), (990.5, "Ben")], 1000.0, 60.0) == [(10.0, "Ana"), (50.5, "Ben")]
    assert align([(939.0, "Ana")], 1000.0, 60.0) == [(0.0, "Ana")]


def test_no_timeline_means_unnamed_not_guessed():
    assert speakers.lookup([])(0, 1) == speakers.UNKNOWN


def test_one_whisper_segment_spanning_two_people_is_split_between_them():
    # Whisper happily runs one sentence into the next person's. An owner on the
    # wrong action item is the failure this whole bot exists to avoid.
    def word(text, start):
        return SimpleNamespace(word=text, start=start, end=start + 0.4)

    words = [word("I", 8.0), word(" will", 8.5), word(" send", 9.0),
             word(" I", 10.5), word(" own", 11.0)]
    runs = _split_by_timeline(words, speakers.lookup([(0, "Ana"), (10, "Ben")]))
    assert [(who, text) for who, _, _, text in runs] == [("Ana", "I will send"), ("Ben", "I own")]


def test_state_survives_a_restart_and_is_private(tmp_path):
    path = tmp_path / "state.json"
    State(path).set("k", "recording", audio="/x.wav")
    assert State(path).get("k")["audio"] == "/x.wav"
    assert path.stat().st_mode & 0o077 == 0


def test_prune_keeps_unfinished_work(tmp_path):
    s = State(tmp_path / "state.json")
    s.set("old-done", "done")
    s.set("old-recorded", "recorded")
    s._data["old-done"]["updated_at"] = s._data["old-recorded"]["updated_at"] = "2000-01-01T00:00:00+00:00"
    s.prune(days=30)
    assert s.get("old-done") is None and s.get("old-recorded") is not None


class FakeDrive:
    """Just enough of Drive to check what the filing code asks for."""

    def __init__(self, existing=None):
        self.existing, self.posts, self.queries, self.next_id = existing or [], [], [], 0

    def _resp(self, body):
        return SimpleNamespace(status_code=200, content=b"x", json=lambda: body,
                               request=SimpleNamespace(method="X", url="u"))

    def get(self, url, params=None):
        self.queries.append(params["q"])
        hit = self.existing if "appProperties" in params["q"] else []
        return self._resp({"files": hit})

    def post(self, url, params=None, json=None, data=None, headers=None):
        self.posts.append(SimpleNamespace(url=url, params=params, json=json, data=data))
        self.next_id += 1
        return self._resp({"id": f"id{self.next_id}", "webViewLink": f"link{self.next_id}"})


def test_note_is_filed_by_month_and_shared_silently():
    http = FakeDrive()
    when = datetime(2026, 11, 5, tzinfo=timezone.utc)
    doc = google_api.file_note(http, "root", "key1", "Standup", when, "# hi", ["a@example.com"])
    assert doc["id"] == "id3"
    assert [p.json["name"] for p in http.posts[:2]] == ["2026", "11"]
    assert b"2026-11-05 Standup" in http.posts[2].data and b"key1" in http.posts[2].data
    share = http.posts[3]
    assert share.json == {"type": "user", "role": "reader", "emailAddress": "a@example.com"}
    assert share.params["sendNotificationEmail"] == "false"


def test_filing_twice_does_not_duplicate():
    http = FakeDrive(existing=[{"id": "already", "webViewLink": "l"}])
    when = datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert google_api.file_note(http, "root", "key1", "x", when, "y", ["a@example.com"])["id"] == "already"
    assert http.posts == []


def test_drive_queries_escape_titles():
    http = FakeDrive()
    google_api.ensure_folder(http, "it's", "root")
    assert "it\\'s" in http.queries[0]


def test_google_errors_do_not_echo_response_bodies():
    resp = SimpleNamespace(status_code=403, content=b"secret doc name", json=dict,
                           request=SimpleNamespace(method="GET", url="https://g/x?q=secret"))
    with pytest.raises(google_api.GoogleError) as e:
        google_api._json(resp)
    assert "secret" not in str(e.value)
