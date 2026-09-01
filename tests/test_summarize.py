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


# --- Ollama autostart -------------------------------------------------------

def test_autostart_is_skipped_when_already_running(monkeypatch):
    from meetnotes import summarize as S
    monkeypatch.setattr(S, "ollama_available", lambda: True)
    monkeypatch.setattr(S.shutil, "which", lambda n: (_ for _ in ()).throw(AssertionError("looked for the binary")))
    assert S.ensure_ollama() is True


def test_autostart_launches_the_daemon(monkeypatch):
    from meetnotes import summarize as S
    states = iter([False, False, True])
    monkeypatch.setattr(S, "ollama_available", lambda: next(states))
    monkeypatch.setattr(S.shutil, "which", lambda n: "/opt/homebrew/bin/ollama")
    launched = []
    monkeypatch.setattr(S.subprocess, "Popen", lambda cmd, **kw: launched.append((cmd, kw)))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    notified = []
    assert S.ensure_ollama(on_start=lambda: notified.append(1)) is True
    assert launched[0][0] == ["/opt/homebrew/bin/ollama", "serve"]
    assert launched[0][1]["start_new_session"] is True   # must outlive this process
    assert notified == [1]


def test_autostart_gives_up_when_ollama_is_absent(monkeypatch):
    from meetnotes import summarize as S
    monkeypatch.setattr(S, "ollama_available", lambda: False)
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    assert S.ensure_ollama() is False


def test_a_remote_ollama_is_never_started_locally(monkeypatch):
    from meetnotes import summarize as S
    monkeypatch.setattr(S, "ollama_available", lambda: False)
    monkeypatch.setattr(S.config, "OLLAMA_HOST", "http://gpu-box.local:11434")
    monkeypatch.setattr(S.shutil, "which", lambda n: "/opt/homebrew/bin/ollama")
    assert S.ensure_ollama() is False


def test_autostart_can_be_turned_off(monkeypatch):
    from meetnotes import summarize as S
    monkeypatch.setattr(S, "ollama_available", lambda: False)
    monkeypatch.setattr(S.config, "OLLAMA_AUTOSTART", False)
    monkeypatch.setattr(S.shutil, "which", lambda n: "/opt/homebrew/bin/ollama")
    assert S.ensure_ollama() is False
