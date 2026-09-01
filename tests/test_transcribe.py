"""Speaker attribution: the part that would silently produce a wrong transcript."""
from dataclasses import dataclass

import numpy as np
import pytest

from meetnotes import config
from meetnotes.transcribe import (
    Segment,
    Transcript,
    _attribute,
    _split_by_speaker,
    load_transcript,
    write_transcript,
)

ROLES = ["me", "them"]
SR = config.SAMPLE_RATE


@dataclass
class FakeWord:
    start: float
    end: float
    word: str


def two_channel_audio(turns, gain=(0.5, 0.3)):
    """turns: [(channel, start_sec, end_sec)] -> audio + per-channel norms."""
    total = int(max(e for _, _, e in turns) * SR) + SR
    audio = np.random.default_rng(0).normal(0, 1e-4, (total, 2)).astype(np.float32)
    rng = np.random.default_rng(1)
    for ch, start, end in turns:
        a, b = int(start * SR), int(end * SR)
        speech = rng.normal(0, gain[ch], b - a).astype(np.float32)
        audio[a:b, ch] += speech
        if ch == 1:
            audio[a:b, 0] += speech * 0.04   # the mic also hears the speakers
    from meetnotes.transcribe import _cancel_leak, _norms
    scored = _cancel_leak(audio, ROLES)
    return scored, _norms(scored)


def words(spans):
    return [FakeWord(s, e, f" w{i}") for i, (s, e) in enumerate(spans)]


def test_split_by_speaker_cuts_at_the_turn_change():
    """A single Whisper segment routinely spans a turn; it must come out as two."""
    audio, norms = two_channel_audio([(0, 0.0, 2.0), (1, 2.0, 4.0)])
    spans = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0),
             (2.0, 2.5), (2.5, 3.0), (3.0, 3.5), (3.5, 4.0)]
    runs = _split_by_speaker(words(spans), audio, ROLES, norms)
    assert [r[0] for r in runs] == ["You", "Them"]
    assert runs[0][3] == "w0 w1 w2 w3"
    assert runs[1][3] == "w4 w5 w6 w7"


def test_split_by_speaker_keeps_one_speaker_as_one_run():
    audio, norms = two_channel_audio([(1, 0.0, 3.0)])
    runs = _split_by_speaker(words([(i * 0.5, i * 0.5 + 0.5) for i in range(6)]),
                             audio, ROLES, norms)
    assert len(runs) == 1
    assert runs[0][0] == "Them"


def test_mic_bleed_is_not_mistaken_for_the_mic_speaking():
    """Channel 0 always carries a quiet copy of channel 1 through the speakers."""
    audio, norms = two_channel_audio([(1, 0.0, 2.0)])
    runs = _split_by_speaker(words([(i * 0.5, i * 0.5 + 0.5) for i in range(4)]),
                             audio, ROLES, norms)
    assert [r[0] for r in runs] == ["Them"]


def test_a_quiet_mic_still_wins_its_own_turns():
    """Per-channel normalization: gain differences must not decide the speaker."""
    audio, norms = two_channel_audio([(0, 0.0, 2.0), (1, 2.0, 4.0)], gain=(0.05, 0.6))
    runs = _split_by_speaker(words([(i * 0.5, i * 0.5 + 0.5) for i in range(8)]),
                             audio, ROLES, norms)
    assert [r[0] for r in runs] == ["You", "Them"]


def test_crosstalk_is_flagged_as_uncertain():
    audio, norms = two_channel_audio([(0, 0.0, 2.0), (1, 0.0, 2.0)], gain=(0.4, 0.4))
    runs = _split_by_speaker(words([(i * 0.5, i * 0.5 + 0.5) for i in range(4)]),
                             audio, ROLES, norms)
    assert all(r[0].endswith("?") for r in runs)


def test_segment_level_fallback():
    audio, norms = two_channel_audio([(0, 0.0, 2.0), (1, 2.0, 4.0)])
    assert _attribute(audio, ROLES, 0.0, 2.0, norms) == "You"
    assert _attribute(audio, ROLES, 2.0, 4.0, norms) == "Them"


def test_silence_has_no_speaker():
    audio = np.zeros((SR * 2, 2), dtype=np.float32)
    norms = np.array([1.0, 1.0])
    assert _attribute(audio, ROLES, 0.0, 2.0, norms) == "Speaker"


def test_mic_only_recording_is_always_you():
    audio = np.abs(np.random.default_rng(0).normal(0, 0.3, (SR, 1)).astype(np.float32))
    assert _attribute(audio, ["me"], 0.0, 1.0, np.array([1.0])) == "You"


def test_transcript_round_trip(tmp_path):
    t = Transcript("/tmp/a.wav", "Standup", "2026-01-01T10:00:00", 12.5, "en",
                   [Segment(0.0, 4.0, "You", "hello"), Segment(4.0, 8.0, "Them", "hi")])
    md, js = write_transcript(t, out_dir=tmp_path)
    assert "[00:00] You: hello" in md.read_text()
    back = load_transcript(js)
    assert back.label == "Standup"
    assert [s.speaker for s in back.segments] == ["You", "Them"]


@pytest.mark.parametrize("seconds,expected", [(5, "00:05"), (65, "01:05"), (3725, "01:02:05")])
def test_timestamp_format(seconds, expected):
    from meetnotes.transcribe import _ts
    assert _ts(seconds) == expected
