from meetnotes.summarize import _chunks, _extractive, normalize_actions
from meetnotes.transcribe import Segment, Transcript


def _transcript(segments):
    return Transcript(
        audio_path="/tmp/x.wav", label="Test", started_at="2026-01-01T10:00:00",
        duration=60.0, language="en",
        segments=[Segment(i * 5.0, i * 5.0 + 4.0, sp, txt) for i, (sp, txt) in enumerate(segments)],
    )


def test_normalize_actions_only_touches_the_action_section():
    src = (
        "## Key points\n- a plain bullet\n\n"
        "## Action items\n- Doron: ship the migration\n* Them - watch the dashboard\n"
        "- [ ] **Ana** — already formatted\n\n"
        "## Open questions\n- still a plain bullet\n"
    )
    out = normalize_actions(src)
    assert "- a plain bullet" in out
    assert "- still a plain bullet" in out
    assert "- [ ] **Doron** — ship the migration" in out
    assert "- [ ] **Them** — watch the dashboard" in out
    assert out.count("- [ ] **Ana** — already formatted") == 1


def test_normalize_actions_leaves_none_recorded_alone():
    assert "None recorded." in normalize_actions("## Action items\nNone recorded.\n")


def test_normalize_actions_handles_a_bare_task():
    out = normalize_actions("## Action items\n- send the invite\n")
    assert out.strip() == "## Action items\n- [ ] send the invite"


def test_chunks_cover_every_word_and_overlap():
    words = [f"w{i}" for i in range(4000)]
    parts = _chunks(" ".join(words), size=1000, overlap=100)
    assert len(parts) > 1
    assert parts[0].split()[0] == "w0"
    assert parts[-1].split()[-1] == "w3999"
    # consecutive chunks share their overlap
    assert parts[0].split()[-100:] == parts[1].split()[:100]


def test_chunks_leaves_short_text_alone():
    assert _chunks("one two three") == ["one two three"]


def test_extractive_finds_actions_decisions_and_questions():
    out = _extractive(_transcript([
        ("You", "We looked at the checkout latency problem on the orders endpoint today."),
        ("Them", "Should we roll the release back or push the index forward?"),
        ("You", "We agreed to ship the index forward for the orders endpoint."),
        ("Them", "I will monitor the checkout dashboard after the deploy lands."),
    ]))
    for heading in ("## TL;DR", "## Key points", "## Decisions", "## Action items", "## Open questions"):
        assert heading in out
    assert "we agreed to ship the index forward" in out.lower()
    assert "- [ ] **Them** — I will monitor" in out
    assert "Should we roll the release back" in out


def test_extractive_survives_an_empty_transcript():
    assert "## TL;DR" in _extractive(_transcript([]))
