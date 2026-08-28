import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from alpha_foundry_v5.contracts import ExperimentSpec, ResearchStage, TimeWindow
from alpha_foundry_v5.hypotheses import hypothesis_grid
from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.ledger import SearchLedger
from alpha_foundry_v5.modeling import RidgeAdapter, nested_purged_walk_forward
from alpha_foundry_v5.research_engine import ResearchEngine, build_evidence


def _frame(n=4000, start_ns=1_000_000_000_000, symbol=None, seed=5):
    rng = np.random.RandomState(seed)
    t = np.arange(n)
    base = 100 + np.cumsum(rng.normal(0, 0.001, n))
    shock = rng.normal(0, 1, n)
    frame = pd.DataFrame({
        "asof_ns": (start_ns + t * 100_000_000).astype(np.int64),
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
    if symbol is not None:
        frame["symbol"] = symbol
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


def test_nested_cv_predictions_by_config_has_one_column_per_trial_and_matches_selected():
    rng = np.random.RandomState(7)
    n = 3000
    x = rng.normal(size=(n, 3))
    y = 0.7 * x[:, 0] + rng.normal(scale=0.5, size=n)
    ts = 1_000_000_000 + np.arange(n, dtype=np.int64) * 1_000_000
    configs = [{"alpha": 0.1}, {"alpha": 1.0}, {"alpha": 10.0}]
    result = nested_purged_walk_forward(x, y, ts, RidgeAdapter(), configs, outer_splits=4, inner_splits=3, purge_ms=1, embargo_ms=1)
    assert result.predictions_by_config.shape == (n, len(configs))
    assert np.isfinite(result.predictions_by_config).any(axis=0).all()  # every config got evaluated somewhere
    # Each fold's stitched "selected" prediction at a row must equal ONE of that row's
    # per-config columns -- namely whichever config that row's fold actually selected.
    # NestedFoldResult doesn't expose test_idx, so check membership rather than identity.
    finite_rows = np.isfinite(result.predictions)
    matches_some_column = np.any(np.isclose(result.predictions_by_config[finite_rows], result.predictions[finite_rows, None]), axis=1)
    assert matches_some_column.all()


def test_research_engine_run_discovery_produces_symbol_ics_and_trial_returns(tmp_path):
    a = _frame(2500, symbol=None, seed=1)
    a["symbol"] = "BTCUSDT"
    b = _frame(2500, symbol=None, seed=2)
    b["symbol"] = "ETHUSDT"
    frame = pd.concat([a, b], ignore_index=True)
    h = hypothesis_grid("A1", "venue=okx", horizons_ms=[100], confirmation_min_hours=12)[0]
    window = TimeWindow(int(frame.asof_ns.min()), int(frame.asof_ns.max()) + 1)
    e = ExperimentSpec("exp-sym", h.digest, ResearchStage.DEV_DISCOVERY, "dataset", window, "commit", 42, 100, 100, search_family_id=h.family_id)
    ledger = SearchLedger(str(tmp_path / "ledger.jsonl"))
    result = ResearchEngine(ledger).run_discovery(frame, h, e, cadence_ms=100, configs=[{"alpha": 0.1}, {"alpha": 1.0}], outer_splits=3, inner_splits=2, block_size_rows=100)
    assert set(result.symbol_ics) == {"BTCUSDT", "ETHUSDT"}
    assert all(np.isfinite(v) for v in result.symbol_ics.values())
    assert result.trial_returns.shape == (result.n, 2)


def test_research_engine_run_confirmation_requires_confirmation_stage(tmp_path):
    frame = _frame(2500)
    h = hypothesis_grid("A1", "venue=okx", horizons_ms=[100], confirmation_min_hours=12)[0]
    window = TimeWindow(int(frame.asof_ns.min()), int(frame.asof_ns.max()) + 1)
    e = ExperimentSpec("exp-wrong-stage", h.digest, ResearchStage.DEV_DISCOVERY, "dataset", window, "commit", 42, 100, 100, search_family_id=h.family_id)
    ledger = SearchLedger(str(tmp_path / "ledger.jsonl"))
    with pytest.raises(ValueError, match="INDEPENDENT_CONFIRMATION"):
        ResearchEngine(ledger).run_confirmation(frame, h, e, cadence_ms=100, configs=[{"alpha": 0.1}], outer_splits=3, inner_splits=2, block_size_rows=100)


def test_research_engine_run_confirmation_on_a_later_window(tmp_path):
    discovery_frame = _frame(2500, start_ns=1_000_000_000_000, seed=1)
    discovery_window = TimeWindow(int(discovery_frame.asof_ns.min()), int(discovery_frame.asof_ns.max()) + 1)

    confirm_start = discovery_window.stop_ns + 1_000_000_000
    confirm_frame = _frame(2500, start_ns=confirm_start, seed=9)
    h = hypothesis_grid("A1", "venue=okx", horizons_ms=[100], confirmation_min_hours=12)[0]
    confirm_window = TimeWindow(int(confirm_frame.asof_ns.min()), int(confirm_frame.asof_ns.max()) + 1)
    assert confirm_window.start_ns > discovery_window.stop_ns

    e = ExperimentSpec("exp-confirm", h.digest, ResearchStage.INDEPENDENT_CONFIRMATION, "dataset", confirm_window, "commit", 42, 100, 100, search_family_id=h.family_id)
    ledger = SearchLedger(str(tmp_path / "ledger.jsonl"))
    result = ResearchEngine(ledger).run_confirmation(confirm_frame, h, e, cadence_ms=100, configs=[{"alpha": 0.1}, {"alpha": 1.0}], outer_splits=3, inner_splits=2, block_size_rows=100)
    assert np.isfinite(result.ic)
    assert result.trial_returns.shape == (result.n, 2)

    evidence = build_evidence(
        result,
        stage=ResearchStage.INDEPENDENT_CONFIRMATION,
        pvalue_family=[result.block_p],
        own_pvalue_index=0,
        primary_symbols=("BTCUSDT",),
        discovery_window=discovery_window,
        evaluation_window=confirm_window,
        block_size_rows=100,
    )
    assert evidence.independent_window is True


def test_build_evidence_discovery_window_equals_evaluation_window_is_not_independent(tmp_path):
    frame = _frame(2500)
    h = hypothesis_grid("A1", "venue=okx", horizons_ms=[100], confirmation_min_hours=12)[0]
    window = TimeWindow(int(frame.asof_ns.min()), int(frame.asof_ns.max()) + 1)
    e = ExperimentSpec("exp-disc", h.digest, ResearchStage.DEV_DISCOVERY, "dataset", window, "commit", 42, 100, 100, search_family_id=h.family_id)
    ledger = SearchLedger(str(tmp_path / "ledger.jsonl"))
    result = ResearchEngine(ledger).run_discovery(frame, h, e, cadence_ms=100, configs=[{"alpha": 0.1}, {"alpha": 1.0}], outer_splits=3, inner_splits=2, block_size_rows=100)
    evidence = build_evidence(
        result,
        stage=ResearchStage.DEV_DISCOVERY,
        pvalue_family=[result.block_p],
        own_pvalue_index=0,
        primary_symbols=("BTCUSDT",),
        discovery_window=window,
        evaluation_window=window,
        block_size_rows=100,
    )
    assert evidence.independent_window is False


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
    for script in ["alpha_foundry_v5_readiness.py", "alpha_foundry_v5_freeze_dataset.py", "alpha_foundry_v5_discover.py", "build_alpha_foundry_v5_data_planes.py"]:
        p = subprocess.run([sys.executable, str(root / "scripts" / script), "--help"], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert p.returncode == 0, p.stderr
