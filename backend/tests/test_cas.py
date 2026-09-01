"""Closing Auction Session (CAS) window status and reference price/band —
see app.data.cas's module docstring for the mechanism and its source."""
from datetime import datetime

import pytest

from app.data.cas import (
    REFERENCE_BAND_PCT,
    cas_window_status,
    compute_bias_signal,
    constituent_snapshot,
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


@pytest.mark.parametrize(
    "when,expected_substring",
    [
        (_at(15, 15), "reference price calculation"),
        (_at(15, 19), "reference price calculation"),
        (_at(15, 20), "market + limit orders"),
        (_at(15, 24), "market + limit orders"),
        (_at(15, 25), "limit orders only"),
        (_at(15, 29), "limit orders only"),
        (_at(15, 30), "order matching"),
        (_at(15, 34), "order matching"),
    ],
)
def test_cas_window_status_auction_sub_phase_labels(when, expected_substring):
    status = cas_window_status(when)
    assert status.phase == "auction"  # sub-phase only refines the label, not the phase
    assert expected_substring in status.label


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


def test_constituent_snapshot_computes_deviation_from_reference():
    series = _series([(15, 0, 100.0, 10), (15, 10, 100.0, 10), (15, 30, 103.0, 5)])
    snap = constituent_snapshot("FOO", "Foo Ltd", series)
    assert snap.reference_price == pytest.approx(100.0)
    assert snap.current_price == pytest.approx(103.0)  # last bar, after the reference window
    assert snap.deviation_pct == pytest.approx(3.0)
    assert snap.band_lower == pytest.approx(97.0)
    assert snap.band_upper == pytest.approx(103.0)


def test_constituent_snapshot_no_reference_yet_still_reports_current_price():
    series = _series([(9, 15, 50.0, 10), (10, 0, 51.0, 20)])
    snap = constituent_snapshot("FOO", "Foo Ltd", series)
    assert snap.reference_price is None
    assert snap.deviation_pct is None
    assert snap.current_price == pytest.approx(51.0)


def test_constituent_snapshot_empty_series():
    snap = constituent_snapshot("FOO", "Foo Ltd", [])
    assert snap.reference_price is None
    assert snap.current_price is None
    assert snap.deviation_pct is None


def _snap(deviation_pct: float | None):
    if deviation_pct is None:
        # A bar outside the 3:00-3:15pm reference window -> no reference price yet.
        return constituent_snapshot("X", "X", _series([(10, 0, 100.0, 10)]))
    return constituent_snapshot(
        "X", "X",
        _series([(15, 0, 100.0, 10), (15, 30, 100.0 * (1 + deviation_pct / 100.0), 1)]),
    )


def test_compute_bias_signal_none_when_no_snapshot_has_data():
    assert compute_bias_signal([_snap(None), _snap(None)]) is None


def test_compute_bias_signal_upside_and_breadth():
    snapshots = [_snap(1.0), _snap(2.0), _snap(-0.5)]
    signal = compute_bias_signal(snapshots)
    assert signal.direction == "Upside"
    assert signal.n_up == 2
    assert signal.n_down == 1
    assert signal.n_total == 3
    assert signal.n_with_data == 3
    assert signal.average_deviation_pct == pytest.approx((1.0 + 2.0 - 0.5) / 3, abs=1e-3)
    assert signal.breadth_pct == pytest.approx(2 / 3 * 100.0, abs=0.1)


def test_compute_bias_signal_downside():
    signal = compute_bias_signal([_snap(-1.0), _snap(-2.0), _snap(0.1)])
    assert signal.direction == "Downside"
    assert signal.n_down == 2


def test_compute_bias_signal_flat_within_threshold():
    signal = compute_bias_signal([_snap(0.01), _snap(-0.01), _snap(0.0)], flat_threshold_pct=0.02)
    assert signal.direction == "Flat"
    assert signal.n_flat == 3


def test_compute_bias_signal_magnitude_buckets():
    assert compute_bias_signal([_snap(0.05)]).magnitude_bucket == "< 0.1%"
    assert compute_bias_signal([_snap(0.2)]).magnitude_bucket == "0.1% – 0.3%"
    assert compute_bias_signal([_snap(0.5)]).magnitude_bucket == "0.3% – 1%"
    assert compute_bias_signal([_snap(2.0)]).magnitude_bucket == "1% – 3%"
    assert compute_bias_signal([_snap(5.0)]).magnitude_bucket == "> 3%"


def test_compute_bias_signal_ignores_snapshots_without_reference():
    signal = compute_bias_signal([_snap(1.0), _snap(None)])
    assert signal.n_total == 2
    assert signal.n_with_data == 1
