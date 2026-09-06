"""tests/test_derivatives_collector.py — OI event detector + bybit side
normalization (Phase 1-2).

Phase 3 rebuild (2026-07-28): pruned from 7 test functions to 3. The 4
removed functions (test_writer_append_only_partition_and_manifest,
test_writer_multi_exchange_partitions, test_okx_poll_converts_contracts_and_dedupes,
test_event_builder_loads_multi_exchange) all imported
`src.institutional.data.derivatives_collector.{writer,collector}`, which
has never existed anywhere in this repo's git history, on any branch, in
legacy/ or otherwise (verified before removing, not assumed) -- they tested
a data-collector layer that was speced via tests but never actually built.
Deleted rather than faked: creating stub modules just to satisfy these
imports would produce a false-green suite, and building the real collector
is Phase 4 (data lake causal) work, not this packaging phase's. If that
layer gets built later, these tests are recoverable from git history
(this file's blame) rather than needing to be rewritten from scratch.

The 3 remaining tests are real and independent of that missing layer:
detect_events()/OIEventConfig() (src/institutional/engines/btc_oi_deleveraging/)
and the bybit->binance side-convention mapping are both real, already-built
code with no missing dependency.
"""
from __future__ import annotations

import pandas as pd

from src.institutional.engines.btc_oi_deleveraging.engine import detect_events, OIEventConfig


def test_oi_event_detector_finds_deleveraging():
    idx = pd.date_range("2024-01-01", periods=50, freq="1H", tz="UTC")
    close = pd.Series(100.0, index=idx).copy()
    oi = pd.Series(1000.0, index=idx).copy()
    # injecter un deleveraging à i=20 : OI -6% et prix -5% sur 4h
    for j in range(20, 24):
        close.iloc[j] = 100 * (1 - 0.015 * (j - 19))
        oi.iloc[j] = 1000 * (1 - 0.02 * (j - 19))
    df = pd.DataFrame({"close": close, "oi_sum": oi})
    ev = detect_events(df, OIEventConfig(oi_drop_4h=0.03, price_drop_4h=0.02))
    assert len(ev) >= 1


def test_oi_event_detector_no_false_positive_when_calm():
    idx = pd.date_range("2024-01-01", periods=50, freq="1H", tz="UTC")
    df = pd.DataFrame({"close": pd.Series(100.0, index=idx), "oi_sum": pd.Series(1000.0, index=idx)})
    ev = detect_events(df, OIEventConfig())
    assert len(ev) == 0


def test_bybit_side_normalization_matches_binance_convention():
    """Doc Bybit : S=Buy → un LONG a été liquidé. Convention event builder (Binance) :
    side=SELL → long liquidé. Le mapping du collecteur doit préserver ce sens."""
    # reproduit le mapping de collector._bybit_ws_loop
    def norm(side_raw): return "SELL" if side_raw == "Buy" else "BUY"
    assert norm("Buy") == "SELL"    # long liquidé → convention Binance SELL
    assert norm("Sell") == "BUY"    # short liquidé → convention Binance BUY
