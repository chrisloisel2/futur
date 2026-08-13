"""
tests/unit/test_backfill_binance_metrics_vision.py
─────────────────────────────────────────────────────────────────────────────
scripts/backfill_binance_metrics_vision.py: the canonical OI Vision metrics
backfill (writes data/derivatives_backfill/binance_vision_metrics/
{symbol}_metrics_5m.parquet, the exact path DATA_V2_READINESS.json's
oi_vision_5m dataset reads).

Fix (2026-08-13): default --symbols was CORE_12 (12 symbols) -- a plain
re-run of this script silently never refreshed the other ~300 symbols in
the PIT universe, which is why oi_vision_5m staleness quietly regressed
(252/312 -> 44/312 passing) while this session's active backfill attention
was on aggTrades. default_symbols() now reads the full PIT universe from
instrument_master, same pattern already used for the funding top-up.

Gate:
    python3 -m pytest tests/unit/test_backfill_binance_metrics_vision.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import backfill_binance_metrics_vision as bmv


def test_default_symbols_reads_full_pit_universe(tmp_path, monkeypatch):
    im_path = tmp_path / "instrument_master.parquet"
    pd.DataFrame({"symbol": ["ZZZUSDT", "AAAUSDT", "AAAUSDT"]}).to_parquet(im_path, index=False)
    monkeypatch.setattr(bmv, "INSTRUMENT_MASTER", im_path)
    assert bmv.default_symbols() == ["AAAUSDT", "ZZZUSDT"]  # deduped and sorted, not CORE_12


def test_default_symbols_falls_back_to_core_12_without_instrument_master(tmp_path, monkeypatch):
    monkeypatch.setattr(bmv, "INSTRUMENT_MASTER", tmp_path / "does_not_exist.parquet")
    assert bmv.default_symbols() == bmv.CORE_12


def test_main_stops_cleanly_at_disk_floor(tmp_path, monkeypatch, capsys):
    im_path = tmp_path / "instrument_master.parquet"
    pd.DataFrame({"symbol": ["AAAUSDT", "BBBUSDT", "CCCUSDT"]}).to_parquet(im_path, index=False)
    monkeypatch.setattr(bmv, "INSTRUMENT_MASTER", im_path)
    monkeypatch.setattr(bmv, "free_gb", lambda path: 5.0)  # always below any sane floor
    calls = []
    monkeypatch.setattr(bmv, "backfill_symbol", lambda *a, **k: calls.append(a) or {"symbol": a[0], "new": 0})
    monkeypatch.setattr(sys, "argv", ["prog", "--min-free-gb", "15"])
    with pytest.raises(SystemExit) as exc:
        bmv.main()
    assert exc.value.code == 1
    assert not calls  # never even attempted the first symbol -- floor checked up front
    assert "STOP" in capsys.readouterr().out
