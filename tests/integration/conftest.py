"""tests/integration/conftest.py -- shared synthetic-data fixtures.

Phase 3 step 9: some integration tests need `load_enriched()` (real
signature: src/institutional/engines/legacy_bridge.py) to return real-shaped
OHLCV+funding data, but this machine has no local
data/enriched/{ASSET}_1h_enriched.parquet (it lives on the deployment host,
per prior-session memory -- see docs/v2/EXECUTION_STATE.md). A clean clone
never has it either, so a canonical test cannot depend on it.

`synthetic_enriched_loader` builds a deterministic (seeded), minimal-but-
real-shaped replacement: only the columns the calling code actually reads
(`datetime`, `close`, `funding_rate` -- see
MultiLegBacktester._load()/backtest/multileg_backtester.py:130-140) are
populated with real values; nothing here fabricates a trading result, it
only unblocks the code path that needs *some* valid price series to run at
all. Tests using it assert structural/invariant properties (no naked short,
hedge closes without long, carry P&L bucket exists), never a specific P&L
number -- synthetic data cannot honestly support the latter.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_synthetic_enriched(asset: str, start: str, end: str,
                             seed: int = 20260728) -> pd.DataFrame:
    idx = pd.date_range(start, end, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed + (hash(asset) % 1000))
    # small, bounded random walk -- realistic magnitude, not a fitted series
    log_returns = rng.normal(loc=0.0, scale=0.0015, size=len(idx))
    close = 50_000.0 * np.exp(np.cumsum(log_returns))
    return pd.DataFrame({
        "datetime": idx,
        "close": close,
        "open": close,
        "high": close,
        "low": close,
        "volume": 1.0,
        "funding_rate": 0.0001,   # typical positive perp funding, constant
    })


@pytest.fixture
def synthetic_load_enriched(monkeypatch):
    """Patches `load_enriched` as imported into multileg_backtester.py (the
    binding that module sees, not the original legacy_bridge module -- name
    bindings are per-importer). Returns the patch function so a test can
    call it explicitly if it needs a specific asset/date range verified."""
    import src.institutional.backtest.multileg_backtester as mlb_mod

    def _fake_load_enriched(asset, required_cols=None, start=None, end=None):
        df = _make_synthetic_enriched(asset, start, end)
        if required_cols is not None:
            keep = {"datetime"} | set(required_cols)
            df = df[[c for c in df.columns if c in keep]]
        return df

    monkeypatch.setattr(mlb_mod, "load_enriched", _fake_load_enriched)
    return _fake_load_enriched
