"""
tests/unit/test_agg_trades_flow.py
─────────────────────────────────────────────────────────────────────────────
Data V2 steps 7/8: aggTrades -> 1m/5m flow. Covers the aggressor-sign gate
(step 8: aggressive_buy_usd + aggressive_sell_usd == total traded USD, no
artificial 50/50) and the batching fix for a real OOM this script caused in
production: an earlier version submitted a symbol's ENTIRE day list to the
ThreadPoolExecutor at once, and got killed by the kernel OOM-killer at
~29GB RSS on a 31GB host after finishing only one (tiny) symbol. Batching
(build_agg_trades_flow.py::_chunked) bounds how many raw per-day trade
frames can be in flight at once and checkpoints the manifest after every
batch, not just at symbol end.

Gate:
    python3 -m pytest tests/unit/test_agg_trades_flow.py -q
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.normalized.agg_trades import build_agg_trades_flow as flow_mod


def _fake_trades(d: date, n: int = 1000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed + d.toordinal())
    idx = pd.to_datetime(d) + pd.to_timedelta(rng.integers(0, 86_400, n), unit="s")
    idx = idx.tz_localize("UTC")
    price = 100 + np.cumsum(rng.normal(0, 0.01, n))
    qty = rng.uniform(0.01, 2.0, n)
    is_buyer_maker = rng.random(n) > 0.5
    df = pd.DataFrame({
        "price": price, "quantity": qty, "is_buyer_maker": is_buyer_maker,
    }, index=idx).sort_index()
    df.index.name = "timestamp"
    df["usd"] = df["price"] * df["quantity"]
    df["aggressive_buy_usd"] = np.where(~df["is_buyer_maker"], df["usd"], 0.0)
    df["aggressive_sell_usd"] = np.where(df["is_buyer_maker"], df["usd"], 0.0)
    df["aggressive_buy_qty"] = np.where(~df["is_buyer_maker"], df["quantity"], 0.0)
    df["aggressive_sell_qty"] = np.where(df["is_buyer_maker"], df["quantity"], 0.0)
    return df


def test_chunked_respects_batch_size():
    items = list(range(23))
    chunks = list(flow_mod._chunked(items, 5))
    assert [len(c) for c in chunks] == [5, 5, 5, 5, 3]
    assert sum(chunks, []) == items


def test_aggregate_bars_gate_buy_plus_sell_equals_total():
    trades = _fake_trades(date(2024, 1, 1), n=5000)
    bars = flow_mod.aggregate_bars(trades, "5min")
    classified = bars["aggressive_buy_usd"].sum() + bars["aggressive_sell_usd"].sum()
    assert classified == pytest.approx(trades["usd"].sum(), rel=1e-9)


def test_aggregate_bars_no_artificial_50_50():
    # skewed aggressor mix (80% buys) -- a naive 50/50 split would fail this
    rng = np.random.default_rng(0)
    idx = pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 86_400, 2000), unit="s")
    idx = idx.tz_localize("UTC")
    price = np.full(2000, 100.0)
    qty = np.full(2000, 1.0)
    is_buyer_maker = rng.random(2000) > 0.8  # 80% False -> 80% aggressive buy
    df = pd.DataFrame({"price": price, "quantity": qty, "is_buyer_maker": is_buyer_maker}, index=idx).sort_index()
    df.index.name = "timestamp"
    df["usd"] = df["price"] * df["quantity"]
    df["aggressive_buy_usd"] = np.where(~df["is_buyer_maker"], df["usd"], 0.0)
    df["aggressive_sell_usd"] = np.where(df["is_buyer_maker"], df["usd"], 0.0)
    df["aggressive_buy_qty"] = np.where(~df["is_buyer_maker"], df["quantity"], 0.0)
    df["aggressive_sell_qty"] = np.where(df["is_buyer_maker"], df["quantity"], 0.0)

    bars = flow_mod.aggregate_bars(df, "5min")
    buy_share = bars["aggressive_buy_usd"].sum() / (bars["aggressive_buy_usd"].sum() + bars["aggressive_sell_usd"].sum())
    assert buy_share == pytest.approx(0.8, abs=0.05)


def test_cvd_is_cumulative_across_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(flow_mod, "OUT_1M", tmp_path / "1m/venue=binance")
    monkeypatch.setattr(flow_mod, "OUT_5M", tmp_path / "5m/venue=binance")

    days_fetched: list[date] = []

    def fake_fetch_day(symbol, d):
        days_fetched.append(d)
        return _fake_trades(d, n=200)

    monkeypatch.setattr(flow_mod, "fetch_day", fake_fetch_day)

    r = flow_mod.build_symbol("FAKEUSDT", date(2024, 1, 1), date(2024, 1, 10), workers=2, batch_days=2)
    assert r["new_days"] == 10
    assert sorted(days_fetched) == [date(2024, 1, 1) + pd.Timedelta(days=i) for i in range(10)]

    out = pd.read_parquet(flow_mod.OUT_5M / "symbol=FAKEUSDT/year=2024/flow.parquet")
    out = out.sort_values("timestamp")
    # CVD must be a running cumulative sum of signed_volume across the WHOLE
    # symbol history, not reset at each batch boundary.
    expected_cvd = out["signed_volume"].cumsum()
    np.testing.assert_allclose(out["CVD"].to_numpy(), expected_cvd.to_numpy(), rtol=1e-9)


def test_cvd_canonicalized_after_earlier_data_inserted_later(tmp_path, monkeypatch):
    """The real bug found running the InstrumentMaster V2 delta backfill on
    AIAUSDT: a first build_symbol call for [Jan 5, Jan 10] writes CVD
    starting from 0 (correct, at the time). A LATER call (a delta backfill
    revealing an earlier true listing_ts) for [Jan 1, Jan 4] must not just
    tack those days on with their own local 0-based CVD, and must not
    leave the Jan 5-10 days' CVD un-shifted -- the WHOLE series must end up
    as one true cumulative sum from Jan 1 onward."""
    monkeypatch.setattr(flow_mod, "OUT_1M", tmp_path / "1m/venue=binance")
    monkeypatch.setattr(flow_mod, "OUT_5M", tmp_path / "5m/venue=binance")
    monkeypatch.setattr(flow_mod, "fetch_day", lambda symbol, d: _fake_trades(d, n=200))

    # first pass: symbol appears to start Jan 5 (its "listing_ts" at the time)
    flow_mod.build_symbol("AIAUSDT", date(2024, 1, 5), date(2024, 1, 10), workers=2, batch_days=3)
    # second pass: InstrumentMaster V2 proved it actually existed from Jan 1
    flow_mod.build_symbol("AIAUSDT", date(2024, 1, 1), date(2024, 1, 4), workers=2, batch_days=3)

    out = pd.read_parquet(flow_mod.OUT_5M / "symbol=AIAUSDT/year=2024/flow.parquet")
    out = out.sort_values("timestamp").reset_index(drop=True)
    # true canonical CVD: one cumsum over the WHOLE, correctly-ordered history
    expected_cvd = out["signed_volume"].cumsum()
    np.testing.assert_allclose(out["CVD"].to_numpy(), expected_cvd.to_numpy(), rtol=1e-9)
    # and the manifest's carried last_cvd must match the true final value,
    # not whatever the second (earlier-days-only) batch computed locally
    manifest = json.loads((flow_mod.OUT_1M / "symbol=AIAUSDT/manifest.json").read_text())
    assert manifest["last_cvd_5m"] == pytest.approx(float(expected_cvd.iloc[-1]))


def test_canonicalize_cvd_is_noop_safe_on_empty_symbol(tmp_path):
    result = flow_mod.canonicalize_cvd(tmp_path / "5m/venue=binance", "GHOSTUSDT")
    assert result is None


def test_manifest_checkpoints_after_each_batch_not_only_at_symbol_end(tmp_path, monkeypatch):
    monkeypatch.setattr(flow_mod, "OUT_1M", tmp_path / "1m/venue=binance")
    monkeypatch.setattr(flow_mod, "OUT_5M", tmp_path / "5m/venue=binance")

    call_count = {"n": 0}

    def flaky_fetch_day(symbol, d):
        call_count["n"] += 1
        if call_count["n"] > 4:  # die partway through the 2nd batch (batch_days=2, workers=2 -> 4/batch)
            raise RuntimeError("simulated crash mid-symbol")
        return _fake_trades(d, n=100)

    monkeypatch.setattr(flow_mod, "fetch_day", flaky_fetch_day)

    with pytest.raises(RuntimeError):
        flow_mod.build_symbol("FLAKYUSDT", date(2024, 1, 1), date(2024, 1, 10), workers=2, batch_days=2)

    manifest_path = tmp_path / "1m/venue=binance/symbol=FLAKYUSDT/manifest.json"
    assert manifest_path.exists(), "first batch must be checkpointed to disk before the crash in batch 2"
    import json
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["done_days"]) == 4  # exactly the first (successful) batch, not 0 and not all 10


def test_gate_mismatch_is_fail_closed_not_written_not_done(tmp_path, monkeypatch):
    """A day whose aggressor split doesn't reconcile to total USD must be
    written NOWHERE and marked done NOWHERE -- an earlier version still
    wrote the (silently wrong) bars and checkpointed the day as done,
    making a corrupted day indistinguishable from a good one downstream."""
    monkeypatch.setattr(flow_mod, "OUT_1M", tmp_path / "1m/venue=binance")
    monkeypatch.setattr(flow_mod, "OUT_5M", tmp_path / "5m/venue=binance")

    bad_day = date(2024, 1, 3)

    def fake_fetch_day(symbol, d):
        return _fake_trades(d, n=200)

    real_aggregate_bars = flow_mod.aggregate_bars

    def corrupting_aggregate_bars(trades, freq):
        bars = real_aggregate_bars(trades, freq)
        if trades.index[0].date() == bad_day:
            bars["aggressive_buy_usd"] = bars["aggressive_buy_usd"] * 0.5  # break reconciliation
        return bars

    monkeypatch.setattr(flow_mod, "fetch_day", fake_fetch_day)
    monkeypatch.setattr(flow_mod, "aggregate_bars", corrupting_aggregate_bars)

    r = flow_mod.build_symbol("GATEUSDT", date(2024, 1, 1), date(2024, 1, 5), workers=2, batch_days=5)
    assert r["gate_fail_days"] == 1

    manifest_path = tmp_path / "1m/venue=binance/symbol=GATEUSDT/manifest.json"
    import json
    manifest = json.loads(manifest_path.read_text())
    assert bad_day.isoformat() in manifest["failed_days"]
    assert bad_day.isoformat() not in manifest["done_days"]

    out = pd.read_parquet(flow_mod.OUT_5M / "symbol=GATEUSDT/year=2024/flow.parquet")
    written_days = set(pd.to_datetime(out["timestamp"]).dt.date)
    assert bad_day not in written_days  # nothing from the mismatched day made it to disk
    assert len(written_days) == 4  # the other 4 days of the 5-day window did

    # failed_days must be retryable: re-running with the bug "fixed" should pick it up
    monkeypatch.setattr(flow_mod, "aggregate_bars", real_aggregate_bars)
    r2 = flow_mod.build_symbol("GATEUSDT", date(2024, 1, 1), date(2024, 1, 5), workers=2, batch_days=5)
    assert r2["new_days"] == 1  # only the previously-failed day is retried
    manifest = json.loads(manifest_path.read_text())
    assert bad_day.isoformat() in manifest["done_days"]
    assert bad_day.isoformat() not in manifest["failed_days"]  # cleared once repaired, not stuck forever
