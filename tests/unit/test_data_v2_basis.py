"""
tests/unit/test_data_v2_basis.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 9: perp/spot basis. Covers a real regression caught while
building this against real BTCUSDT data: pd.merge_asof(..., on="timestamp")
keeps the LEFT frame's (mark's, jittery) timestamp in its output, so
indexing the result by that column and then exact-joining against the
(clean 5m-grid) perp/spot frame silently dropped every settlement except
the ones with exactly zero jitter (85/183 real BTCUSDT settlements matched
before the fix, 183/183 after -- see data_v2/features/basis.py).

Gate:
    python3 -m pytest tests/unit/test_data_v2_basis.py -q
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.features import basis as basis_mod


@pytest.fixture()
def fake_universe(tmp_path, monkeypatch):
    perp_dir = tmp_path / "perp/venue=binance"
    spot_dir = tmp_path / "spot/venue=binance"
    out_dir = tmp_path / "basis/venue=binance"
    funding_dir = tmp_path / "funding"
    premium_dir = tmp_path / "premium"
    for d in (perp_dir, spot_dir, out_dir, funding_dir, premium_dir):
        d.mkdir(parents=True)

    monkeypatch.setattr(basis_mod, "PERP_DIR", perp_dir)
    monkeypatch.setattr(basis_mod, "SPOT_DIR", spot_dir)
    monkeypatch.setattr(basis_mod, "OUT_DIR", out_dir)
    monkeypatch.setattr(basis_mod, "FUNDING_DIR", funding_dir)
    monkeypatch.setattr(basis_mod, "PREMIUM_DIR", premium_dir)
    return {"perp": perp_dir, "spot": spot_dir, "out": out_dir, "funding": funding_dir, "premium": premium_dir}


def _write_5m(root: Path, symbol: str, year: int, idx: pd.DatetimeIndex, col: str, values) -> None:
    d = root / f"symbol={symbol}" / f"year={year}"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"timestamp": idx, col: values}).to_parquet(d / "f.parquet", index=False)


def test_perp_spot_basis_matches_manual_ratio(fake_universe):
    idx = pd.date_range("2024-03-01", periods=300, freq="5min", tz="UTC")
    rng = np.random.default_rng(1)
    spot_close = 100 + np.cumsum(rng.normal(0, 0.1, len(idx)))
    perp_close = spot_close * (1 + rng.normal(0, 0.0005, len(idx)))
    _write_5m(fake_universe["perp"], "FOOUSDT", 2024, idx, "close", perp_close)
    _write_5m(fake_universe["spot"], "FOOUSDT", 2024, idx, "spot_close", spot_close)

    df = basis_mod.build_basis_symbol("FOOUSDT")
    expected = perp_close / spot_close - 1.0
    np.testing.assert_allclose(df["perp_spot_basis"].to_numpy(), expected, rtol=1e-10)


def test_basis_only_where_both_legs_present_no_forward_fill(fake_universe):
    idx_perp = pd.date_range("2024-03-01", periods=10, freq="5min", tz="UTC")
    idx_spot = idx_perp[3:]  # spot starts later -- no bars for the first 3
    _write_5m(fake_universe["perp"], "BARUSDT", 2024, idx_perp, "close", np.full(10, 100.0))
    _write_5m(fake_universe["spot"], "BARUSDT", 2024, idx_spot, "spot_close", np.full(7, 100.0))

    df = basis_mod.build_basis_symbol("BARUSDT")
    assert len(df) == 7  # inner join, not 10 with NaN/ffill for the missing spot bars
    assert df["timestamp"].min() == idx_spot[0]


def test_mark_spot_basis_matches_despite_millisecond_jitter(fake_universe):
    idx = pd.date_range("2024-03-01", periods=300, freq="5min", tz="UTC")
    close = np.full(len(idx), 100.0)
    _write_5m(fake_universe["perp"], "BAZUSDT", 2024, idx, "close", close)
    _write_5m(fake_universe["spot"], "BAZUSDT", 2024, idx, "spot_close", close)

    settlement_ts = [
        idx[96],                                    # zero jitter
        idx[192] + pd.Timedelta(milliseconds=3),    # a few ms of jitter, like real Binance data
        idx[288] + pd.Timedelta(milliseconds=1),
    ]
    funding_df = pd.DataFrame({"timestamp": settlement_ts, "funding_rate": [0.0001, 0.0002, 0.0003],
                                "mark_price": [101.0, 99.0, 102.0]})
    funding_df.to_parquet(fake_universe["funding"] / "BAZUSDT.parquet", index=False)

    df = basis_mod.build_basis_symbol("BAZUSDT")
    matched = df["mark_spot_basis"].notna().sum()
    assert matched == 3, f"expected all 3 jittery settlements to match despite ms jitter, got {matched}"
    assert df.loc[df["timestamp"] == idx[96], "mark_spot_basis"].iloc[0] == pytest.approx(0.01)


def test_mark_spot_basis_join_is_causal_never_matches_a_future_bar(fake_universe):
    """A settlement a couple seconds BEFORE a 5m boundary must bucket to
    the PRIOR (already-started) bar, never the next one. An earlier version
    used merge_asof(direction="nearest", tolerance=5min): "nearest" has no
    causality guarantee -- given a settlement close to the midpoint between
    two bars, it can match the bar that starts AFTER the settlement,
    leaking a not-yet-existing price into mark_spot_basis. floor+exact
    removes that possibility outright (deterministic backward bucketing)."""
    idx = pd.date_range("2024-03-01", periods=10, freq="5min", tz="UTC")
    close = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 999.0])
    _write_5m(fake_universe["perp"], "PREUSDT", 2024, idx, "close", close)
    _write_5m(fake_universe["spot"], "PREUSDT", 2024, idx, "spot_close", close)

    # settlement 2 seconds before idx[9] (price 999.0, the "future" bar
    # relative to this settlement) -- must bucket to idx[8] (price 100.0),
    # not "round to nearest" and pick up the not-yet-started bar's price.
    settlement_ts = idx[9] - pd.Timedelta(seconds=2)
    funding_df = pd.DataFrame({"timestamp": [settlement_ts], "funding_rate": [0.0001], "mark_price": [100.0]})
    funding_df.to_parquet(fake_universe["funding"] / "PREUSDT.parquet", index=False)

    df = basis_mod.build_basis_symbol("PREUSDT")
    matched = df.loc[df["mark_spot_basis"].notna()]
    assert len(matched) == 1
    assert matched["timestamp"].iloc[0] == idx[8]  # the prior bar, not idx[9]
    assert matched["mark_spot_basis"].iloc[0] == pytest.approx(0.0)  # 100/100 - 1, not 100/999 - 1


def test_premium_index_passthrough(fake_universe):
    idx = pd.date_range("2024-03-01", periods=5, freq="5min", tz="UTC")
    close = np.full(5, 100.0)
    _write_5m(fake_universe["perp"], "QUXUSDT", 2024, idx, "close", close)
    _write_5m(fake_universe["spot"], "QUXUSDT", 2024, idx, "spot_close", close)
    pd.DataFrame({"ts": idx, "premium": np.linspace(0.001, 0.002, 5)}).to_parquet(
        fake_universe["premium"] / "QUXUSDT_premium_5m.parquet", index=False
    )

    df = basis_mod.build_basis_symbol("QUXUSDT")
    assert df["premium_index"].notna().all()


def test_basis_z_1d_excludes_current_bar_from_its_own_threshold(fake_universe):
    """Round-4 fix: basis_z_1d's mean/std must come from basis.shift(1) --
    the strictly PRIOR 288 bars -- never including the bar's own value. A
    huge one-bar spike must not be able to inflate the very mean/std it is
    then measured against (which would silently damp its own z-score)."""
    idx = pd.date_range("2024-03-01", periods=400, freq="5min", tz="UTC")
    rng = np.random.default_rng(5)
    spot_close = np.full(len(idx), 100.0)
    perp_close = spot_close * (1 + rng.normal(0, 0.0001, len(idx)))
    spike_pos = 350
    perp_close[spike_pos] = spot_close[spike_pos] * 1.05  # huge, isolated 5% spike
    _write_5m(fake_universe["perp"], "SPKUSDT", 2024, idx, "close", perp_close)
    _write_5m(fake_universe["spot"], "SPKUSDT", 2024, idx, "spot_close", spot_close)

    df = basis_mod.build_basis_symbol("SPKUSDT")
    basis = df["perp_spot_basis"]

    # manually recompute the STRICTLY PRIOR window's mean/std for the spike
    # bar and confirm basis_z_1d matches it exactly -- if the spike were
    # (bug) included in its own window, the manual prior-only figure would
    # disagree with whatever the code produced.
    prior = basis.iloc[spike_pos - 288 : spike_pos]  # the 288 bars strictly before the spike
    expected_z = (basis.iloc[spike_pos] - prior.mean()) / prior.std()
    assert df["basis_z_1d"].iloc[spike_pos] == pytest.approx(expected_z, rel=1e-9)

    # the bar strictly AFTER the spike, by contrast, must have the spike
    # inside ITS prior window (this is expected, not a bug -- confirms the
    # window is genuinely history-based, not just "always excludes bar
    # spike_pos").
    prior_next = basis.iloc[spike_pos - 287 : spike_pos + 1]
    expected_z_next = (basis.iloc[spike_pos + 1] - prior_next.mean()) / prior_next.std()
    assert df["basis_z_1d"].iloc[spike_pos + 1] == pytest.approx(expected_z_next, rel=1e-9)


def test_basis_z_1d_requires_complete_288_bar_warmup(fake_universe):
    """Round-4 fix: min_periods is the FULL 288-bar window, not 288//3 --
    basis_z_1d must be NaN until a complete day of history exists, not
    fire off a third of a day's worth of (unreliable) mean/std."""
    idx = pd.date_range("2024-03-01", periods=300, freq="5min", tz="UTC")
    rng = np.random.default_rng(6)
    spot_close = 100 + np.cumsum(rng.normal(0, 0.05, len(idx)))
    perp_close = spot_close * (1 + rng.normal(0, 0.0005, len(idx)))
    _write_5m(fake_universe["perp"], "WARMUSDT", 2024, idx, "close", perp_close)
    _write_5m(fake_universe["spot"], "WARMUSDT", 2024, idx, "spot_close", spot_close)

    df = basis_mod.build_basis_symbol("WARMUSDT")
    # bars 0..287 (index positions 0-287, i.e. the first 288 rows) can never
    # have a complete strictly-prior 288-bar window -- all must be NaN.
    assert df["basis_z_1d"].iloc[:288].isna().all()
    # bar 288 (the 289th row) is the first with a full prior window.
    assert df["basis_z_1d"].iloc[288:].notna().all()


def test_missing_spot_returns_none(fake_universe):
    idx = pd.date_range("2024-03-01", periods=5, freq="5min", tz="UTC")
    _write_5m(fake_universe["perp"], "NOSPOTUSDT", 2024, idx, "close", np.full(5, 100.0))
    assert basis_mod.build_basis_symbol("NOSPOTUSDT") is None
