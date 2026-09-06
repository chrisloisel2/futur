"""
tests/test_executability_and_spec_gates.py — items C1 et C2.

Deux portes de capital ajoutées le 2026-09-06, et la consolidation de la table
alpha_id -> colonne temps qui vivait en trois copies divergentes.

C2 — BLOCK_UNRESOLVED_SPEC : un alpha `RECONSTRUCTED` ne reçoit pas de capital.
     Sa spec a été reconstruite À PARTIR des observations qui servent à le
     juger ; il ne peut donc pas produire une preuve forward jamais-vue.
C1 — BLOCK_NOT_EXECUTABLE : un alpha dont la latence médiane RÉCENTE dépasse
     son propre horizon ne peut pas recevoir de capital. Le système imprimait
     jusqu'ici `VALIDATED_FOR_FORWARD` + `eligible: true` + « 31/31 périmées »
     sans jamais refuser la contradiction.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.live_alpha_lab.eligibility import (
    EligibilityReason, ValidationLink, is_forward_eligible,
    recent_decision_lag_median_h)
from src.institutional.live_alpha_lab.schema import (
    SYMBOL_COL_BY_ALPHA, TIME_COL_BY_ALPHA)

_VALID = {"A": [ValidationLink("CAND", "VALIDATED_FOR_FORWARD", True, 30.0)]}


def _alpha(**kw):
    base = dict(alpha_id="A", operational_status="SIGNAL_SHADOW",
                scientific_status="FROZEN", horizon="fwd_4h")
    base.update(kw)
    return base


# ── C2 : spec non résolue ────────────────────────────────────────────────────

def test_reconstructed_alpha_gets_no_capital():
    v = is_forward_eligible(_alpha(scientific_status="RECONSTRUCTED"), _VALID)
    assert not v.eligible
    assert v.reason is EligibilityReason.BLOCK_UNRESOLVED_SPEC


def test_reconstructed_is_blocked_even_when_fully_validated():
    """Le trou exact que cette porte bouche : SHORT_COVERING est RECONSTRUCTED
    et ne reçoit aujourd'hui aucun capital, mais par ACCIDENT du registre de
    validation. Le jour où un candidat le validerait, il recevrait du capital
    en restant RECONSTRUCTED."""
    v = is_forward_eligible(_alpha(scientific_status="RECONSTRUCTED"), _VALID)
    assert v.reason is EligibilityReason.BLOCK_UNRESOLVED_SPEC


def test_reconstructed_has_its_own_reason_not_the_dead_mechanism_one():
    """Deux motifs distincts : là le mécanisme est mort, ici il est peut-être
    bon mais sa spec n'est pas établie. Jamais un seul fourre-tout."""
    dead = is_forward_eligible(_alpha(scientific_status="REJECTED"), _VALID)
    unresolved = is_forward_eligible(_alpha(scientific_status="RECONSTRUCTED"), _VALID)
    assert dead.reason is EligibilityReason.BLOCK_SCIENTIFIC_STATUS
    assert unresolved.reason is EligibilityReason.BLOCK_UNRESOLVED_SPEC


def test_frozen_alpha_is_untouched_by_the_new_gate():
    assert is_forward_eligible(_alpha(), _VALID).eligible


# ── C1 : exécutabilité ───────────────────────────────────────────────────────

def test_latency_above_its_own_horizon_blocks_capital():
    v = is_forward_eligible(_alpha(), _VALID, decision_lag_median_h=18.5)
    assert not v.eligible
    assert v.reason is EligibilityReason.BLOCK_NOT_EXECUTABLE
    assert "18.5h" in v.detail and "fwd_4h" in v.detail


def test_latency_below_horizon_passes():
    assert is_forward_eligible(_alpha(), _VALID, decision_lag_median_h=0.3).eligible


def test_unknown_latency_does_not_block():
    """Un alpha qui vient d'être figé n'a aucune décision forward, donc aucune
    latence mesurable. Fail-closed ICI empêcherait tout nouvel alpha de
    démarrer — le fail-closed a déjà lieu en amont (validation)."""
    assert is_forward_eligible(_alpha(), _VALID, decision_lag_median_h=None).eligible


def test_unknown_horizon_does_not_block():
    v = is_forward_eligible(_alpha(horizon="fwd_inconnu"), _VALID,
                            decision_lag_median_h=999.0)
    assert v.eligible


def test_a_longer_horizon_tolerates_a_longer_latency():
    """La porte compare la latence à SON PROPRE horizon, pas à une constante."""
    assert not is_forward_eligible(_alpha(horizon="fwd_4h"), _VALID,
                                   decision_lag_median_h=18.5).eligible
    assert is_forward_eligible(_alpha(horizon="fwd_24h"), _VALID,
                               decision_lag_median_h=18.5).eligible


# ── mesure de la latence récente ─────────────────────────────────────────────

def _decisions(lags_h, ages_h, provenance="FORWARD_LIVE"):
    now = pd.Timestamp("2026-09-06T12:00:00Z")
    rows = []
    for lag, age in zip(lags_h, ages_h):
        decided = now - pd.Timedelta(hours=age)
        rows.append({"event_time": decided - pd.Timedelta(hours=lag),
                     "decided_at": decided, "provenance": provenance})
    return pd.DataFrame(rows)


def test_recent_lag_ignores_the_historical_catch_up():
    """Le cumul inclut des décisions nées périmées lors d'un rattrapage et ne
    redescend jamais. Un indicateur qui ne redescend pas après un incident
    condamnerait à vie un alpha réparé depuis."""
    df = _decisions(lags_h=[48.0, 48.0, 0.2, 0.3], ages_h=[100.0, 99.0, 2.0, 1.0])
    lag = recent_decision_lag_median_h(df, "event_time",
                                       now=pd.Timestamp("2026-09-06T12:00:00Z"))
    assert lag == pytest.approx(0.25, abs=0.01)


def test_recent_lag_is_none_when_nothing_recent():
    df = _decisions(lags_h=[48.0], ages_h=[100.0])
    assert recent_decision_lag_median_h(
        df, "event_time", now=pd.Timestamp("2026-09-06T12:00:00Z")) is None


def test_recent_lag_ignores_replay_rows():
    df = _decisions(lags_h=[0.2], ages_h=[1.0], provenance="REPLAY")
    assert recent_decision_lag_median_h(
        df, "event_time", now=pd.Timestamp("2026-09-06T12:00:00Z")) is None


def test_recent_lag_is_none_not_zero_when_unmeasurable():
    """0 se lirait comme « latence nulle », la valeur la plus permissive."""
    assert recent_decision_lag_median_h(pd.DataFrame(), "event_time") is None
    assert recent_decision_lag_median_h(
        _decisions([0.2], [1.0]), "colonne_absente") is None


# ── table canonique ──────────────────────────────────────────────────────────

def test_the_three_former_copies_now_share_one_table():
    from src.institutional.live_alpha_lab import trade_trace
    import scripts.compute_live_alpha_lab_scoreboard as sb
    import scripts.apply_provenance_tags as apt
    assert trade_trace._TIME_COL is TIME_COL_BY_ALPHA
    assert trade_trace._SYMBOL_COL is SYMBOL_COL_BY_ALPHA
    assert sb._TIME_COL is TIME_COL_BY_ALPHA
    assert sb._SYMBOL_COL is SYMBOL_COL_BY_ALPHA
    assert apt.TIME_COL_BY_ALPHA is TIME_COL_BY_ALPHA


def test_every_declared_runner_is_in_the_canonical_table():
    """Le mode de panne à éliminer : un alpha absent de la table disparaît
    silencieusement de la mesure au lieu d'être signalé."""
    import yaml
    root = Path(__file__).parents[1]
    cfg = yaml.safe_load((root / "configs" / "live_alpha_runners.yaml").read_text())
    missing = [r["alpha_id"] for r in cfg["runners"]
               if r["alpha_id"] not in TIME_COL_BY_ALPHA]
    assert not missing, f"runners absents de schema.TIME_COL_BY_ALPHA : {missing}"


def test_both_tables_cover_the_same_alphas():
    assert set(TIME_COL_BY_ALPHA) == set(SYMBOL_COL_BY_ALPHA)


# ── C3 : hygiène du registre ────────────────────────────────────────────────

def test_c3_no_candidate_stays_unimplemented_without_a_decision():
    """LE test qui force la décision. Passé le seuil, il échoue tant que
    personne n'a tranché — implémenter, ou retirer avec motif. Volontairement
    un test rouge plutôt qu'une mutation automatique du registre : une
    décision scientifique ne doit pas disparaître dans un cron."""
    import yaml
    from src.institutional.live_alpha_lab.eligibility import stale_unimplemented
    reg = yaml.safe_load((Path(__file__).parents[1] / "configs"
                          / "live_alpha_registry.yaml").read_text())
    stale = stale_unimplemented(reg["alphas"])
    assert not stale, "\n".join(f"{x['alpha_id']} : {x['detail']}" for x in stale)


def test_c3_a_state_without_a_date_is_reported_not_ignored():
    """Un `operational_status` sans `operational_status_since` est un état sans
    durée : l'ignorer serait la façon la plus simple de contourner le contrôle."""
    from src.institutional.live_alpha_lab.eligibility import stale_unimplemented
    r = stale_unimplemented([{"alpha_id": "X", "operational_status": "CODE_MISSING"}])
    assert len(r) == 1 and r[0]["days"] is None


def test_c3_an_explicit_retirement_closes_the_case():
    from src.institutional.live_alpha_lab.eligibility import stale_unimplemented
    old = {"alpha_id": "X", "operational_status": "CODE_MISSING",
           "operational_status_since": "2020-01-01T00:00:00+00:00"}
    assert len(stale_unimplemented([old])) == 1
    tranche = dict(old, retirement_decision="jamais implémenté : mécanisme absorbé par Y")
    assert stale_unimplemented([tranche]) == []


def test_c3_a_running_alpha_is_never_flagged():
    from src.institutional.live_alpha_lab.eligibility import stale_unimplemented
    assert stale_unimplemented([{"alpha_id": "X", "operational_status": "SIGNAL_SHADOW",
                                 "operational_status_since": "2020-01-01T00:00:00+00:00"}]) == []
