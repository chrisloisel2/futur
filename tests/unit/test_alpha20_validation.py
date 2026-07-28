"""
tests/test_alpha20_validation.py
─────────────────────────────────────────────────────────────────────────────
DSR/PBO réels, gates sleeve, échelle de promotion, registre d'expériences
(xvenue verrouillé à vie), réconciliation (gate 0,01 USDT sur volets
événementiels). Aucun réseau.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.alpha20.validation import experiment_registry as er
from src.alpha20.validation import live_reconciliation as lr
from src.alpha20.validation import promotion_gate as pg


def test_dsr_strong_vs_weak():
    idx = pd.date_range("2025-01-01", periods=500, freq="1D")
    rng = np.random.RandomState(1)
    strong = pd.Series(0.003 + 0.005 * rng.randn(500), index=idx)
    weak = pd.Series(0.0001 + 0.01 * rng.randn(500), index=idx)
    assert pg.deflated_sharpe_ratio(strong, n_trials=10) > 0.95
    assert pg.deflated_sharpe_ratio(weak, n_trials=100) < 0.95
    assert pg.deflated_sharpe_ratio(strong.iloc[:10], 1) == 0.0  # trop court


def test_phi_inv_sanity():
    assert abs(pg._phi_inv(0.975) - 1.959964) < 1e-4
    assert abs(pg._phi_inv(0.5)) < 1e-9
    assert abs(pg._phi(pg._phi_inv(0.95)) - 0.95) < 1e-6


def test_pbo_overfit_vs_real():
    rng = np.random.RandomState(3)
    T = 400
    # 50 stratégies de bruit pur → sélectionner la meilleure IS = overfit → PBO haut
    noise = pd.DataFrame(rng.randn(T, 50) * 0.01)
    assert pg.pbo_cscv(noise, n_splits=100) > 0.3
    # une vraie stratégie parmi le bruit → le best IS est souvent la vraie → PBO bas
    real = noise.copy()
    real[0] = 0.004 + rng.randn(T) * 0.005
    assert pg.pbo_cscv(real, n_splits=100) < 0.10
    assert pg.pbo_cscv(noise.iloc[:20], n_splits=10) == 1.0     # indécidable


def test_gate_sleeve_pf_and_top10():
    ev = pd.Series([0.01] * 30 + [-0.005] * 20)
    res = {g.gate: g for g in pg.gate_sleeve(ev, ev * 0.5, recent_year_net=0.02)}
    assert res["pf_min"].passed and res["pf_min"].value == pytest.approx(3.0)
    assert res["top10_events_removed_positive"].passed
    assert res["no_destructive_recent_year"].passed
    res = {g.gate: g for g in pg.gate_sleeve(ev, ev - 0.02, recent_year_net=-0.05)}
    assert not res["costs_x2_positive"].passed
    assert not res["no_destructive_recent_year"].passed


def test_promotion_ladder():
    r = pg.can_promote("shadow", closed_trades=120, full_cycles=0,
                       tracking_error=0.1, risk_violations=0,
                       net_return=0.01, days_in_stage=31)
    assert r["promote"] and r["next"]["stage"] == "paper_executable"
    r = pg.can_promote("shadow", closed_trades=10, full_cycles=1,
                       tracking_error=0.1, risk_violations=0,
                       net_return=0.01, days_in_stage=31)
    assert not r["promote"]                       # ni 100 trades ni 3 cycles
    assert not pg.can_promote("live_5pct", 999, 9, 0.0, 0, 1.0, 999)["promote"]


def test_registry_locks_xvenue_forever():
    with pytest.raises(er.RecycledExperimentError):
        er.guard_new_experiment("funding_xvenue_v0", new_thesis="nouvelle idée")
    with pytest.raises(er.RecycledExperimentError):
        er.guard_new_experiment("xvenue_v2_hl_only", new_thesis="maker only")
    with pytest.raises(er.RecycledExperimentError):
        er.guard_new_experiment("top_traders_divergence")   # sans thèse
    ok = er.guard_new_experiment("top_traders_divergence", "données tick achetées")
    assert ok["status"] == "reopened_with_thesis"
    assert er.guard_new_experiment("btc_variance_premium")["status"] == "new"
    assert er.guard_new_experiment("carry_basis_v12")["status"] == "foundation"
    # carry_solusdt/carry_bnbusdt (9c410e1) : bloqués sans thèse, mais PAS
    # verrouillés à vie comme xvenue — une vraie réouverture documentée reste
    # possible via le mécanisme normal, pas une interdiction permanente.
    with pytest.raises(er.RecycledExperimentError):
        er.guard_new_experiment("carry_solusdt")
    with pytest.raises(er.RecycledExperimentError):
        er.guard_new_experiment("carry_bnbusdt")
    reopened_sol = er.guard_new_experiment(
        "carry_solusdt", "changement matériel d'exécution : basis réel câblé")
    assert reopened_sol["status"] == "reopened_with_thesis"
    reopened_bnb = er.guard_new_experiment(
        "carry_bnbusdt", "changement matériel d'exécution : basis réel câblé")
    assert reopened_bnb["status"] == "reopened_with_thesis"


def test_provenance_blocked_never_usable_as_evidence_even_with_thesis():
    """cross_exchange_stress_gate_h2 (STRESS_GATE) : provenance non vérifiable,
    PAS falsifié — donc PAS dans closed_no_edge, mais toujours bloqué en
    entrée, même avec une thèse (contrairement à un NO_EDGE recyclable)."""
    with pytest.raises(er.ProvenanceBlockedError):
        er.guard_new_experiment("cross_exchange_stress_gate_h2")
    with pytest.raises(er.ProvenanceBlockedError):
        er.guard_new_experiment("cross_exchange_stress_gate_h2",
                                "nouvelle thèse quelconque")
    hit = er.lookup("cross_exchange_stress_gate_h2")
    assert hit["section"] == "provenance_blocked"
    assert hit["current_status"] == "UNVERIFIED_PROVENANCE"
    assert hit["superseding_experiment_status"] == "closed_no_incremental_edge"


def test_superseding_reproduction_closed_no_incremental_edge_not_permanent():
    """stress_gate_dispersion_v2_reproduction a conclu (Phase 3, e863335) :
    NO_INCREMENTAL_EDGE, pas NO_EDGE au sens absolu — bloqué sans thèse,
    mais réouvrable avec une thèse nouvelle, comme tout closed_no_edge
    ordinaire (contrairement au verrou permanent xvenue)."""
    with pytest.raises(er.RecycledExperimentError):
        er.guard_new_experiment("stress_gate_dispersion_v2_reproduction")
    reopened = er.guard_new_experiment(
        "stress_gate_dispersion_v2_reproduction",
        "nouvelle thèse : mécanisme différent, pas juste un seuil/horizon modifié")
    assert reopened["status"] == "reopened_with_thesis"
    hit = er.lookup("stress_gate_dispersion_v2_reproduction")
    assert hit["verdict"] == "NO_INCREMENTAL_EDGE"


def test_reconciliation_gate(monkeypatch, tmp_path):
    audit = {"run": "2026-07-19T00:00:00Z", "verdict": "COMPTABILITÉ_CONFIRMÉE",
             "checks": {"carry_vs_funding_api_usdt":
                        {"gap": -0.008, "ok": True},
                        "fees_vs_bareme_usdt": {"gap": 0.01, "ok": True}},
             "carry_detail": [{"symbol": "BTCUSDT",
                               "funding_time": "2026-07-18T00:00:00+00:00",
                               "rate": 3e-05, "notional_usdt": 91560.0,
                               "accrual_usdt": 2.815}],
             "fees_lines": [{"line": "2026-07-17T10:02:55Z carry x",
                             "fee_usdt": 32.05}]}
    assert lr.evaluate(audit)["passed"]
    audit["checks"]["fees_vs_bareme_usdt"]["gap"] = 0.02   # > 0,01 USDT
    assert not lr.evaluate(audit)["passed"]
    audit["checks"]["fees_vs_bareme_usdt"]["gap"] = 0.005
    audit["checks"]["carry_vs_funding_api_usdt"]["ok"] = False
    assert not lr.evaluate(audit)["passed"]

    from src.alpha20.accounting import event_ledger
    monkeypatch.setattr(event_ledger, "LEDGER_DIR", tmp_path / "ledger")
    n = lr.ingest_facts(audit, "AUDIT_test.json")
    assert n == 2 and lr.ingest_facts(audit, "AUDIT_test.json") == 0  # idempotent
    df = event_ledger.read()
    assert set(df["kind"]) == {"funding", "fee"}
    assert df[df["kind"] == "fee"]["amount_usdt"].iloc[0] == -32.05


def _dec(ts, fees_cum, carry_cum):
    from src.alpha20.contracts import LedgerEvent
    return LedgerEvent(ts=ts, kind="decision", sleeve="portfolio",
                       venue="binance_usdm", amount_usdt=0.0, ref="rebalance",
                       meta={"mongo_fees_cum": fees_cum,
                             "mongo_carry_cum": carry_cum, "exec_usdt": {}})


def test_forward_gate_per_interval(monkeypatch, tmp_path):
    from src.alpha20.accounting import event_ledger
    from src.alpha20.contracts import LedgerEvent
    monkeypatch.setattr(event_ledger, "LEDGER_DIR", tmp_path / "ledger")
    g = lr.forward_gate()
    assert g["passed"] is None and g["consecutive_ok"] == 0   # rien → pending
    event_ledger.append([_dec("2026-07-20T02:30:00Z", -100.0, 10.0)])
    assert lr.forward_gate()["status"] == "pending"           # 1 seul rebalance
    event_ledger.append([
        LedgerEvent(ts="2026-07-20T08:00:00Z", kind="funding",
                    sleeve="carry_BTCUSDT", venue="binance_usdm",
                    amount_usdt=2.5, ref="settlement"),
        LedgerEvent(ts="2026-07-20T10:30:00Z", kind="fee", sleeve="carry_BTCUSDT",
                    venue="binance_usdm", amount_usdt=-18.32, ref="rebalance"),
        _dec("2026-07-20T10:30:00Z", -118.32, 12.5),
    ])
    g = lr.forward_gate()
    assert g["passed"] and g["consecutive_ok"] == 1
    assert g["intervals"][-1]["gap_fees_usdt"] == 0.0
    assert g["intervals"][-1]["gap_carry_usdt"] == 0.0
    # intervalle 2 propre (aucun flux, cumuls inchangés) → 2 consécutifs
    event_ledger.append([_dec("2026-07-20T18:30:00Z", -118.32, 12.5)])
    g = lr.forward_gate()
    assert g["passed"] and g["consecutive_ok"] == 2
    # intervalle 3 divergent (Mongo bouge sans événement) → rouge, série cassée
    event_ledger.append([_dec("2026-07-21T02:30:00Z", -140.0, 12.5)])
    g = lr.forward_gate()
    assert not g["passed"] and g["consecutive_ok"] == 0
    assert g["intervals"][-1]["gap_fees_usdt"] > 0.01
    assert g["intervals"][0]["passed"]                        # l'historique reste vert


def test_forward_gate_ignores_audit_ingested_facts(monkeypatch, tmp_path):
    from src.alpha20.accounting import event_ledger
    from src.alpha20.contracts import LedgerEvent
    monkeypatch.setattr(event_ledger, "LEDGER_DIR", tmp_path / "ledger")
    event_ledger.append([
        _dec("2026-07-20T02:30:00Z", -100.0, 0.0),
        LedgerEvent(ts="2026-07-20T05:00:00Z", kind="fee", sleeve="portfolio",
                    venue="binance_usdm", amount_usdt=-32.05,
                    ref="AUDIT_2026-07-19.json"),             # fait ré-ingéré
        _dec("2026-07-20T10:30:00Z", -100.0, 0.0),            # Mongo inchangé
    ])
    g = lr.forward_gate()
    assert g["passed"]                                        # l'audit ne compte pas
