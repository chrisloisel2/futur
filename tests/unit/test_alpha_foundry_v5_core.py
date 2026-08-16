import numpy as np
import pandas as pd
import pytest

from alpha_foundry_v5.artifacts import ArtifactStore
from alpha_foundry_v5.contracts import ExperimentSpec, ResearchStage, TimeWindow
from alpha_foundry_v5.execution import FeeSchedule, LatencyModel, MarketSnapshot, OrderIntent, passive_execution, taker_execution
from alpha_foundry_v5.ledger import SearchBudgetExceeded, SearchLedger
from alpha_foundry_v5.lineage import ExperimentRegistry
from alpha_foundry_v5.manifest import DatasetManifest, fingerprint_partitions, verify_manifest, write_manifest
from alpha_foundry_v5.portfolio import Sleeve, admit_sleeves
from alpha_foundry_v5.quality import audit_point_in_time, require_pit_clean
from alpha_foundry_v5.splits import PurgedWalkForwardSplitter
from alpha_foundry_v5.statistics import bh_qvalues, cscv_pbo, deflated_sharpe_probability


def test_manifest_is_immutable_and_detects_mutation(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("alpha")
    manifest = DatasetManifest("v1", "x", TimeWindow(1, 10), ("book",), ("test",), fingerprint_partitions([str(f)]), 1, "abc", "receive<=asof", "event+receive")
    out = tmp_path / "manifest.json"
    write_manifest(manifest, str(out))
    assert verify_manifest(manifest)["ok"] is True
    with pytest.raises(FileExistsError):
        write_manifest(manifest, str(out))
    f.write_text("beta")
    assert verify_manifest(manifest)["ok"] is False


def test_search_budget_is_hard(tmp_path):
    ledger = SearchLedger(str(tmp_path / "ledger.jsonl"))
    ledger.reserve("t1", "fam", "h", "e", {"a": 1}, "DEV", 2)
    ledger.reserve("t2", "fam", "h", "e", {"a": 2}, "DEV", 2)
    with pytest.raises(SearchBudgetExceeded):
        ledger.reserve("t3", "fam", "h", "e", {"a": 3}, "DEV", 2)
    assert ledger.effective_trials("fam") == 2


def test_ledger_hash_chain_and_unreserved_completion_fail(tmp_path):
    ledger = SearchLedger(str(tmp_path / "chain.jsonl"))
    ledger.reserve("t1", "fam", "h", "e", {"a": 1}, "DEV", 3)
    ledger.complete("t1", "fam", "h", "e", {"a": 1}, "DEV", 0.2)
    assert ledger.verify()["ok"] is True
    with pytest.raises(ValueError):
        ledger.complete("never", "fam", "h", "e", {"a": 2}, "DEV", 0.1)
    path = tmp_path / "chain.jsonl"
    rows = path.read_text().splitlines()
    rows[0] = rows[0].replace('"status":"RESERVED"', '"status":"TAMPERED"')
    path.write_text("\n".join(rows) + "\n")
    assert ledger.verify()["ok"] is False


def test_confirmation_lineage_refuses_overlap(tmp_path):
    registry = ExperimentRegistry(str(tmp_path / "experiments"))
    registry.register(ExperimentSpec("dev", "hyp", ResearchStage.DEV_DISCOVERY, "d", TimeWindow(100, 200), "abc", 1, 10, 10))
    with pytest.raises(ValueError):
        registry.register(ExperimentSpec("bad", "hyp", ResearchStage.INDEPENDENT_CONFIRMATION, "d2", TimeWindow(150, 250), "abc", 2, 10, 10))
    registry.register(ExperimentSpec("good", "hyp", ResearchStage.INDEPENDENT_CONFIRMATION, "d3", TimeWindow(201, 300), "abc", 2, 10, 10))


def test_purged_walk_forward_has_gap():
    ts = 1_000_000_000 + np.arange(1000, dtype=np.int64) * 1_000_000
    folds = list(PurgedWalkForwardSplitter(4, purge_ms=10).split(ts))
    assert len(folds) == 4
    for fold in folds:
        assert ts[fold.train_idx].max() < ts[fold.test_idx].min() - 10_000_000 + 1


def test_multisymbol_duplicate_timestamps_never_cross_folds():
    unique = 1_000_000_000 + np.arange(500, dtype=np.int64) * 1_000_000
    ts = np.repeat(unique, 3)
    folds = list(PurgedWalkForwardSplitter(4, purge_ms=5, embargo_ms=2).split(ts))
    assert len(folds) == 4
    seen_test = set()
    for fold in folds:
        train_times = set(ts[fold.train_idx].tolist())
        test_times = set(ts[fold.test_idx].tolist())
        assert not train_times.intersection(test_times)
        assert not seen_test.intersection(test_times)
        seen_test.update(test_times)
        assert max(train_times) < min(test_times) - 7_000_000 + 1


def test_statistics_multiple_testing_pbo_and_dsr():
    q = bh_qvalues([0.001, 0.02, 0.5])
    assert q[0] <= q[1] <= q[2]
    rng = np.random.RandomState(2)
    assert 0 <= cscv_pbo(rng.normal(size=(500, 6)), n_blocks=10) <= 1
    strong = rng.normal(0.003, 0.01, size=1000)
    assert deflated_sharpe_probability(strong, [0.0, 0.01, 0.02, 0.03]) > 0.5


def test_execution_is_cost_aware_and_passive_conservative():
    snap = MarketSnapshot(1, 100.0, 99.99, 100.01, 1_000_000, 1_000_000, 5.0, 50_000_000)
    fees = FeeSchedule(-0.1, 2.0)
    taker = taker_execution(OrderIntent("buy", 10000, 5.0, "taker"), snap, 100.02, fees, LatencyModel(5, 10, 5))
    assert taker.net_edge_bps < 5.0
    maker = passive_execution(OrderIntent("buy", 10000, 1.0, "maker", 99.99), snap, 50000, 10000, 40000, 99.98, fees)
    assert maker.filled is False
    assert 0 < maker.fill_probability < 1
    assert maker.model_confidence == "L2_CONSERVATIVE"


def test_portfolio_rejects_same_economic_source():
    rng = np.random.RandomState(1)
    pnl = pd.DataFrame({"a": rng.normal(size=500), "b": rng.normal(size=500), "c": rng.normal(size=500)})
    result = admit_sleeves([Sleeve("s1", "source1", "a", 3.0), Sleeve("clone", "source1", "b", 2.0), Sleeve("s2", "source2", "c", 1.0)], pnl, min_unique_sources=2, min_effective_bets=1.5)
    assert "clone" in result.rejected
    assert result.unique_economic_sources == 2
    assert result.effective_independent_bets > 1.5


def test_pit_auditor_fails_on_future_availability():
    frame = pd.DataFrame({"asof_ns": [100, 200, 300], "symbol": ["BTC", "BTC", "BTC"], "book_available_ts_ns": [90, 250, 290]})
    result = audit_point_in_time(frame)
    assert result.future_availability_violations == 1
    with pytest.raises(ValueError):
        require_pit_clean(result)


def test_artifact_store_seal_detects_mutation(tmp_path):
    store = ArtifactStore(str(tmp_path / "artifacts"))
    store.write_json("e1", "metrics.json", {"ic": 0.1})
    store.seal("e1", {"code_commit": "abc"})
    assert store.verify("e1")["ok"] is True
    with pytest.raises(RuntimeError):
        store.write_json("e1", "later.json", {"x": 1})
    (tmp_path / "artifacts" / "e1" / "metrics.json").write_text("{}")
    assert store.verify("e1")["ok"] is False
