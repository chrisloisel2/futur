"""tests/test_live_event_builder.py — catalogage liquidations → events (causal + labels)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

import src.institutional.events.live_event_builder as EB


def _setup(tmp, monkeypatch):
    # 2 liquidations BTC dans la même fenêtre 5min : un LONG liquidé (SELL) gros + un SHORT (BUY) petit
    base = pd.Timestamp("2024-06-01T00:00:00Z")
    recs = pd.DataFrame({
        "timestamp": [int(base.timestamp() * 1000), int((base + pd.Timedelta(minutes=1)).timestamp() * 1000)],
        "symbol": ["BTCUSDT", "BTCUSDT"], "side": ["SELL", "BUY"],
        "price": [60000.0, 60010.0], "qty": [10.0, 1.0], "usd": [600000.0, 60000.0],
    })
    p = (tmp / "exchange=binance" / "market=usdm" / "stream=force_order"
         / "symbol=BTCUSDT" / "date=2024-06-01" / "part-1.parquet")
    p.parent.mkdir(parents=True, exist_ok=True)
    recs.to_parquet(p, index=False)
    monkeypatch.setattr(EB, "RAW_ROOT", tmp)
    # prix : hausse après l'event → rebond du long flush
    idx = pd.date_range("2024-06-01", periods=48, freq="1h", tz="UTC")
    px = pd.Series(60000 * np.cumprod(1 + np.full(48, 0.001)), index=idx)
    monkeypatch.setattr(EB, "_price", lambda s: px)


def test_event_clustering_and_side(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    ev = EB.build_events()
    assert len(ev) == 1                              # 2 liqs même fenêtre 5min → 1 event
    row = ev.iloc[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["total_usd"] == 660000.0
    assert row["liquidation_side"] == "LONG_LIQ"    # SELL (600k) > BUY (60k)
    assert row["significant"] == 1                  # ≥ 250k$


def test_forward_labels_after_event(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    ev = EB.build_events()
    row = ev.iloc[0]
    assert row["label_available"] == 1
    assert row["forward_return_4h"] > 0             # prix monte après → rebond
    assert row["MFE_4h"] >= row["forward_return_1h"]  # max favorable ≥ ret court


def test_empty_when_no_liquidations(tmp_path, monkeypatch):
    monkeypatch.setattr(EB, "RAW_ROOT", tmp_path / "empty")
    assert EB.build_events().empty
