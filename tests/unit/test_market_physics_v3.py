import numpy as np
import pandas as pd
import pytest

from market_physics_v3.cross_venue import VenueQuote, fair_value
from market_physics_v3.derivatives import cascade_pressure, option_surface_state
from market_physics_v3.execution import execution_metrics
from market_physics_v3.information_audit import cross_sectional_rank_ic, effective_sample_size
from market_physics_v3.microstructure import BookSnapshot, cancellation_imbalance, top_of_book_ofi, trade_flow_features
from market_physics_v3.pipeline import MarketPhysicsStateBuilder, VenueWindow
from market_physics_v3.schema import BookEvent, BookLevel, ExecutionTrace, OptionQuote, TradeEvent, canonical_partition

NS = 1_000_000_000


def snap(ts, bid_qty=10.0, ask_qty=10.0, bid=99.0, ask=101.0):
    return BookSnapshot(ts, (BookLevel(bid, bid_qty), BookLevel(bid - 1, 20)), (BookLevel(ask, ask_qty), BookLevel(ask + 1, 20)))


def test_microprice_moves_toward_ask_when_bid_queue_dominates():
    s = snap(NS, bid_qty=30, ask_qty=10)
    assert s.mid < s.microprice < s.best_ask.price
    assert s.queue_imbalance() > 0


def test_ofi_positive_when_bid_size_added():
    a = snap(NS, bid_qty=10, ask_qty=10)
    b = snap(2 * NS, bid_qty=20, ask_qty=10)
    assert top_of_book_ofi(a, b) > 0


def test_cancellation_imbalance_detects_ask_liquidity_removed():
    events = [
        BookEvent("x", "BTCUSDT", NS, NS, 1, "cancel", "ask", 101, 5),
        BookEvent("x", "BTCUSDT", NS + 1, NS + 1, 2, "cancel", "bid", 99, 1),
    ]
    assert cancellation_imbalance(events) > 0


def test_trade_flow_absorption_high_when_flow_has_little_impact():
    trades = [TradeEvent("x", "BTCUSDT", NS + i, NS + i, str(i), 100, 100, "buy") for i in range(10)]
    low_move = trade_flow_features(trades, 100, 100.01)
    high_move = trade_flow_features(trades, 100, 101)
    assert low_move["absorption_notional_per_bp"] > high_move["absorption_notional_per_bp"]


def test_cross_venue_fair_value_downweights_stale_quote():
    now = 10 * NS
    fresh = VenueQuote("binance", now, 100, 1.0, 1_000_000)
    stale = VenueQuote("slow", now - 5 * NS, 110, 1.0, 1_000_000)
    out = fair_value([fresh, stale], now, half_life_ms=100)
    assert abs(out["fair_value"] - 100) < 0.1
    assert out["weights"]["binance"] > out["weights"]["slow"]


def test_cascade_risk_increases_when_liquidations_exceed_depth():
    out = cascade_pressure({10.0: 20_000_000, 25.0: 80_000_000}, {10.0: 10_000_000, 25.0: 20_000_000})
    assert out["cascade_risk_25bps"] > out["cascade_risk_10bps"]
    assert out["cascade_risk_max"] == out["cascade_risk_25bps"]


def test_option_surface_rr25():
    t = NS
    expiry1 = 100 * NS
    expiry2 = 200 * NS
    qs = [
        OptionQuote("deribit", "BTC", t, t, expiry1, 100, "call", 1, 2, .50, .52, .50),
        OptionQuote("deribit", "BTC", t, t, expiry1, 110, "call", 1, 2, .55, .57, .25),
        OptionQuote("deribit", "BTC", t, t, expiry1, 90, "put", 1, 2, .65, .67, -.25),
        OptionQuote("deribit", "BTC", t, t, expiry1, 100, "put", 1, 2, .52, .54, -.50),
        OptionQuote("deribit", "BTC", t, t, expiry2, 100, "call", 1, 2, .60, .62, .50),
        OptionQuote("deribit", "BTC", t, t, expiry2, 100, "put", 1, 2, .61, .63, -.50),
    ]
    out = option_surface_state(qs, 100)
    assert out["rr25_near"] < 0
    assert out["atm_iv_term_slope"] > 0


def test_execution_metrics_slippage_and_markout():
    tr = ExecutionTrace("o", "binance", "BTCUSDT", "buy", NS, NS + 1, NS + 2, NS + 3, NS + 4, 100, 2, 2, 100.1, .02, False)
    out = execution_metrics(tr, {1000: 100.3})
    assert out["slippage_bps"] > 0
    assert out["markout_1000ms_bps"] > 0
    assert out["implementation_shortfall_bps"] > out["slippage_bps"]


def test_schema_rejects_negative_transport_latency():
    with pytest.raises(ValueError):
        TradeEvent("x", "BTCUSDT", 2 * NS, NS, "1", 100, 1, "buy")


def test_partition_contract():
    p = canonical_partition("trades", "Binance", "btcusdt", "2026-08-15")
    assert p == "market_physics_v3/raw/trades/venue=binance/symbol=BTCUSDT/date=2026-08-15"


def test_state_builder_is_causal_and_keeps_future_trade_out():
    a = snap(NS, 10, 10)
    b = snap(2 * NS, 20, 10)
    past = TradeEvent("binance", "BTCUSDT", 2 * NS - 1, 2 * NS - 1, "p", 100, 1, "buy")
    future = TradeEvent("binance", "BTCUSDT", 2 * NS + 1, 2 * NS + 1, "f", 100, 1000, "buy")
    w = VenueWindow("binance", a, b, trades=[past, future])
    out = MarketPhysicsStateBuilder().build("BTCUSDT", 2 * NS, [w])
    assert out["binance__trade_count"] == 1.0


def test_cross_sectional_rank_ic_recovers_ordering():
    df = pd.DataFrame({"timestamp": [1, 1, 1, 2, 2, 2], "f": [1, 2, 3, 3, 2, 1], "y": [1, 2, 3, 3, 2, 1]})
    ic = cross_sectional_rank_ic(df, "f", "y")
    assert np.allclose(ic.values, 1.0)


def test_effective_sample_size_detects_autocorrelation():
    rng = np.random.default_rng(3)
    e = rng.normal(size=1000)
    x = np.zeros(1000)
    for i in range(1, 1000):
        x[i] = .95 * x[i-1] + e[i]
    assert effective_sample_size(pd.Series(x), 50) < 300
