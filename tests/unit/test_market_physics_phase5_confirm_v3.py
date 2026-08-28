import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from market_physics_v3.phase5_confirm import (
    DISCOVERY_STOP_NS,
    LOCKED_FEATURE,
    run_locked_confirmation,
)


def _frame(hours=0.02, start_ns=DISCOVERY_STOP_NS + 10_000_000_000, cadence_ms=100):
    rows = int(hours * 3600 * 1000 / cadence_ms) + 1
    t = np.arange(rows, dtype=np.int64)
    parts = []
    for i, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        x = np.sin(t / 200.0 + i * 0.2)
        base = 100.0 + i * 10.0 + 0.001 * np.cumsum(x)
        frame = pd.DataFrame({
            "asof_ns": start_ns + t * cadence_ms * 1_000_000,
            "symbol": symbol,
            "price_ready": True,
            "price_fair_value": base,
            "okx__depth_fresh": True,
            LOCKED_FEATURE: x,
        })
        for venue, offset in (("binance", -0.02), ("bybit", 0.0), ("hyperliquid", 0.02), ("okx", 0.01)):
            frame[venue + "__price_mid"] = base + offset
            frame[venue + "__price_weight"] = 1.0
        parts.append(frame)
    return pd.concat(parts, ignore_index=True)


def test_confirmation_refuses_discovery_overlap():
    frame = _frame(start_ns=DISCOVERY_STOP_NS - 1_000_000_000)
    with pytest.raises(ValueError, match="overlaps or predates"):
        run_locked_confirmation(frame, min_duration_hours=0.01, block_shuffle_repeats=5)


def test_confirmation_refuses_short_independent_window():
    frame = _frame(hours=0.02)
    with pytest.raises(ValueError, match="preregistered 12.000h minimum"):
        run_locked_confirmation(frame, block_shuffle_repeats=5)


def test_confirmation_returns_locked_schema_on_independent_window():
    frame = _frame(hours=0.02)
    out = run_locked_confirmation(
        frame,
        min_duration_hours=0.01,
        block_shuffle_repeats=5,
    )
    assert out["summary"]["feature"] == LOCKED_FEATURE
    assert out["summary"]["horizon_ms"] == 30000
    assert out["summary"]["independent_window"] is True
    assert set(out["symbols"]["symbol"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert set(out["symbols"]["role"]) == {"PRIMARY", "SUPPORT"}
    assert out["summary"]["verdict"] in {"CONFIRMED_INFORMATION_CANDIDATE", "NOT_CONFIRMED"}


def test_phase5_2_cli_bootstraps_repo_root():
    root = Path(__file__).resolve().parents[2]
    p = subprocess.run(
        [sys.executable, str(root / "scripts/confirm_market_physics_phase5_v3.py"), "--help"],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert p.returncode == 0, p.stderr
