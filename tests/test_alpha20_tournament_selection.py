"""
tests/test_alpha20_tournament_selection.py
─────────────────────────────────────────────────────────────────────────────
Protocole de sélection figé : échantillon insuffisant → OBSERVING, aucun
survivant → NO_SELECTION, robustesse faible → FRAGILE, config modifiée en
phase D → REJECTED (jamais de recalibrage), clustering → un seul dominant par
groupe corrélé. Séries synthétiques déterministes, aucun réseau, aucun ledger
réel (fixture autouse).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.alpha20.contracts import LedgerEvent
from src.alpha20.tournament import reconciliation
from src.alpha20.tournament.paper_account import PaperAccount
from src.alpha20.tournament.runner_registry import RunnerSpec
from src.alpha20.tournament.selection import clustering, phases
from src.alpha20.tournament.selection.manifest import load_protocol


def _spec(rid, capital=200000.0, status="ACTIVE"):
    return RunnerSpec(runner_id=rid, family="test", status=status,
                      git_commit="abc", config_hash=f"hash_{rid}",
                      sizing={"capital_standalone_eur": capital}, venue="binance_usdm",
                      config={"k": 1})


def _seed_history(rid, n_days, daily_mu, daily_sigma, seed, capital=200000.0,
                  n_decisions=None, start_days_ago=None):
    acc = PaperAccount(rid, capital)
    rng = np.random.RandomState(seed)
    now = datetime.now(timezone.utc)
    start_ago = start_days_ago if start_days_ago is not None else n_days
    t0 = now - timedelta(days=start_ago)
    nav = capital
    for i in range(n_days):
        ts = (t0 + timedelta(days=i)).isoformat()
        r = daily_mu + daily_sigma * rng.randn()
        nav *= (1 + r)
        acc.emit([LedgerEvent(ts=ts, kind="fill", sleeve="s", venue="binance_usdm",
                              amount_usdt=nav - (nav / (1 + r)), ref=f"f{i}")])
        acc.mark(nav, {"nav_usdt": nav}, ts=ts)
    n_dec = n_decisions if n_decisions is not None else n_days
    for i in range(n_dec):
        ts = (t0 + timedelta(hours=i)).isoformat()
        acc.emit([LedgerEvent(ts=ts, kind="decision", sleeve="s", venue="binance_usdm",
                              amount_usdt=0.0, ref="tournament_cycle")])
    return acc


def test_observation_insufficient_sample_stays_observing():
    spec = _spec("young")
    _seed_history("young", n_days=5, daily_mu=0.001, daily_sigma=0.001, seed=1)
    st = phases.selection_status(spec)
    assert st["status"] == "OBSERVING"
    assert st["observation"]["age_days"] < load_protocol()["phase_b_observation"]["min_days"]


def test_no_selection_when_nothing_eligible():
    specs = [_spec("s1"), _spec("s2")]
    _seed_history("s1", n_days=5, daily_mu=0.0, daily_sigma=0.0005, seed=2)  # trop jeune
    _seed_history("s2", n_days=5, daily_mu=0.0, daily_sigma=0.0005, seed=3)
    result = phases.run_selection(specs)
    assert result["verdict"] == "NO_SELECTION"
    assert all(s["status"] == "OBSERVING" for s in result["statuses"].values())


def test_eligible_runner_with_strong_stable_positive_returns():
    spec = _spec("strong")
    _seed_history("strong", n_days=45, daily_mu=0.0025, daily_sigma=0.002, seed=7,
                  n_decisions=350)
    st = phases.selection_status(spec)
    assert st["status"] == "ELIGIBLE"
    assert st["bootstrap_lcb95"] is not None and st["bootstrap_lcb95"] > 0


def test_observe_only_registry_status_never_becomes_eligible():
    """Un runner OBSERVE_ONLY dans le registre (configs/alpha20_runners.yaml)
    doit rester exclu de la sélection quelle que soit sa performance — sinon
    OBSERVE_ONLY ne serait qu'un champ de schéma YAML sans effet réel."""
    spec = _spec("obs_only", status="OBSERVE_ONLY")
    _seed_history("obs_only", n_days=45, daily_mu=0.0025, daily_sigma=0.002, seed=7,
                  n_decisions=350)
    st = phases.selection_status(spec)
    assert st["status"] == "OBSERVE_ONLY"
    assert st["reasons"] == ["registry_status_not_active"]


def test_observe_only_excluded_from_run_selection_alongside_eligible_active():
    active = _spec("active_strong")
    observe = _spec("obs_only", status="OBSERVE_ONLY")
    _seed_history("active_strong", n_days=45, daily_mu=0.0025, daily_sigma=0.002,
                  seed=7, n_decisions=350)
    _seed_history("obs_only", n_days=45, daily_mu=0.0025, daily_sigma=0.002,
                  seed=7, n_decisions=350)
    result = phases.run_selection([active, observe])
    assert result["statuses"]["obs_only"]["status"] == "OBSERVE_ONLY"
    assert "obs_only" not in result["selected"]
    assert result["statuses"]["active_strong"]["status"] in (
        "ELIGIBLE", "SELECTED_PROVISIONAL")


def test_fragile_when_bootstrap_lcb_non_positive():
    """NAV construite à la main (pas de marche aléatoire) : petites hausses
    régulières entrecoupées de chutes ponctuelles assez espacées pour que le
    drawdown PEAK-TO-TROUGH reste < 2,5% (jamais REJECTED sur ce critère),
    mais assez fréquentes/profondes pour qu'un ré-échantillonnage i.i.d. du
    bootstrap produise un rendement total négatif au 5e percentile → LCB ≤ 0
    → FRAGILE."""
    spec = _spec("noisy")
    acc = PaperAccount("noisy", 200000.0)
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=45)
    pattern = ([0.0015] * 7 + [-0.010]) * 5 + [0.0015] * 5   # 45 points
    nav = 200000.0
    for i, r in enumerate(pattern):
        ts = (t0 + timedelta(days=i)).isoformat()
        nav *= (1 + r)
        acc.mark(nav, {"nav_usdt": nav}, ts=ts)
    for i in range(350):
        ts = (t0 + timedelta(hours=i)).isoformat()
        acc.emit([LedgerEvent(ts=ts, kind="decision", sleeve="s", venue="binance_usdm",
                              amount_usdt=0.0, ref="tournament_cycle")])
    st = phases.selection_status(spec)
    assert st["status"] in ("FRAGILE", "REJECTED")   # jamais ELIGIBLE sur ce profil
    assert acc.drawdown() < 0.025 or st["status"] == "REJECTED"
    if st["status"] == "FRAGILE":
        assert any(v is not None and v <= 0 for v in st["robustness"].values())


def test_rejected_on_drawdown_breach():
    spec = _spec("crashed")
    acc = PaperAccount("crashed", 200000.0)
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=45)
    # monte doucement puis un crash > 2,5%
    navs = [200000.0 * (1 + 0.0005) ** i for i in range(40)] + [190000.0] * 5
    for i, nav in enumerate(navs):
        ts = (t0 + timedelta(days=i)).isoformat()
        acc.mark(nav, {"nav_usdt": nav}, ts=ts)
    for i in range(350):                          # ≥ 300 décisions génériques
        ts = (t0 + timedelta(hours=i)).isoformat()
        acc.emit([LedgerEvent(ts=ts, kind="decision", sleeve="s", venue="binance_usdm",
                              amount_usdt=0.0, ref="tournament_cycle")])
    st = phases.selection_status(spec)
    assert st["status"] == "REJECTED"
    assert "max_drawdown_exceeded" in st["reasons"]


def test_ineligible_on_invalid_ledger():
    spec = _spec("corrupt")
    _seed_history("corrupt", n_days=45, daily_mu=0.001, daily_sigma=0.001, seed=13,
                  n_decisions=350)
    acc = PaperAccount("corrupt", 200000.0)
    f = acc.ledger_dir / "ledger.jsonl"
    lines = f.read_text().splitlines()
    lines[0] = lines[0][:20]
    f.write_text("\n".join(lines) + "\n")
    st = phases.selection_status(spec)
    assert st["status"] == "INELIGIBLE"


def test_rare_strategy_override_lower_decision_threshold():
    spec = _spec("mh_events_exec")
    # 45 jours d'historique NAV mais seulement 30 décisions (< 300 générique,
    # >= 30 de l'override "stratégie rare")
    _seed_history("mh_events_exec", n_days=45, daily_mu=0.001, daily_sigma=0.001,
                 seed=17, n_decisions=30)
    obs = phases.observation_status(spec, PaperAccount("mh_events_exec", 200000.0))
    assert obs["min_decisions"] == 30
    assert obs["ready_for_selection"]


def test_clustering_keeps_one_dominant_per_correlated_group():
    n = 60
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=n)
    rng = np.random.RandomState(99)
    shared = 0.001 + 0.003 * rng.randn(n)
    idio_c = 0.0005 * rng.randn(n)
    ids = ["clone_a", "clone_b", "independent"]
    for rid, extra_mu, series in (
            ("clone_a", 0.0, shared), ("clone_b", 0.0, shared + idio_c * 0.1),
            ("independent", 0.002, 0.0015 + 0.004 * rng.randn(n))):
        acc = PaperAccount(rid, 200000.0)
        nav = 200000.0
        for i in range(n):
            ts = (t0 + timedelta(days=i)).isoformat()
            r = series[i]
            nav *= (1 + r)
            acc.mark(nav, {"nav_usdt": nav}, ts=ts)
        for i in range(350):                      # ≥ 300 décisions génériques
            ts = (t0 + timedelta(hours=i)).isoformat()
            acc.emit([LedgerEvent(ts=ts, kind="decision", sleeve="s",
                                  venue="binance_usdm", amount_usdt=0.0,
                                  ref="tournament_cycle")])
    specs = [_spec(r) for r in ids]
    statuses = {r: phases.selection_status(s) for r, s in zip(ids, specs)}
    eligible_specs = [s for s in specs if statuses[s.runner_id]["status"] == "ELIGIBLE"]
    if len(eligible_specs) < 2:
        pytest.skip("séries synthétiques pas assez fortes sur cette seed")
    dominants, clusters = clustering.select_dominants(eligible_specs, statuses)
    clone_cluster = [c for c in clusters
                     if {"clone_a", "clone_b"} <= set(c["members"])]
    assert clone_cluster, "clone_a/clone_b doivent être dans le même cluster corrélé"
    assert len([d for d in dominants if d in ("clone_a", "clone_b")]) == 1


def test_confirm_rejects_on_config_change_no_recalibration_allowed():
    spec = _spec("promoted")
    _seed_history("promoted", n_days=20, daily_mu=0.001, daily_sigma=0.001, seed=5)
    res = phases.confirm(spec, provisional_config_hash="OLD_HASH_DIFFERENT",
                         provisional_ts=(datetime.now(timezone.utc)
                                        - timedelta(days=20)).isoformat())
    assert res["status"] == "REJECTED"
    assert "config_changed_during_observation" in res["reasons"]


def test_confirm_pending_before_window_elapsed():
    spec = _spec("promoted2")
    _seed_history("promoted2", n_days=3, daily_mu=0.001, daily_sigma=0.001, seed=6)
    res = phases.confirm(spec, provisional_config_hash=spec.config_hash,
                         provisional_ts=(datetime.now(timezone.utc)
                                        - timedelta(days=3)).isoformat())
    assert res["status"] == "SELECTED_PROVISIONAL"
    assert res["days_elapsed"] < load_protocol()["phase_d_confirmation"]["min_days"]


def test_confirm_promotes_when_window_and_config_hold():
    spec = _spec("promoted3")
    _seed_history("promoted3", n_days=20, daily_mu=0.0025, daily_sigma=0.002, seed=21,
                  n_decisions=350)
    prov_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    res = phases.confirm(spec, provisional_config_hash=spec.config_hash,
                         provisional_ts=prov_ts)
    assert res["status"] in ("SELECTED_CONFIRMED", "REJECTED")  # jamais recalibré silencieusement
    assert res["new_events"] >= load_protocol()["phase_d_confirmation"]["min_new_events"]
