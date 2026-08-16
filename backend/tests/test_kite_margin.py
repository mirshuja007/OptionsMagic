import pytest

from app.core.black_scholes import OptionType
from app.data.instruments import get_instrument
from app.margin.kite_margin import KiteMarginError, _order_param, fetch_basket_margin
from app.strategy.legs import Leg, Side


def _leg(tradingsymbol="NIFTY26AUG24800CE", side=Side.SHORT, strike=24800.0):
    return Leg(
        option_type=OptionType.CALL,
        strike=strike,
        side=side,
        quantity_lots=1,
        entry_price=100.0,
        iv=0.15,
        tradingsymbol=tradingsymbol,
    )


def test_order_param_requires_tradingsymbol():
    instrument = get_instrument("NIFTY")
    leg_without_symbol = _leg(tradingsymbol="")
    with pytest.raises(KiteMarginError):
        _order_param(leg_without_symbol, instrument)


def test_order_param_maps_side_and_quantity():
    instrument = get_instrument("NIFTY")
    short_leg = _leg(side=Side.SHORT)
    long_leg = _leg(side=Side.LONG)

    short_param = _order_param(short_leg, instrument)
    long_param = _order_param(long_leg, instrument)

    assert short_param["transaction_type"] == "SELL"
    assert long_param["transaction_type"] == "BUY"
    assert short_param["quantity"] == instrument.lot_size
    assert short_param["exchange"] == instrument.kite_options_exchange
    assert short_param["tradingsymbol"] == "NIFTY26AUG24800CE"


def test_fetch_basket_margin_empty_legs_returns_zero():
    instrument = get_instrument("NIFTY")
    result = fetch_basket_margin([], instrument)
    assert result.total_margin == 0.0


def test_fetch_basket_margin_rejects_legs_without_tradingsymbol(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "t")
    from app.data.kite_client import reset_kite_client

    reset_kite_client()
    instrument = get_instrument("NIFTY")
    legs = [_leg(tradingsymbol="")]
    with pytest.raises(KiteMarginError):
        fetch_basket_margin(legs, instrument)
    reset_kite_client()


class FakeKiteMargin:
    def __init__(self, response):
        self._response = response
        self.last_call = None

    def basket_order_margins(self, orders, consider_positions=True, mode=None):
        self.last_call = (orders, consider_positions, mode)
        return self._response


def test_fetch_basket_margin_happy_path(monkeypatch):
    import app.margin.kite_margin as kite_margin_mod

    fake = FakeKiteMargin(
        {"final": {"total": 9047.38, "span": 8000.0, "exposure": 1047.38, "option_premium": 0.0, "orders": []}}
    )
    monkeypatch.setattr(kite_margin_mod, "get_kite_client", lambda: fake)

    instrument = get_instrument("NIFTY")
    legs = [_leg(tradingsymbol="NIFTY26AUG24800CE", side=Side.SHORT), _leg(tradingsymbol="NIFTY26AUG25000CE", side=Side.LONG, strike=25000.0)]

    result = fetch_basket_margin(legs, instrument, consider_positions=False)

    assert result.total_margin == 9047.38
    assert result.span_margin == 8000.0
    assert result.exposure_margin == 1047.38
    assert fake.last_call[1] is False
    assert len(fake.last_call[0]) == 2


def test_fetch_basket_margin_falls_back_to_initial_section(monkeypatch):
    import app.margin.kite_margin as kite_margin_mod

    fake = FakeKiteMargin({"initial": {"total": 5000.0}})
    monkeypatch.setattr(kite_margin_mod, "get_kite_client", lambda: fake)

    instrument = get_instrument("NIFTY")
    result = fetch_basket_margin([_leg()], instrument)
    assert result.total_margin == 5000.0


def test_fetch_basket_margin_sums_per_order_when_no_total(monkeypatch):
    import app.margin.kite_margin as kite_margin_mod

    fake = FakeKiteMargin({"final": {"orders": [{"total": 3000.0}, {"total": 1500.0}]}})
    monkeypatch.setattr(kite_margin_mod, "get_kite_client", lambda: fake)

    instrument = get_instrument("NIFTY")
    result = fetch_basket_margin([_leg()], instrument)
    assert result.total_margin == 4500.0


def test_fetch_basket_margin_raises_on_unexpected_shape(monkeypatch):
    import app.margin.kite_margin as kite_margin_mod

    fake = FakeKiteMargin({"unexpected": "shape"})
    monkeypatch.setattr(kite_margin_mod, "get_kite_client", lambda: fake)

    instrument = get_instrument("NIFTY")
    with pytest.raises(KiteMarginError):
        fetch_basket_margin([_leg()], instrument)
