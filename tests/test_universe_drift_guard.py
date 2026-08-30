"""tests/test_universe_drift_guard.py — regression guard for the 2026-08-14
universe-drift bug: the event-engine scripts must always trade the frozen
50-symbol universe (configs/portfolio_v1_1_parallel_50.yaml), never whatever
happens to be sitting in data/derivatives_backfill/binance_vision_metrics/.

See reports/edge_discovery/alpha_hunt_2026-08-29/w1_liq_cascade/REPORT.md §1.3
for the incident this guards against.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))

import scripts.run_event_shadow_daily as shadow_daily
import scripts.train_event_engine as train_engine

FROZEN_UNIVERSE = [f"SYM{i}USDT" for i in range(50)]


def _fake_root(tmp_path: Path) -> Path:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "portfolio_v1_1_parallel_50.yaml").write_text(
        yaml.safe_dump({"universe": FROZEN_UNIVERSE}))
    metrics_dir = tmp_path / "data" / "derivatives_backfill" / "binance_vision_metrics"
    metrics_dir.mkdir(parents=True)
    # the 50 legitimate files...
    for sym in FROZEN_UNIVERSE:
        (metrics_dir / f"{sym}_metrics_5m.parquet").write_bytes(b"")
    # ...plus 300 files dropped by an unrelated backfill (the actual incident).
    for i in range(300):
        (metrics_dir / f"DRIFTEDCOIN{i}USDT_metrics_5m.parquet").write_bytes(b"")
    assert len(list(metrics_dir.glob("*_metrics_5m.parquet"))) == 350
    return tmp_path


def test_shadow_daily_ignores_drifted_metrics_dir(tmp_path):
    root = _fake_root(tmp_path)
    symbols = shadow_daily.load_universe(root)
    assert symbols == sorted(FROZEN_UNIVERSE)
    assert len(symbols) == 50


def test_train_event_engine_ignores_drifted_metrics_dir(tmp_path):
    root = _fake_root(tmp_path)
    symbols = train_engine.load_universe(root)
    assert symbols == sorted(FROZEN_UNIVERSE)
    assert len(symbols) == 50
