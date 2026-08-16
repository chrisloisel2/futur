import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_foundry_v5.contracts import ExperimentSpec, ResearchStage, TimeWindow
from alpha_foundry_v5.hypotheses import hypothesis_grid
from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.ledger import SearchLedger
from alpha_foundry_v5.modeling import RidgeAdapter, nested_purged_walk_forward
from alpha_foundry_v5.research_engine import ResearchEngine


def _frame(n=4000):
    rng = np.random.RandomState(5)
    t = np.arange(n)
    base = 100 + np.cumsum(rng.normal(0, 0.001, n))
    shock = rng.normal(0, 1, n)
    frame = pd.DataFrame({
        "asof_ns": (1_000_000_000_000 + t * 100_000_000).astype(np.int64),
        "price_fair_value": base,
        "binance__price_mid": base + 0.001 * shock,
        "bybit__price_mid": base - 0.001 * shock,
        "okx__price_mid": base + 0.0005 * shock,
        "hyperliquid__price_mid": base - 0.0002 * shock,
        "binance__price_dislocation_bps": shock,
        "bybit__price_dislocation_bps": -shock,
        "okx__price_dislocation_bps": 0.5 * shock,
        "hyperliquid__price_dislocation_bps": -0.2 * shock,
    })
    for v in ("binance", "bybit", "okx", "hyperliquid"):
        frame[v + "__price_weight"] = 1.0
    return frame


def test_all_16_labs_have_unique_sources():
    registry = LabRegistry()
    assert len(registry.specs) == 16
    assert len({s.economic_source_id for s in registry.specs.values()}) == 16


def test_lab_readiness_is_fail_closed():
    registry = LabRegistry()
    frame = _frame()
    assert registry.readiness("A1", frame)["ready"] is True
    assert registry.readiness("A14", frame)["ready"] is False


def test_nested_cv_produces_outer_oos_predictions():
    rng = np.random.RandomState(7)
    n = 3000
    x = rng.normal(size=(n, 3))
    y = 0.7 * x[:, 0] + rng.normal(scale=0.5, size=n)
    ts = 1_000_000_000 + np.arange(n, dtype=np.int64) * 1_000_000
    result = nested_purged_walk_forward(x, y, ts, RidgeAdapter(), [{"alpha": 0.1}, {"alpha": 1.0}, {"alpha": 10.0}], outer_splits=4, inner_splits=3, purge_ms=1, embargo_ms=1)
    assert len(result.folds) == 4
    assert result.outer_ic > 0.5
    assert np.isnan(result.predictions[:500]).any()


def test_research_engine_charges_search_budget_and_fdr(tmp_path):
    frame = _frame(5000)
    h = hypothesis_grid("A1", "venue=okx", horizons_ms=[100], confirmation_min_hours=12)[0]
    window = TimeWindow(int(frame.asof_ns.iloc[0]), int(frame.asof_ns.iloc[-1]) + 1)
    e = ExperimentSpec("exp1", h.digest, ResearchStage.DEV_DISCOVERY, "dataset", window, "commit", 42, 100, 100, search_family_id=h.family_id)
    ledger = SearchLedger(str(tmp_path / "ledger.jsonl"))
    result = ResearchEngine(ledger).run_discovery(frame, h, e, cadence_ms=100, configs=[{"alpha": 0.1}, {"alpha": 1.0}], outer_splits=3, inner_splits=2, block_size_rows=100)
    finalized = ResearchEngine(ledger).finalize_family([result])[0]
    assert ledger.effective_trials(h.family_id) == 2
    assert np.isfinite(finalized.ic)
    assert 0 <= finalized.q_value <= 1


def test_temporal_features_do_not_cross_symbol_boundary():
    registry = LabRegistry()
    a = _frame(20)
    a["symbol"] = "BTCUSDT"
    b = _frame(20)
    b["symbol"] = "ETHUSDT"
    b["asof_ns"] = a["asof_ns"]
    b["binance__price_mid"] = b["binance__price_mid"] + 1000.0
    frame = pd.concat([a, b], ignore_index=True).sort_values(["asof_ns", "symbol"]).reset_index(drop=True)
    features = registry.materialize_features("A1", frame)
    eth_first = frame.index[frame["symbol"] == "ETHUSDT"][0]
    assert np.isnan(features.loc[eth_first, "binance__price_mid__ret_1"])


def test_v5_clis_bootstrap_repo_root():
    root = Path(__file__).resolve().parents[2]
    for script in ["alpha_foundry_v5_readiness.py", "alpha_foundry_v5_freeze_dataset.py", "alpha_foundry_v5_discover.py"]:
        p = subprocess.run([sys.executable, str(root / "scripts" / script), "--help"], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert p.returncode == 0, p.stderr
