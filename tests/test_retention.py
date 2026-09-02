"""Retention deletes permanently, so what it must NOT touch matters most."""
import os
import time

import pytest

from localscribe import config, retention

DAY = retention.DAY
NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path, monkeypatch):
    dirs = {}
    for name, attr in [("audio", "AUDIO_DIR"), ("transcripts", "TRANSCRIPT_DIR"),
                       ("summaries", "SUMMARY_DIR")]:
        d = tmp_path / name
        d.mkdir()
        monkeypatch.setattr(config, attr, d)
        dirs[name] = d
    monkeypatch.setattr(config, "RETENTION_DAYS", 30)
    monkeypatch.setattr(config, "RETENTION_TRANSCRIPTS", 0)
    monkeypatch.setattr(config, "RETENTION_SUMMARIES", 0)
    return dirs


def write(directory, name, age_days, content=b"x"):
    path = directory / name
    path.write_bytes(content)
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_old_audio_is_deleted(store):
    old = write(store["audio"], "standup_old.wav", 45)
    sweep = retention.prune(now=NOW)
    assert not old.exists()
    assert sweep.count == 1


def test_recent_audio_is_kept(store):
    fresh = write(store["audio"], "standup_new.wav", 29)
    retention.prune(now=NOW)
    assert fresh.exists()


def test_the_boundary_keeps_a_file_exactly_at_the_limit(store):
    edge = write(store["audio"], "edge.wav", 30)
    retention.prune(now=NOW)
    assert edge.exists(), "a file exactly at the window should survive"


def test_the_sidecar_goes_with_its_recording(store):
    write(store["audio"], "standup.wav", 40)
    write(store["audio"], "standup.json", 40)
    retention.prune(now=NOW)
    assert list(store["audio"].iterdir()) == []


def test_transcripts_and_summaries_are_kept_by_default(store):
    t = write(store["transcripts"], "old.md", 400)
    s = write(store["summaries"], "old.md", 400)
    retention.prune(now=NOW)
    assert t.exists() and s.exists(), "the notes are the point; never expire them silently"


def test_notes_expire_when_asked(store):
    t = write(store["transcripts"], "old.md", 40)
    s = write(store["summaries"], "old.md", 40)
    retention.prune({"transcripts": 30, "summaries": 30}, now=NOW)
    assert not t.exists() and not s.exists()


def test_zero_days_means_keep_forever(store):
    ancient = write(store["audio"], "ancient.wav", 10_000)
    retention.prune({"audio": 0}, now=NOW)
    assert ancient.exists()


def test_negative_days_also_keeps_forever(store):
    ancient = write(store["audio"], "ancient.wav", 10_000)
    retention.prune({"audio": -1}, now=NOW)
    assert ancient.exists()


def test_unknown_file_types_are_never_touched(store):
    """Only extensions LocalScribe writes, so a stray file is safe."""
    keep = [
        write(store["audio"], "notes.txt", 999),
        write(store["audio"], ".DS_Store", 999),
        write(store["audio"], "recording.mp3", 999),
        write(store["summaries"], "summary.json", 999),   # summaries are .md only
    ]
    retention.prune({"audio": 1, "transcripts": 1, "summaries": 1}, now=NOW)
    for path in keep:
        assert path.exists(), f"{path.name} is not ours to delete"


def test_symlinks_are_never_followed(store, tmp_path):
    outside = tmp_path / "precious.wav"
    outside.write_bytes(b"not ours")
    link = store["audio"] / "link.wav"
    link.symlink_to(outside)
    os.utime(link, (NOW - 999 * DAY, NOW - 999 * DAY), follow_symlinks=False)

    retention.prune({"audio": 1}, now=NOW)
    assert outside.exists(), "a symlink must not be a route out of the data directory"
    assert link.is_symlink()


def test_directories_are_ignored(store):
    nested = store["audio"] / "subdir.wav"
    nested.mkdir()
    retention.prune({"audio": 1}, now=NOW)
    assert nested.is_dir()


def test_dry_run_deletes_nothing_but_still_reports(store):
    old = write(store["audio"], "old.wav", 40, content=b"y" * 2048)
    sweep = retention.prune(dry_run=True, now=NOW)
    assert old.exists()
    assert sweep.count == 1
    assert sweep.bytes_freed == 2048
    assert "would delete" in retention.describe(sweep)


def test_reports_bytes_freed(store):
    write(store["audio"], "a.wav", 40, content=b"z" * 1_000_000)
    write(store["audio"], "b.wav", 40, content=b"z" * 500_000)
    sweep = retention.prune(now=NOW)
    assert sweep.bytes_freed == 1_500_000
    assert "1.5 MB" in retention.describe(sweep)


def test_nothing_to_do_is_falsy_and_silent(store):
    write(store["audio"], "fresh.wav", 1)
    sweep = retention.prune(now=NOW)
    assert not sweep
    assert retention.describe(sweep) == ""


def test_missing_directory_is_not_an_error(store, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path / "gone")
    assert retention.expired("audio", 30, now=NOW) == []


def test_uses_real_clock_by_default(store):
    write(store["audio"], "old.wav", 0)
    os.utime(store["audio"] / "old.wav", (time.time() - 60 * DAY,) * 2)
    assert retention.prune().count == 1


# --- deleting a recording once its summary exists ---------------------------

def test_recording_and_sidecar_are_deleted(store):
    wav = write(store["audio"], "standup.wav", 0, content=b"a" * 5000)
    side = write(store["audio"], "standup.json", 0, content=b"{}")
    freed = retention.delete_recording(wav)
    assert not wav.exists() and not side.exists()
    assert freed == 5002


def test_a_missing_sidecar_is_fine(store):
    wav = write(store["audio"], "standup.wav", 0, content=b"a" * 100)
    assert retention.delete_recording(wav) == 100


def test_audio_outside_the_data_directory_is_never_deleted(store, tmp_path):
    """`process` is routinely pointed at someone's only copy of a file."""
    elsewhere = tmp_path / "Downloads"
    elsewhere.mkdir()
    theirs = elsewhere / "interview.wav"
    theirs.write_bytes(b"irreplaceable")

    assert retention.delete_recording(theirs) == 0
    assert theirs.exists()


def test_a_symlink_in_the_audio_directory_is_never_followed(store, tmp_path):
    outside = tmp_path / "precious.wav"
    outside.write_bytes(b"not ours")
    link = store["audio"] / "link.wav"
    link.symlink_to(outside)

    assert retention.delete_recording(link) == 0
    assert outside.exists()


def test_a_nested_path_under_the_audio_directory_is_refused(store):
    nested = store["audio"] / "sub"
    nested.mkdir()
    wav = nested / "deep.wav"
    wav.write_bytes(b"x")
    assert retention.delete_recording(wav) == 0
    assert wav.exists()


def test_only_wav_files_are_deleted(store):
    other = write(store["audio"], "notes.txt", 0)
    assert retention.delete_recording(other) == 0
    assert other.exists()


def test_deleting_something_that_is_already_gone_is_harmless(store):
    assert retention.delete_recording(store["audio"] / "never-existed.wav") == 0
