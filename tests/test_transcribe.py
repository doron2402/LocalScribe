"""Speaker attribution: the part that would silently produce a wrong transcript."""
from dataclasses import dataclass

import numpy as np
import pytest

from localscribe import config
from localscribe.transcribe import (
    Segment,
    Transcript,
    _attribute,
    _split_by_speaker,
    _who,
    load_transcript,
    measure_bleed,
    write_transcript,
)

ROLES = ["me", "them"]
SR = config.SAMPLE_RATE


@dataclass
class FakeWord:
    start: float
    end: float
    word: str


def room_impulse(rng, taps=int(0.08 * SR), delay=45):
    """A delayed copy plus a decaying tail — what actually reaches the mic.

    Normalized to unit energy, so the `bleed` argument alone sets how loud the
    speakers come back. Without that the tail adds energy and the mic ends up
    hearing the speakers as loudly as the source, which in a real room is the
    condition for feedback howl, not a meeting.
    """
    ir = np.zeros(taps, dtype=np.float32)
    ir[delay] = 1.0
    tail = rng.normal(0, 0.25, taps - delay - 15) * np.exp(np.linspace(0, -5, taps - delay - 15))
    ir[delay + 15:] = tail
    return ir / np.sqrt(np.sum(ir ** 2))


def two_channel_audio(turns, gain=(0.5, 0.3), bleed=0.35, reverb=True):
    """turns: [(channel, start_sec, end_sec)] -> (audio, bleed measurement)."""
    rng = np.random.default_rng(0)
    total = int(max(e for _, _, e in turns) * SR) + SR
    audio = rng.normal(0, 1e-4, (total, 2)).astype(np.float32)
    ir = room_impulse(rng)
    for ch, start, end in turns:
        a, b = int(start * SR), int(end * SR)
        speech = rng.normal(0, gain[ch], b - a).astype(np.float32)
        audio[a:b, ch] += speech
        if ch == 1:  # the speakers leak back into the microphone
            leak = np.convolve(speech, ir)[: b - a] * bleed if reverb else speech * bleed
            audio[a:b, 0] += leak.astype(np.float32)
    return audio, measure_bleed(audio, ROLES)


def words(spans):
    return [FakeWord(s, e, f" w{i}") for i, (s, e) in enumerate(spans)]


def test_split_by_speaker_cuts_at_the_turn_change():
    """A single Whisper segment routinely spans a turn; it must come out as two."""
    audio, bleed = two_channel_audio([(0, 0.0, 3.0), (1, 3.0, 6.0)])
    spans = [(t, t + 0.5) for t in np.arange(0.2, 2.6, 0.5)]
    spans += [(t, t + 0.5) for t in np.arange(3.2, 5.6, 0.5)]
    runs = _split_by_speaker(words(spans), audio, ROLES, bleed)
    assert [r[0].rstrip("?") for r in runs] == ["You", "Them"]


def test_split_by_speaker_keeps_one_speaker_as_one_run():
    audio, bleed = two_channel_audio([(1, 0.0, 4.0)])
    runs = _split_by_speaker(words([(t, t + 0.5) for t in np.arange(0.3, 3.4, 0.5)]),
                             audio, ROLES, bleed)
    assert len(runs) == 1
    assert runs[0][0].rstrip("?") == "Them"


def test_reverberant_bleed_is_not_mistaken_for_the_mic_speaking():
    """The mic hears the speakers late and smeared by the room.

    A delay-and-subtract canceller removes almost none of that, so attribution
    compares smoothed energy against what the bleed alone would explain.
    """
    audio, bleed = two_channel_audio([(1, 0.0, 5.0)], bleed=0.5)
    for t in np.arange(0.5, 4.3, 0.4):
        idx, _ = _who(audio, ROLES, bleed, t, t + 0.35)
        assert idx == 1, f"bleed at t={t:.1f}s was credited to the microphone"


def test_a_quiet_mic_still_wins_its_own_turns():
    """Gain differences between devices must not decide the speaker."""
    audio, bleed = two_channel_audio([(0, 0.0, 3.0), (1, 3.0, 6.0)], gain=(0.05, 0.6))
    assert _who(audio, ROLES, bleed, 1.0, 2.0)[0] == 0
    assert _who(audio, ROLES, bleed, 4.0, 5.0)[0] == 1


def test_talking_over_each_other_is_flagged_as_uncertain():
    """Clean turns first, then a stretch where both talk at once.

    The bleed has to be learned from somewhere, so a recording that is nothing
    but crosstalk is genuinely undecidable — there is no moment of far-end-only
    audio to calibrate against. A real call always has some.
    """
    audio, bleed = two_channel_audio([
        (0, 0.0, 3.0),            # you alone
        (1, 3.5, 6.5),            # them alone: this is what calibrates the bleed
        (0, 7.0, 10.0), (1, 7.0, 10.0),   # both at once
    ], gain=(0.45, 0.45))
    assert _who(audio, ROLES, bleed, 1.0, 2.5)[1] is True, "a clean turn should be confident"
    assert _who(audio, ROLES, bleed, 4.0, 6.0)[1] is True, "a clean turn should be confident"
    assert _who(audio, ROLES, bleed, 7.5, 9.5)[1] is False, "overlap should be marked unsure"


def test_loud_far_end_alone_is_confident():
    audio, bleed = two_channel_audio([(1, 0.0, 4.0)])
    idx, confident = _who(audio, ROLES, bleed, 1.0, 3.0)
    assert (idx, confident) == (1, True)


def test_segment_level_fallback():
    audio, bleed = two_channel_audio([(0, 0.0, 3.0), (1, 3.0, 6.0)])
    assert _attribute(audio, ROLES, bleed, 0.5, 2.5) == "You"
    assert _attribute(audio, ROLES, bleed, 3.5, 5.5).rstrip("?") == "Them"


def test_silence_has_no_speaker():
    audio = np.zeros((SR * 3, 2), dtype=np.float32)
    bleed = measure_bleed(audio, ROLES)
    assert _attribute(audio, ROLES, bleed, 0.0, 2.0) == "Speaker"


def test_mic_only_recording_is_always_you():
    rng = np.random.default_rng(0)
    audio = rng.normal(0, 0.3, (SR * 2, 1)).astype(np.float32)
    bleed = measure_bleed(audio, ["me"])
    assert bleed.single is True
    assert _attribute(audio, ["me"], bleed, 0.0, 1.0) == "You"


def test_bleed_gain_reflects_how_loud_the_speakers_are():
    _, quiet = two_channel_audio([(1, 0.0, 5.0)], bleed=0.1)
    _, loud = two_channel_audio([(1, 0.0, 5.0)], bleed=0.6)
    assert loud.gain > quiet.gain * 2


def test_transcript_round_trip(tmp_path):
    t = Transcript("/tmp/a.wav", "Standup", "2026-01-01T10:00:00", 12.5, "en",
                   [Segment(0.0, 4.0, "You", "hello"), Segment(4.0, 8.0, "Them", "hi")],
                   engine="mlx", model="base.en")
    md, js = write_transcript(t, out_dir=tmp_path)
    body = md.read_text()
    assert "[00:00] You: hello" in body
    assert "mlx (base.en)" in body
    back = load_transcript(js)
    assert back.label == "Standup"
    assert [s.speaker for s in back.segments] == ["You", "Them"]


@pytest.mark.parametrize("seconds,expected", [(5, "00:05"), (65, "01:05"), (3725, "01:02:05")])
def test_timestamp_format(seconds, expected):
    from localscribe.transcribe import _ts
    assert _ts(seconds) == expected
