"""Weekly/monthly expiry-day logic in the mock feed.

Current NSE/BSE rules encoded here (verified against NSE/BSE circulars as of
the Aug/Sep 2025 rule change — see app/data/instruments.py's comment for the
citation trail): only Nifty 50 (NSE) and Sensex (BSE) still get weekly
options; everything else on NSE (BankNifty, FinNifty, MidcpNifty, single
stocks) is monthly-only, expiring the last Tuesday of the month. These are
mock-feed-only defaults — kite_feed.py never hardcodes any of this, it just
reads whatever expiries are actually listed.
"""
from datetime import date, timedelta

import pytest

from app.data.instruments import get_instrument
from app.data.mock_feed import (
    _default_expiry,
    _last_weekday_of_month,
    _next_monthly_expiry,
    available_expiries,
    generate_option_chain,
    next_weekly_expiry,
)


def test_next_weekly_expiry_defaults_to_tuesday():
    # 2026-08-16 is a Sunday; next Tuesday is 2026-08-18.
    assert next_weekly_expiry(date(2026, 8, 16)) == date(2026, 8, 18)


def test_next_weekly_expiry_on_the_target_weekday_rolls_to_next_week():
    # If today IS the target weekday, expiry should be 7 days out, not today.
    tuesday = date(2026, 8, 18)
    assert next_weekly_expiry(tuesday, weekday=1) == date(2026, 8, 25)


def test_next_weekly_expiry_supports_other_weekdays():
    # Thursday (Sensex's day), from a Sunday.
    assert next_weekly_expiry(date(2026, 8, 16), weekday=3) == date(2026, 8, 20)


def test_last_weekday_of_month():
    # August 2026: Tuesdays fall on 4, 11, 18, 25 -> last is the 25th.
    assert _last_weekday_of_month(2026, 8, weekday=1) == date(2026, 8, 25)


def test_last_weekday_of_month_handles_december_year_rollover():
    result = _last_weekday_of_month(2026, 12, weekday=1)
    assert result.month == 12
    assert result.weekday() == 1


def test_next_monthly_expiry_rolls_to_next_month_once_this_months_has_passed():
    after_last_tuesday = date(2026, 8, 26)  # one day after 2026-08-25
    result = _next_monthly_expiry(after_last_tuesday, weekday=1)
    assert result == date(2026, 9, 29)  # last Tuesday of September 2026


def test_default_expiry_matches_verified_nse_bse_rules():
    today = date(2026, 8, 16)
    assert _default_expiry(get_instrument("NIFTY"), today) == date(2026, 8, 18)  # weekly Tuesday
    assert _default_expiry(get_instrument("SENSEX"), today) == date(2026, 8, 20)  # weekly Thursday
    assert _default_expiry(get_instrument("BANKNIFTY"), today) == date(2026, 8, 25)  # monthly last Tuesday
    assert _default_expiry(get_instrument("FINNIFTY"), today) == date(2026, 8, 25)
    assert _default_expiry(get_instrument("MIDCPNIFTY"), today) == date(2026, 8, 25)
    assert _default_expiry(get_instrument("RELIANCE"), today) == date(2026, 8, 25)


def test_default_expiry_for_commodities_is_illustrative_offset_not_weekday_based():
    today = date(2026, 8, 16)
    expiry = _default_expiry(get_instrument("CRUDEOIL"), today)
    assert (expiry - today).days == 20


@pytest.mark.parametrize("symbol", ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "RELIANCE"])
def test_default_expiry_is_never_in_the_past(symbol):
    today = date(2026, 8, 16)
    assert _default_expiry(get_instrument(symbol), today) >= today


def test_nifty_and_sensex_defaults_differ():
    """The original bug this suite guards against: Nifty and Sensex used to
    share one hardcoded Thursday default, which is wrong for both today
    (Nifty moved to Tuesday) and structurally wrong going forward (they're
    on different exchanges with independently-set expiry days).
    """
    today = date(2026, 8, 16)
    nifty_expiry = _default_expiry(get_instrument("NIFTY"), today)
    sensex_expiry = _default_expiry(get_instrument("SENSEX"), today)
    assert nifty_expiry != sensex_expiry
    assert nifty_expiry.weekday() == 1  # Tuesday
    assert sensex_expiry.weekday() == 3  # Thursday


def test_available_expiries_weekly_returns_consecutive_tuesdays():
    today = date(2026, 8, 16)
    expiries = available_expiries("NIFTY", today=today, count=4)
    assert expiries == [date(2026, 8, 18), date(2026, 8, 25), date(2026, 9, 1), date(2026, 9, 8)]


def test_available_expiries_monthly_returns_consecutive_last_tuesdays():
    today = date(2026, 8, 16)
    expiries = available_expiries("BANKNIFTY", today=today, count=3)
    assert expiries == [date(2026, 8, 25), date(2026, 9, 29), date(2026, 10, 27)]


def test_available_expiries_commodity_steps_by_30_days_from_the_20_day_offset():
    today = date(2026, 8, 16)
    expiries = available_expiries("CRUDEOIL", today=today, count=3)
    assert expiries == [today + timedelta(days=20), today + timedelta(days=50), today + timedelta(days=80)]


def test_available_expiries_are_all_in_the_future():
    today = date(2026, 8, 16)
    for symbol in ["NIFTY", "SENSEX", "BANKNIFTY", "GOLD"]:
        for expiry in available_expiries(symbol, today=today):
            assert expiry >= today


def test_available_expiries_default_count_is_six():
    assert len(available_expiries("NIFTY")) == 6


def test_chain_spot_is_stable_across_expiry_selection():
    """Spot is a property of the underlying, not of which expiry's chain is
    being viewed — switching expiries in the UI must not silently change it.
    """
    c1 = generate_option_chain("NIFTY", expiry=date(2026, 8, 18))
    c2 = generate_option_chain("NIFTY", expiry=date(2026, 9, 1))
    assert c1.spot == c2.spot


def test_chain_oi_differs_across_expiry_selection():
    """The original bug this guards against: OI (and everything derived from
    it — Max Pain, PCR, GEX, Smart OI Bias) was seeded off symbol only, so
    every expiry showed byte-identical OI once spot/strikes matched —
    invisible until the expiry selector let users compare them side by side.
    """
    c1 = generate_option_chain("NIFTY", expiry=date(2026, 8, 18))
    c2 = generate_option_chain("NIFTY", expiry=date(2026, 9, 1))
    ois1 = [row.call.oi for row in c1.rows]
    ois2 = [row.call.oi for row in c2.rows]
    assert ois1 != ois2
