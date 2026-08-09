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
