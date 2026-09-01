import numpy as np

from localscribe import config
from localscribe.audio import Device, _Source, find_device


def _devices(monkeypatch, names):
    devs = [Device(i, n, 1, 48000.0) for i, n in enumerate(names)]
    monkeypatch.setattr("localscribe.audio.list_input_devices", lambda: devs)
    return devs


def test_find_device_prefers_an_exact_name(monkeypatch):
    _devices(monkeypatch, ["BlackHole 2ch", "BlackHole 16ch"])
    assert find_device("BlackHole 16ch").name == "BlackHole 16ch"


def test_find_device_matches_a_substring_case_insensitively(monkeypatch):
    _devices(monkeypatch, ["MacBook Pro Microphone", "BlackHole 2ch"])
    assert find_device("blackhole").name == "BlackHole 2ch"


def test_find_device_returns_none_when_absent(monkeypatch):
    _devices(monkeypatch, ["MacBook Pro Microphone"])
    assert find_device("BlackHole") is None
    assert find_device("") is None


def _source():
    return _Source("them", Device(0, "fake", 1, 48000.0))


def test_take_zero_pads_a_source_running_behind():
    s = _source()
    s.buf = np.ones(100, dtype=np.float32)
    out = s.take(300)
    assert out.shape == (300,)
    assert out[:100].all() and not out[100:].any()
    assert s.buf.size == 0


def test_take_trims_a_source_that_drifts_ahead():
    """Each device runs off its own clock; over an hour one gains samples."""
    s = _source()
    s.buf = np.arange(config.SAMPLE_RATE * 5, dtype=np.float32)
    s.take(10)
    assert s.buf.size == int(0.5 * config.SAMPLE_RATE)   # capped at the skew limit
    assert s.buf[-1] == config.SAMPLE_RATE * 5 - 1       # keeps the newest audio


def test_take_is_exact_when_the_source_is_in_step():
    s = _source()
    s.buf = np.arange(500, dtype=np.float32)
    assert np.array_equal(s.take(500), np.arange(500, dtype=np.float32))
    assert s.buf.size == 0


def test_callback_downmixes_to_mono():
    s = _source()
    s._callback(np.array([[1.0, 3.0], [2.0, 4.0]], dtype=np.float32), 2, None, None)
    assert np.array_equal(s.q.get_nowait(), np.array([2.0, 3.0], dtype=np.float32))
