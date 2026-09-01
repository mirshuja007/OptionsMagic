"""app.data.cas_history: the persistent log of real CAS session outcomes
(reference price vs. actual settled close) that any future "probability of
a CAS move" answer has to be built from — see the module docstring."""
from datetime import date, datetime, time as dtime

import pytest

from app.data import cas_history
from app.data.cas_history import (
    CASSessionRecord,
    load_records,
    record_session_outcome,
    summarize_history,
)

SESSION_DATE = date(2026, 8, 25)  # a fixed past Tuesday — not "today" in any test run


def _series(bars: list[tuple[int, int, float, int]], on: date = SESSION_DATE):
    """bars: (hour, minute, price, volume) -> (datetime, price, volume)."""
    return [(datetime.combine(on, dtime(h, m)), p, v) for h, m, p, v in bars]


def _known_series(on: date = SESSION_DATE):
    # Reference VWAP (15:00-15:15 bars only): (100*10 + 104*10) / 20 = 102.
    # Final price: the series' last bar, 110, regardless of its timestamp.
    return _series([(15, 0, 100.0, 10), (15, 10, 104.0, 10), (15, 30, 110.0, 5)], on)


def test_record_session_outcome_computes_reference_and_final_from_series(monkeypatch, tmp_path):
    monkeypatch.setattr("app.data.feed.generate_minute_series", lambda symbol, session_date=None, **kw: _known_series())
    path = tmp_path / "log.csv"

    record = record_session_outcome("NIFTY", SESSION_DATE, path=path)

    assert record.reference_price == pytest.approx(102.0)
    assert record.final_price == pytest.approx(110.0)
    assert record.move_pct == pytest.approx((110.0 - 102.0) / 102.0 * 100.0, abs=1e-3)
    assert record.session_date == SESSION_DATE
    assert record.symbol == "NIFTY"


def test_record_session_outcome_raises_for_future_date(tmp_path):
    future = date.today().replace(year=date.today().year + 1)
    with pytest.raises(ValueError, match="future"):
        record_session_outcome("NIFTY", future, path=tmp_path / "log.csv")


def test_record_session_outcome_raises_when_today_and_auction_not_settled(monkeypatch, tmp_path):
    monkeypatch.setattr("app.data.feed.generate_minute_series", lambda symbol, session_date=None, **kw: _known_series(date.today()))
    today = date.today()
    not_settled = datetime.combine(today, dtime(12, 0))  # mid-session continuous trading

    with pytest.raises(ValueError, match="hasn't settled"):
        record_session_outcome("NIFTY", today, now=not_settled, path=tmp_path / "log.csv")


def test_record_session_outcome_allows_today_after_settlement(monkeypatch, tmp_path):
    today = date.today()
    monkeypatch.setattr("app.data.feed.generate_minute_series", lambda symbol, session_date=None, **kw: _known_series(today))
    settled = datetime.combine(today, dtime(15, 40))  # transition phase

    record = record_session_outcome("NIFTY", today, now=settled, path=tmp_path / "log.csv")
    assert record.session_date == today


def test_record_session_outcome_raises_when_series_has_no_reference_window(monkeypatch, tmp_path):
    no_ref_window = _series([(9, 15, 100.0, 10), (10, 0, 101.0, 20)])
    monkeypatch.setattr("app.data.feed.generate_minute_series", lambda symbol, session_date=None, **kw: no_ref_window)

    with pytest.raises(ValueError, match="reference window"):
        record_session_outcome("NIFTY", SESSION_DATE, path=tmp_path / "log.csv")


def test_record_session_outcome_raises_for_empty_series(monkeypatch, tmp_path):
    monkeypatch.setattr("app.data.feed.generate_minute_series", lambda symbol, session_date=None, **kw: [])

    with pytest.raises(ValueError, match="no data"):
        record_session_outcome("NIFTY", SESSION_DATE, path=tmp_path / "log.csv")


def test_record_session_outcome_replaces_not_duplicates_same_day_symbol(monkeypatch, tmp_path):
    path = tmp_path / "log.csv"
    monkeypatch.setattr("app.data.feed.generate_minute_series", lambda symbol, session_date=None, **kw: _known_series())
    record_session_outcome("NIFTY", SESSION_DATE, path=path)

    # Same (date, symbol) logged again with a different outcome.
    different_series = _series([(15, 0, 200.0, 10), (15, 10, 200.0, 10), (15, 30, 202.0, 5)])
    monkeypatch.setattr("app.data.feed.generate_minute_series", lambda symbol, session_date=None, **kw: different_series)
    record_session_outcome("NIFTY", SESSION_DATE, path=path)

    records = load_records("NIFTY", path=path)
    assert len(records) == 1
    assert records[0].reference_price == pytest.approx(200.0)


def test_load_records_filters_by_symbol_and_sorts_by_date(tmp_path):
    path = tmp_path / "log.csv"
    rows = [
        CASSessionRecord(date(2026, 8, 26), "NIFTY", 100.0, 101.0, 1.0, datetime(2026, 8, 26, 16, 0)),
        CASSessionRecord(date(2026, 8, 24), "NIFTY", 100.0, 99.0, -1.0, datetime(2026, 8, 24, 16, 0)),
        CASSessionRecord(date(2026, 8, 25), "SENSEX", 100.0, 100.5, 0.5, datetime(2026, 8, 25, 16, 0)),
    ]
    cas_history._write_all(rows, path)

    nifty = load_records("NIFTY", path=path)
    assert [r.session_date for r in nifty] == [date(2026, 8, 24), date(2026, 8, 26)]

    everything = load_records(path=path)
    assert len(everything) == 3


def test_summarize_history_returns_none_when_no_records(tmp_path):
    assert summarize_history("NIFTY", path=tmp_path / "log.csv") is None


def test_summarize_history_computes_correct_stats(tmp_path):
    path = tmp_path / "log.csv"
    rows = [
        CASSessionRecord(date(2026, 8, 24), "NIFTY", 100.0, 101.0, 1.0, datetime(2026, 8, 24, 16, 0)),
        CASSessionRecord(date(2026, 8, 25), "NIFTY", 100.0, 99.5, -0.5, datetime(2026, 8, 25, 16, 0)),
        CASSessionRecord(date(2026, 8, 26), "NIFTY", 100.0, 100.31, 0.31, datetime(2026, 8, 26, 16, 0)),
    ]
    cas_history._write_all(rows, path)

    summary = summarize_history("NIFTY", path=path)

    assert summary.n_sessions == 3
    assert summary.mean_move_pct == pytest.approx((1.0 - 0.5 + 0.31) / 3, abs=1e-3)
    assert summary.std_move_pct is not None
    assert summary.min_move_pct == pytest.approx(-0.5)
    assert summary.max_move_pct == pytest.approx(1.0)
    assert summary.pct_upside == pytest.approx(2 / 3 * 100.0, abs=0.1)  # +1.0% and +0.31%
    assert summary.pct_downside == pytest.approx(1 / 3 * 100.0, abs=0.1)  # -0.5%
    assert sum(summary.bucket_counts.values()) == 3


def test_summarize_history_std_is_none_for_single_record(tmp_path):
    path = tmp_path / "log.csv"
    cas_history._write_all(
        [CASSessionRecord(SESSION_DATE, "NIFTY", 100.0, 100.2, 0.2, datetime(2026, 8, 25, 16, 0))], path
    )
    summary = summarize_history("NIFTY", path=path)
    assert summary.n_sessions == 1
    assert summary.std_move_pct is None
