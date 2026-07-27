"""
tests/test_alpha20_tournament_orchestrator.py
─────────────────────────────────────────────────────────────────────────────
Isolation totale entre runners (un qui plante/timeout n'affecte jamais les
autres), indépendance de Mongo (le tournoi n'en dépend structurellement pas —
preuve positive : MongoClient bloqué, le cycle aboutit quand même), config
gelée (config_hash stable tant que le bloc `config` ne change pas), garde
appelée au démarrage du cycle. Aucun réseau réel (bus/broker/adapters
monkeypatchés), aucun ledger réel.
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.alpha20.tournament import market_bus, orchestrator
from src.alpha20.tournament.runner_registry import RunnerSpec


def _spec(rid, config=None):
    return RunnerSpec(runner_id=rid, family="test", status="ACTIVE",
                      git_commit="abc123", config_hash="h1",
                      sizing={"capital_standalone_eur": 1000.0}, venue="binance_usdm",
                      config=config or {"k": 1})


class _OkAdapter:
    def __init__(self, spec):
        self.spec = spec
    def required_universe(self, state): return []
    def required_funding(self, state): return []
    def required_quarterly_pairs(self, state): return []
    def decide(self, snapshot, broker, state, risk_state="risk_on"):
        return [], dict(state, ticked=state.get("ticked", 0) + 1)


class _BoomAdapter:
    def __init__(self, spec):
        self.spec = spec
    def required_universe(self, state): return []
    def required_funding(self, state): return []
    def required_quarterly_pairs(self, state): return []
    def decide(self, snapshot, broker, state, risk_state="risk_on"):
        raise RuntimeError("runner cassé exprès")


class _SlowAdapter:
    def __init__(self, spec):
        self.spec = spec
    def required_universe(self, state): return []
    def required_funding(self, state): return []
    def required_quarterly_pairs(self, state): return []
    def decide(self, snapshot, broker, state, risk_state="risk_on"):
        time.sleep(5)
        return [], state


@pytest.fixture
def no_network(monkeypatch, tmp_path):
    monkeypatch.setattr(market_bus, "BUS_DIR", tmp_path / "bus")
    import src.institutional.live.paper_portfolio as pp
    monkeypatch.setattr(pp, "live_prices", lambda syms: {})
    monkeypatch.setattr(pp, "live_funding", lambda s: None)
    monkeypatch.setattr(pp, "btc_regime", lambda: "UNKNOWN")
    monkeypatch.setattr(orchestrator, "assert_paper_only", lambda: None)
    # Fail-closed behavior of the deployment guard itself is covered by
    # tests/test_alpha20_deployment_guard.py (no real manifest -> refuses to
    # start). These tests are about orchestrator isolation/timeout/Mongo
    # independence, not the guard, so bypass it rather than fabricating a
    # global approved-manifest file.
    monkeypatch.setattr(orchestrator, "assert_deployment_matches_approved", lambda: None)


def test_one_broken_runner_never_blocks_the_others(no_network, monkeypatch):
    specs = [_spec("good1"), _spec("broken"), _spec("good2")]
    monkeypatch.setattr(orchestrator, "runnable_specs", lambda: specs)
    def builder(spec):
        return _BoomAdapter(spec) if spec.runner_id == "broken" else _OkAdapter(spec)
    monkeypatch.setattr(orchestrator, "build_adapter", builder)
    results = orchestrator.run_cycle()
    assert results["good1"]["status"] == "ok"
    assert results["good2"]["status"] == "ok"
    assert results["broken"]["status"] == "error"
    assert "runner cassé exprès" in results["broken"]["error"]


def test_slow_runner_times_out_without_blocking_cycle(no_network, monkeypatch):
    monkeypatch.setattr(orchestrator, "RUNNER_TIMEOUT_S", 1)
    specs = [_spec("fast"), _spec("slow")]
    monkeypatch.setattr(orchestrator, "runnable_specs", lambda: specs)
    def builder(spec):
        return _SlowAdapter(spec) if spec.runner_id == "slow" else _OkAdapter(spec)
    monkeypatch.setattr(orchestrator, "build_adapter", builder)
    t0 = time.time()
    results = orchestrator.run_cycle()
    elapsed = time.time() - t0
    assert results["fast"]["status"] == "ok"
    assert results["slow"]["status"] == "timeout"
    assert elapsed < 4.5                              # n'attend pas les 5s du runner lent


def test_mongo_unavailable_does_not_break_the_cycle(no_network, monkeypatch):
    """Le tournoi n'écrit délibérément PAS dans Mongo (voir reconciliation.py)
    — preuve positive : MongoClient bloqué, un cycle complet aboutit quand
    même."""
    pymongo = pytest.importorskip("pymongo", reason="pymongo absent de cet environnement")
    def _boom(*a, **kw):
        raise AssertionError("le tournoi ne doit JAMAIS instancier MongoClient")
    monkeypatch.setattr(pymongo, "MongoClient", _boom)
    specs = [_spec("solo")]
    monkeypatch.setattr(orchestrator, "runnable_specs", lambda: specs)
    monkeypatch.setattr(orchestrator, "build_adapter", lambda s: _OkAdapter(s))
    results = orchestrator.run_cycle()
    assert results["solo"]["status"] == "ok"


def test_guard_called_before_anything_else(monkeypatch, tmp_path):
    monkeypatch.setattr(market_bus, "BUS_DIR", tmp_path / "bus")
    called = {"guard": False}
    def fake_guard():
        called["guard"] = True
        raise SystemExit(2)
    monkeypatch.setattr(orchestrator, "assert_paper_only", fake_guard)
    with pytest.raises(SystemExit):
        orchestrator.run_cycle()
    assert called["guard"]


def test_config_hash_stable_across_reloads(tmp_path, monkeypatch):
    import yaml
    from src.alpha20.tournament import runner_registry as rr
    reg = {"git_commit": "abc", "runners": [
        {"runner_id": "x", "family": "f", "status": "ACTIVE",
         "config": {"a": 1, "b": [1, 2, 3]}}]}
    p = tmp_path / "reg.yaml"
    p.write_text(yaml.safe_dump(reg))
    monkeypatch.setattr(rr, "REGISTRY_PATH", p)
    h1 = rr.load_registry()["x"].config_hash
    h2 = rr.load_registry()["x"].config_hash
    assert h1 == h2
    reg["runners"][0]["config"]["a"] = 2              # config modifiée
    p.write_text(yaml.safe_dump(reg))
    h3 = rr.load_registry()["x"].config_hash
    assert h3 != h1
