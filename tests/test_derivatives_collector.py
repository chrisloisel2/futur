"""tests/test_derivatives_collector.py — writer append-only + OI event detector (Phase 1-2)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

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


def test_writer_append_only_partition_and_manifest(tmp_path, monkeypatch):
    import src.institutional.data.derivatives_collector.writer as W
    monkeypatch.setattr(W, "RAW_ROOT", tmp_path / "exchange=binance" / "market=usdm")
    p1 = W.write_records("open_interest", "BTCUSDT", [{"timestamp": 1, "open_interest": 1000.0}])
    p2 = W.write_records("open_interest", "BTCUSDT", [{"timestamp": 2, "open_interest": 1001.0}])
    assert p1 != p2 and p1.exists() and p2.exists()           # 2 parts immutables distinctes
    assert pd.read_parquet(p1)["open_interest"].iloc[0] == 1000.0
    # manifest présent + hashé
    import json
    man = p1.with_name(p1.stem + ".manifest.json")
    assert man.exists()
    m = json.loads(man.read_text())
    assert m["rows"] == 1 and len(m["sha256"]) == 64 and m["validation_status"] == "PASS"
