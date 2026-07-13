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
    monkeypatch.setattr(W, "RAW_ROOT", tmp_path)
    p1 = W.write_records("open_interest", "BTCUSDT", [{"timestamp": 1, "open_interest": 1000.0}])
    p2 = W.write_records("open_interest", "BTCUSDT", [{"timestamp": 2, "open_interest": 1001.0}])
    assert p1 != p2 and p1.exists() and p2.exists()           # 2 parts immutables distinctes
    assert "exchange=binance" in str(p1) and "market=usdm" in str(p1)  # défauts inchangés
    assert pd.read_parquet(p1)["open_interest"].iloc[0] == 1000.0
    # manifest présent + hashé
    import json
    man = p1.with_name(p1.stem + ".manifest.json")
    assert man.exists()
    m = json.loads(man.read_text())
    assert m["rows"] == 1 and len(m["sha256"]) == 64 and m["validation_status"] == "PASS"


def test_writer_multi_exchange_partitions(tmp_path, monkeypatch):
    """Le writer route par exchange/market — bybit ne pollue pas le store binance."""
    import json
    import src.institutional.data.derivatives_collector.writer as W
    monkeypatch.setattr(W, "RAW_ROOT", tmp_path)
    pb = W.write_records("force_order", "BTCUSDT",
                         [{"timestamp": 1, "side": "SELL", "usd": 5000.0}],
                         exchange="bybit", market="linear")
    assert "exchange=bybit" in str(pb) and "market=linear" in str(pb)
    m = json.loads(pb.with_name(pb.stem + ".manifest.json").read_text())
    assert m["exchange"] == "bybit" and m["market"] == "linear"
    assert m["partition_id"].startswith("bybit/linear/force_order/BTCUSDT/")


def test_bybit_side_normalization_matches_binance_convention():
    """Doc Bybit : S=Buy → un LONG a été liquidé. Convention event builder (Binance) :
    side=SELL → long liquidé. Le mapping du collecteur doit préserver ce sens."""
    # reproduit le mapping de collector._bybit_ws_loop
    def norm(side_raw): return "SELL" if side_raw == "Buy" else "BUY"
    assert norm("Buy") == "SELL"    # long liquidé → convention Binance SELL
    assert norm("Sell") == "BUY"    # short liquidé → convention Binance BUY


def test_okx_poll_converts_contracts_and_dedupes(tmp_path, monkeypatch):
    """sz OKX = CONTRATS (ctVal 0.01 BTC…) → usd = sz×ctVal×px ; ts déjà vus ignorés."""
    import src.institutional.data.derivatives_collector.writer as W
    import src.institutional.data.derivatives_collector.collector as C
    monkeypatch.setattr(W, "RAW_ROOT", tmp_path)
    col = C.DerivativesCollector(["BTCUSDT"])
    col._okx_ctval = {"BTC-USDT-SWAP": 0.01}
    col._okx_start_ms = 0
    detail = {"ts": "1000", "bkPx": "60000", "sz": "5", "posSide": "long", "side": "sell"}
    payload = {"data": [{"instId": "BTC-USDT-SWAP",
                         "details": [detail, {**detail, "ts": "2000", "posSide": "short"}]}]}
    monkeypatch.setattr(C, "_okx_get", lambda path: payload)
    col._poll_okx_liq_once()
    parts = list(tmp_path.glob("exchange=okx/market=swap/stream=force_order/**/*.parquet"))
    assert len(parts) == 1
    df = pd.read_parquet(parts[0]).sort_values("timestamp")
    assert len(df) == 2
    assert df["usd"].iloc[0] == 5 * 0.01 * 60000          # 3000$, pas 300 000$
    assert df["side"].tolist() == ["SELL", "BUY"]          # long liq → SELL, short → BUY
    # 2e poll identique : tout ts <= last → aucune nouvelle partition
    col._poll_okx_liq_once()
    assert len(list(tmp_path.glob("exchange=okx/**/*.parquet"))) == 1


def test_event_builder_loads_multi_exchange(tmp_path, monkeypatch):
    """load_force_orders lit binance/usdm ET bybit/linear, tague l'exchange."""
    import src.institutional.data.derivatives_collector.writer as W
    import src.institutional.events.live_event_builder as B
    monkeypatch.setattr(W, "RAW_ROOT", tmp_path)
    monkeypatch.setattr(B, "RAW_ROOT", tmp_path)
    W.write_records("force_order", "BTCUSDT",
                    [{"timestamp": 1_700_000_000_000, "side": "SELL", "usd": 1000.0}])
    W.write_records("force_order", "ETHUSDT",
                    [{"timestamp": 1_700_000_060_000, "side": "BUY", "side_raw": "Sell",
                      "usd": 2000.0}],
                    exchange="bybit", market="linear")
    fo = B.load_force_orders()
    assert len(fo) == 2
    assert set(fo["exchange"]) == {"binance", "bybit"}
    assert fo.sort_values("timestamp")["side"].tolist() == ["SELL", "BUY"]
