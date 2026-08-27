"""Closing Auction Session (CAS) window status and reference price/band —
see app.data.cas's module docstring for the mechanism and its source."""
from datetime import datetime

import pytest

from app.data.cas import (
    REFERENCE_BAND_PCT,
    cas_window_status,
    reference_band,
    reference_price,
)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 27, hour, minute)  # a Thursday NSE trading day


@pytest.mark.parametrize(
    "when,expected_phase",
    [
        (_at(8, 0), "pre_open"),
        (_at(9, 14), "pre_open"),
        (_at(9, 15), "continuous"),
        (_at(12, 0), "continuous"),
        (_at(14, 59), "continuous"),
        (_at(15, 0), "reference_window"),
        (_at(15, 14), "reference_window"),
        (_at(15, 15), "auction"),
        (_at(15, 34), "auction"),
        (_at(15, 35), "transition"),
        (_at(15, 49), "transition"),
        (_at(15, 50), "post_close"),
        (_at(15, 59), "post_close"),
        (_at(16, 0), "closed"),
        (_at(20, 0), "closed"),
    ],
)
def test_cas_window_status_phase_boundaries(when, expected_phase):
    assert cas_window_status(when).phase == expected_phase


def test_cas_window_status_defaults_to_now_without_error():
    status = cas_window_status()
    assert status.phase in (
        "pre_open", "continuous", "reference_window", "auction", "transition", "post_close", "closed",
    )


def _series(bars: list[tuple[int, int, float, int]]) -> list[tuple[datetime, float, int]]:
    """bars: (hour, minute, price, volume) -> (datetime, price, volume)."""
    return [(_at(h, m), p, v) for h, m, p, v in bars]


def test_reference_price_is_vwap_of_the_15_00_to_15_15_window_only():
    series = _series(
        [
            (14, 55, 1000.0, 500),  # before the window — must be excluded
            (15, 0, 100.0, 10),
            (15, 5, 110.0, 30),
            (15, 14, 90.0, 10),
            (15, 15, 5000.0, 999),  # at/after the window end — must be excluded
        ]
    )
    expected = (100.0 * 10 + 110.0 * 30 + 90.0 * 10) / (10 + 30 + 10)
    assert reference_price(series) == pytest.approx(expected)


def test_reference_price_none_when_series_does_not_cover_the_window():
    series = _series([(9, 15, 100.0, 10), (10, 0, 101.0, 20)])
    assert reference_price(series) is None


def test_reference_price_none_for_empty_series():
    assert reference_price([]) is None


def test_reference_band_is_symmetric_percent_of_reference_price():
    lower, upper = reference_band(1000.0)
    assert lower == pytest.approx(1000.0 * (1 - REFERENCE_BAND_PCT))
    assert upper == pytest.approx(1000.0 * (1 + REFERENCE_BAND_PCT))
    assert lower < 1000.0 < upper
