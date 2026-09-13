from datetime import date, timedelta

import pytest

from app.analytics import dsrd
from app.analytics import technicals as tech
from app.core.black_scholes import OptionType, greeks as bs_greeks
from app.core.pop import leg_pop_delta_approx
from app.data.models import ChainRow, LegQuote, OptionChain


def _bar(day_offset: int, o: float, h: float, low: float, c: float, v: int = 1000) -> tech.DailyBar:
    return tech.DailyBar(date(2024, 1, 1) + timedelta(days=day_offset), o, h, low, c, v)


def _flat_closes(value: float, n: int) -> list[float]:
    return [value] * n


def _monotonic_closes(start: float, step: float, n: int) -> list[float]:
    return [start + step * i for i in range(n)]


# ---------------------------------------------------------------------------
# rsi_zone / Strategy Matrix / direction_signal
# ---------------------------------------------------------------------------


def test_rsi_zone_boundaries():
    assert dsrd.rsi_zone(None) == "unknown"
    assert dsrd.rsi_zone(39.9) == "low"
    assert dsrd.rsi_zone(40.0) == "mid"
    assert dsrd.rsi_zone(59.9) == "mid"
    assert dsrd.rsi_zone(60.0) == "high"


def test_direction_signal_strong_bullish():
    # All-gains RSI -> 100 ("high") on both timeframes.
    daily = _monotonic_closes(100.0, 1.0, 20)
    weekly = _monotonic_closes(100.0, 1.0, 20)
    sig = dsrd.direction_signal(daily, weekly)
    assert sig.condition == "Strong Bullish"
    assert sig.strategy == "Hedge Put"
    assert sig.bias == "bullish"
    assert sig.in_matrix is True


def test_direction_signal_strong_bearish():
    daily = _monotonic_closes(100.0, -1.0, 20)
    weekly = _monotonic_closes(100.0, -1.0, 20)
    sig = dsrd.direction_signal(daily, weekly)
    assert sig.condition == "Strong Bearish"
    assert sig.strategy == "Hedge Call"
    assert sig.bias == "bearish"


def test_direction_signal_sideways_neutral():
    # Alternating +1/-1 -> RSI settles at 50 ("mid") both timeframes.
    closes = [100.0]
    for i in range(30):
        closes.append(closes[-1] + (1.0 if i % 2 == 0 else -1.0))
    sig = dsrd.direction_signal(closes, closes)
    assert sig.condition == "Sideways/Neutral"
    assert sig.strategy == "Iron Condor"
    assert sig.bias == "neutral"


def test_direction_signal_outside_matrix_is_reported_as_conflicting_not_guessed():
    # Weekly strongly bullish (all gains -> 100/"high"), daily strongly
    # bearish (all losses -> 0/"low") — genuinely not one of the 5 rows.
    daily = _monotonic_closes(100.0, -1.0, 20)
    weekly = _monotonic_closes(100.0, 1.0, 20)
    sig = dsrd.direction_signal(daily, weekly)
    assert sig.in_matrix is False
    assert sig.bias == "neutral"
    assert "Conflicting" in sig.condition


# ---------------------------------------------------------------------------
# Multi-timeframe support/resistance
# ---------------------------------------------------------------------------


def test_multi_timeframe_support_resistance_targets_follow_bias():
    # A gentle uptrend for a year+ of daily bars gives every timeframe a
    # real window to work with.
    bars = [_bar(i, 100 + i * 0.1, 100 + i * 0.1 + 1, 100 + i * 0.1 - 1, 100 + i * 0.1 + 0.2) for i in range(500)]
    spot = bars[-1].close

    bullish = dsrd.multi_timeframe_support_resistance(bars, spot, "bullish")
    for level in bullish.values():
        assert level.target == level.resistance
        assert level.target_kind == "resistance"

    bearish = dsrd.multi_timeframe_support_resistance(bars, spot, "bearish")
    for level in bearish.values():
        assert level.target == level.support
        assert level.target_kind == "support"

    neutral = dsrd.multi_timeframe_support_resistance(bars, spot, "neutral")
    for level in neutral.values():
        assert level.target is None
        assert level.target_kind == "none"

    assert set(bullish.keys()) == {"daily", "weekly", "monthly"}


# ---------------------------------------------------------------------------
# Confirmation signals
# ---------------------------------------------------------------------------


def test_confirmation_signals_flags_breakout_above_upper_band():
    # Flat for a long stretch (tight bands), then a sharp jump above the
    # upper band on the last bar.
    bars = [_bar(i, 100, 100.5, 99.5, 100.0) for i in range(60)]
    bars.append(_bar(60, 100, 112, 99.5, 111.0))
    signals = dsrd.confirmation_signals(bars, support=95.0, resistance=105.0)
    assert signals.breakout == "bullish"


def test_confirmation_signals_no_breakout_on_ordinary_bars():
    bars = [_bar(i, 100 + (i % 3), 101 + (i % 3), 99 + (i % 3), 100 + (i % 3)) for i in range(60)]
    signals = dsrd.confirmation_signals(bars, support=95.0, resistance=105.0)
    assert signals.breakout == "none"


def test_confirmation_signals_candlestick_at_sr_true_when_near_level():
    bars = [_bar(i, 100, 100.6, 99.6, 100.1) for i in range(30)]
    # Hammer right at support=90.
    bars.append(_bar(30, 90.2, 90.5, 85.0, 90.3))
    signals = dsrd.confirmation_signals(bars, support=90.0, resistance=110.0)
    assert signals.candlestick is not None and signals.candlestick[1] == "bullish"
    assert signals.candlestick_at_sr is True


def test_confirmation_signals_candlestick_away_from_sr_is_flagged_as_not_at_sr():
    bars = [_bar(i, 100, 100.6, 99.6, 100.1) for i in range(30)]
    bars.append(_bar(30, 100.2, 100.5, 95.0, 100.3))  # hammer, but nowhere near support=50
    signals = dsrd.confirmation_signals(bars, support=50.0, resistance=110.0)
    assert signals.candlestick is not None
    assert signals.candlestick_at_sr is False


# ---------------------------------------------------------------------------
# Conviction scoring
# ---------------------------------------------------------------------------


def _direction(bias, in_matrix=True, condition=None, strategy=None):
    condition = condition or {"bullish": "Strong Bullish", "bearish": "Strong Bearish", "neutral": "Sideways/Neutral"}[bias]
    strategy = strategy or {"bullish": "Hedge Put", "bearish": "Hedge Call", "neutral": "Iron Condor"}[bias]
    return dsrd.DirectionSignal(70.0, 70.0, "high", "high", condition, strategy, bias, in_matrix)


def _signals(**overrides):
    base = dict(adx=30.0, trend_strength="strong", squeeze=False, breakout="none", divergence="none", candlestick=None, candlestick_at_sr=False)
    base.update(overrides)
    return dsrd.ConfirmationSignals(**base)


def test_conviction_all_factors_aligned_is_high_tier():
    direction = _direction("bullish")
    signals = _signals(breakout="bullish", divergence="bullish", candlestick=("Hammer", "bullish"), candlestick_at_sr=True)
    conviction = dsrd.score_conviction(direction, signals)
    assert conviction.tier == "High"
    assert conviction.score == 2 + 1 + 1 + 1 + 1  # direction, adx, squeeze/breakout, divergence, candlestick


def test_conviction_breakout_against_bias_is_a_strong_penalty():
    direction = _direction("bullish")
    signals = _signals(breakout="bearish")
    conviction = dsrd.score_conviction(direction, signals)
    breakout_factor = next(f for f in conviction.factors if f.name == "Bollinger squeeze")
    assert breakout_factor.contribution == -2
    assert "conflicts" in breakout_factor.note


def test_conviction_divergence_against_bias_warns():
    direction = _direction("bullish")
    signals = _signals(divergence="bearish")
    conviction = dsrd.score_conviction(direction, signals)
    div_factor = next(f for f in conviction.factors if f.name == "RSI divergence")
    assert div_factor.contribution == -2


def test_conviction_candlestick_ignored_when_not_at_sr():
    direction = _direction("bullish")
    signals = _signals(candlestick=("Shooting Star", "bearish"), candlestick_at_sr=False)
    conviction = dsrd.score_conviction(direction, signals)
    candle_factor = next(f for f in conviction.factors if f.name == "Candlestick @ S/R")
    assert candle_factor.contribution == 0
    assert "noise" in candle_factor.note.lower() or "away" in candle_factor.note.lower()


def test_conviction_neutral_strategy_penalizes_strong_trend_and_breakout():
    direction = _direction("neutral")
    signals = _signals(trend_strength="strong", breakout="bullish")
    conviction = dsrd.score_conviction(direction, signals)
    adx_factor = next(f for f in conviction.factors if "ADX" in f.name)
    assert adx_factor.contribution == -1
    breakout_factor = next(f for f in conviction.factors if f.name == "Bollinger squeeze")
    assert breakout_factor.contribution == -2


def test_conviction_outside_matrix_scores_negative_direction_factor():
    direction = _direction("neutral", in_matrix=False, condition="Conflicting (outside documented matrix)", strategy="Wait")
    signals = _signals()
    conviction = dsrd.score_conviction(direction, signals)
    direction_factor = conviction.factors[0]
    assert direction_factor.contribution == -1


def test_conviction_tier_thresholds():
    assert dsrd._tier_for_score(4) == "High"
    assert dsrd._tier_for_score(2) == "Moderate"
    assert dsrd._tier_for_score(0) == "Low"
    assert dsrd._tier_for_score(-1) == "Avoid/Wait"


# ---------------------------------------------------------------------------
# Strike selection
# ---------------------------------------------------------------------------


def _make_chain(spot=24800.0, t=7 / 365, r=0.065, sigma=0.12, strikes=None) -> OptionChain:
    strikes = strikes or [spot + i * 50 for i in range(-30, 31)]
    rows = []
    for strike in strikes:
        call_g = bs_greeks(spot, strike, t, r, sigma, OptionType.CALL)
        put_g = bs_greeks(spot, strike, t, r, sigma, OptionType.PUT)
        call = LegQuote(OptionType.CALL, 10.0, 9.5, 10.5, 1000, 0, 100, sigma, call_g, f"NIFTY_{strike:.0f}_CE")
        put = LegQuote(OptionType.PUT, 10.0, 9.5, 10.5, 1000, 0, 100, sigma, put_g, f"NIFTY_{strike:.0f}_PE")
        rows.append(ChainRow(strike=strike, call=call, put=put))
    return OptionChain(
        symbol="NIFTY", spot=spot, expiry=date.today() + timedelta(days=7), timestamp=None,
        time_to_expiry_years=t, risk_free_rate=r, prev_close=spot, rows=rows,
    )


def test_select_sell_strikes_filters_by_delta_band_both_sides():
    chain = _make_chain()
    result = dsrd.select_sell_strikes(chain, support=chain.spot - 1000, resistance=chain.spot + 1000)

    for c in result["calls"]:
        assert dsrd.DELTA_LOW <= c.delta <= dsrd.DELTA_HIGH
    for p in result["puts"]:
        assert dsrd.DELTA_LOW <= abs(p.delta) <= dsrd.DELTA_HIGH
    assert len(result["calls"]) > 0
    assert len(result["puts"]) > 0


def test_select_sell_strikes_pop_matches_pop_engine():
    chain = _make_chain()
    result = dsrd.select_sell_strikes(chain, support=chain.spot - 1000, resistance=chain.spot + 1000)
    candidate = result["calls"][0]
    expected_pop = leg_pop_delta_approx(
        chain.spot, candidate.strike, chain.time_to_expiry_years, chain.risk_free_rate, 0.12, OptionType.CALL, is_seller=True
    )
    assert candidate.probability_of_profit == pytest.approx(expected_pop)


def test_select_sell_strikes_alignment_flag_and_sort_order():
    chain = _make_chain()
    # Resistance set very far out -> no call candidate can be "aligned".
    result = dsrd.select_sell_strikes(chain, support=chain.spot - 1000, resistance=chain.spot + 100000)
    assert all(c.aligned_with_sr is False for c in result["calls"])

    # Resistance set at spot -> every OTM call candidate is aligned.
    result2 = dsrd.select_sell_strikes(chain, support=chain.spot - 1000, resistance=chain.spot)
    assert all(c.aligned_with_sr is True for c in result2["calls"])


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------


def test_checklist_all_pass_when_aligned_strike_exists():
    chain = _make_chain()
    direction = _direction("bullish")
    sr = dsrd.multi_timeframe_support_resistance(
        [_bar(i, chain.spot, chain.spot + 1, chain.spot - 1, chain.spot) for i in range(60)], chain.spot, "bullish"
    )
    strikes = dsrd.select_sell_strikes(chain, support=chain.spot, resistance=chain.spot)
    items = dsrd.build_checklist(direction, sr, strikes)
    by_step = {item.step: item for item in items}
    assert by_step["Direction"].passed is True
    assert by_step["Support"].passed is True
    assert by_step["Resistance"].passed is True
    assert by_step["Delta"].passed is True  # puts are the relevant side for a bullish bias, all aligned


def test_checklist_delta_fails_when_no_aligned_candidate_on_relevant_side():
    chain = _make_chain()
    direction = _direction("bullish")
    sr = dsrd.multi_timeframe_support_resistance(
        [_bar(i, chain.spot, chain.spot + 1, chain.spot - 1, chain.spot) for i in range(60)], chain.spot, "bullish"
    )
    # Support far below spot -> no put candidate can be aligned for a bullish (sell-put) view.
    strikes = dsrd.select_sell_strikes(chain, support=chain.spot - 100000, resistance=chain.spot)
    items = dsrd.build_checklist(direction, sr, strikes)
    by_step = {item.step: item for item in items}
    assert by_step["Delta"].passed is False
