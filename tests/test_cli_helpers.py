import argparse

import pytest

from meetnotes.cli import hms, meter, parse_duration, slugify


@pytest.mark.parametrize(
    "text,expected",
    [
        ("90s", 90),
        ("30m", 1800),
        ("1h30m", 5400),
        ("2h", 7200),
        ("45", 2700),      # bare number means minutes
        ("1h 30m 15s", 5415),
        (None, None),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def test_parse_duration_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration("soon")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Latency sync", "latency-sync"),
        ("Groucho's 1:1", "groucho-s-1-1"),
        ("   ", "meeting"),
        ("!!!", "meeting"),
    ],
)
def test_slugify(text, expected):
    assert slugify(text) == expected


def test_hms():
    assert hms(0) == "00:00:00"
    assert hms(61) == "00:01:01"
    assert hms(3661) == "01:01:01"


def test_meter_is_monotonic_and_bounded():
    widths = [meter(lvl).count("█") for lvl in (0.0, 1e-4, 0.01, 0.1, 1.0)]
    assert widths == sorted(widths)
    assert widths[0] == 0 and widths[-1] == 24
