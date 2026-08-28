"""tests/truth/test_margin.py -- exposures, margin sizing, liquidation trigger."""
from __future__ import annotations

import pytest

from src.futur.truth.account import Account
from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    FillPayload,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderSubmittedPayload,
    ProductSpec,
    ProductType,
)
from src.futur.truth.margin import (
    MarginConfig,
    can_open_additional_notional,
    compute_exposures,
    compute_margin_state,
    should_liquidate,
)
from src.futur.truth.orders import OrderSide, OrderType

PERP = ProductSpec(venue="TESTX", symbol="BTCUSD-PERP", type=ProductType.LINEAR_PERP,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.5, lot_size=0.001)
PERP2 = ProductSpec(venue="OTHERX", symbol="ETHUSD-PERP", type=ProductType.LINEAR_PERP,
                   base_ccy="ETH", quote_ccy="USD", tick_size=0.5, lot_size=0.001)


def _ev(event_type, payload, event_id="e") -> Event:
    ts = "2026-01-01T00:00:00Z"
    return Event(event_id=event_id, event_type=event_type, ts_event=ts, ts_received=ts,
                payload=payload)


def _deposit(account: Account, amount: float, eid: str = "dep") -> None:
    account.apply_event(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(amount, "USD"), eid))


def _open(account: Account, instrument: ProductSpec, order_id: str, side: OrderSide,
         quantity: float, price: float) -> None:
    account.apply_event(_ev(EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id=order_id, client_order_id=f"c-{order_id}", instrument=instrument,
        side=side.value, order_type=OrderType.MARKET.value, quantity=quantity),
        f"sub-{order_id}"))
    account.apply_event(_ev(EventType.ORDER_ACKNOWLEDGED,
                            OrderAcknowledgedPayload(order_id), f"ack-{order_id}"))
    account.apply_event(_ev(EventType.FILL, FillPayload(
        fill_id=f"f-{order_id}", order_id=order_id, instrument=instrument, price=price,
        quantity=quantity, side=side.value, fee=0.0, fee_ccy="USD"), f"fill-{order_id}"))


def test_margin_config_rejects_maintenance_above_initial():
    with pytest.raises(ValueError):
        MarginConfig(initial_margin_rate=0.05, maintenance_margin_rate=0.10)
    with pytest.raises(ValueError):
        MarginConfig(initial_margin_rate=0.10, maintenance_margin_rate=0.0)


def test_exposures_long_and_short_across_two_venues():
    account = Account()
    _deposit(account, 500_000.0)
    _open(account, PERP, "o1", OrderSide.BUY, 2.0, price=50_000.0)     # long 100k
    _open(account, PERP2, "o2", OrderSide.SELL, 10.0, price=3_000.0)   # short 30k
    exp = compute_exposures(account)
    assert exp.long_exposure == pytest.approx(100_000.0)
    assert exp.short_exposure == pytest.approx(30_000.0)
    assert exp.net_exposure == pytest.approx(70_000.0)
    assert exp.perp_gross == pytest.approx(130_000.0)
    assert exp.total_gross == pytest.approx(130_000.0)
    assert exp.exposure_by_venue == {"TESTX": pytest.approx(100_000.0),
                                     "OTHERX": pytest.approx(30_000.0)}
    assert exp.exposure_by_asset["BTCUSD-PERP"] == pytest.approx(100_000.0)


def test_exposures_computed_fresh_not_cached_after_a_mark_moves_price():
    """Directly proves the "never independently updated" requirement:
    calling compute_exposures again after a MARK reflects the new price
    with no extra bookkeeping step required."""
    account = Account()
    _deposit(account, 500_000.0)
    _open(account, PERP, "o1", OrderSide.BUY, 2.0, price=50_000.0)
    exp_before = compute_exposures(account)
    assert exp_before.long_exposure == pytest.approx(100_000.0)   # priced off entry (no mark yet)
    account.apply_event(_ev(EventType.MARK, MarkPayload(PERP, 60_000.0), "m1"))
    exp_after = compute_exposures(account)
    assert exp_after.long_exposure == pytest.approx(120_000.0)    # now priced off the mark


def test_initial_margin_required_matches_formula():
    account = Account()
    _deposit(account, 500_000.0)
    _open(account, PERP, "o1", OrderSide.BUY, 2.0, price=50_000.0)  # notional 100k
    config = MarginConfig(initial_margin_rate=0.10, maintenance_margin_rate=0.05)
    state = compute_margin_state(account, config)
    assert state.perp_notional == pytest.approx(100_000.0)
    assert state.initial_margin_required == pytest.approx(10_000.0)
    assert state.maintenance_margin_required == pytest.approx(5_000.0)


def test_can_open_additional_notional_true_when_headroom_exists():
    account = Account()
    _deposit(account, 500_000.0)
    config = MarginConfig(initial_margin_rate=0.10, maintenance_margin_rate=0.05)
    # no position yet -- collateral_equity = 500k, plenty of headroom for 100k notional
    assert can_open_additional_notional(account, config, 100_000.0) is True


def test_can_open_additional_notional_false_when_it_would_exceed_equity():
    account = Account()
    _deposit(account, 5_000.0)     # thin account
    config = MarginConfig(initial_margin_rate=0.10, maintenance_margin_rate=0.05)
    # 10% IM on 100k = 10k > 5k equity
    assert can_open_additional_notional(account, config, 100_000.0) is False


def test_should_liquidate_false_with_no_perp_exposure():
    account = Account()
    _deposit(account, 100.0)
    config = MarginConfig()
    assert should_liquidate(account, config) is False


def test_should_liquidate_false_when_equity_covers_maintenance():
    account = Account()
    _deposit(account, 500_000.0)
    _open(account, PERP, "o1", OrderSide.BUY, 2.0, price=50_000.0)  # notional 100k, maint 5k
    config = MarginConfig(initial_margin_rate=0.10, maintenance_margin_rate=0.05)
    assert should_liquidate(account, config) is False


def test_should_liquidate_true_once_adverse_mark_breaches_maintenance():
    """Maintenance margin is sized off the CURRENT mark, not the entry
    price -- so it shrinks as the mark falls too, same as a real perp.
    Liquidation only triggers when the position is levered enough that
    unrealized losses outpace the shrinking requirement: equity
    12,000 vs. 100,000 notional (2.0 BTC @ 50,000, ~8.3x) is thin enough
    that a mark of 40,000 (-20%) does trigger it, verified by hand below,
    not just asserted."""
    account = Account()
    _deposit(account, 12_000.0)
    _open(account, PERP, "o1", OrderSide.BUY, 2.0, price=50_000.0)   # notional 100k, IM 10k <= 12k OK
    config = MarginConfig(initial_margin_rate=0.10, maintenance_margin_rate=0.05)
    assert should_liquidate(account, config) is False   # equity 12k > maintenance 5k (@ entry fallback)

    account.apply_event(_ev(EventType.MARK, MarkPayload(PERP, 40_000.0), "crash"))
    # equity = 12,000 + (40,000-50,000)*2.0 = -8,000
    # maintenance = 2.0 * 40,000 * 0.05 = 4,000
    # -8,000 < 4,000 -> liquidate
    assert should_liquidate(account, config) is True
