"""tests/test_scoreboard_executability.py — deux régressions du scoreboard
Live Alpha Lab, constatées le 2026-09-05.

RÉGRESSION 1 — angle mort de monitoring.
`_TIME_COL` / `_SYMBOL_COL` sont mappés explicitement (jamais devinés), mais
LIQ_CASCADE_REPEAT_SYSTEMIC_V1 et AMIHUD_ILLIQUIDITY_PREMIUM_V1 n'y avaient
jamais été ajoutés après leur déploiement. Conséquence silencieuse :
forward_age / last_trigger / actual_freq / latence sortaient VIDES pour eux --
précisément les deux alphas issus de la Validation Factory. Aucune erreur n'était
levée : les colonnes étaient juste blanches.

RÉGRESSION 2 — l'exécutabilité n'était pas mesurée.
Un alpha peut accumuler des décisions forward impeccables et rester
inexécutable si le lab découvre l'événement après l'expiration de son horizon.
Mesuré ce jour : la famille cascade découvre ses événements 45-48h après coup
pour un horizon de 4h, soit 100% de décisions périmées à l'arrivée, sans que
`forward_decisions` ni `confidence_level` ne le laissent voir.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import scripts.compute_live_alpha_lab_scoreboard as sb

SHADOW = ("SIGNAL_SHADOW", "EXECUTION_SHADOW")


def _shadow_alphas() -> list:
    reg = yaml.safe_load((ROOT / "configs" / "live_alpha_registry.yaml").read_text())
    return [a for a in reg["alphas"] if a.get("operational_status") in SHADOW]


def test_every_shadow_alpha_is_mapped_for_monitoring():
    """RÉGRESSION 1 : un alpha absent de _TIME_COL n'a AUCUNE métrique
    temporelle et ça ne lève rien -- il faut donc l'attraper ici."""
    missing = [a["alpha_id"] for a in _shadow_alphas() if a["alpha_id"] not in sb._TIME_COL]
    assert not missing, (
        f"alphas en shadow absents de _TIME_COL : {missing}. Leur forward_age, "
        "last_trigger, actual_freq et latence de décision sortiraient vides."
    )


def test_every_shadow_alpha_is_mapped_for_declustering():
    """_SYMBOL_COL accepte None (univers mono-symbole) mais l'ABSENCE de clé
    est un oubli : elle fait retomber independent_episodes à 0 en silence."""
    missing = [a["alpha_id"] for a in _shadow_alphas() if a["alpha_id"] not in sb._SYMBOL_COL]
    assert not missing, f"alphas en shadow absents de _SYMBOL_COL : {missing}"


def test_every_shadow_horizon_is_convertible_to_hours():
    """Sans horizon connu, expired_on_arrival ne peut pas être calculé et
    dégrade en 'horizon_inconnu' -- acceptable comme garde-fou, pas comme
    état permanent d'un alpha déployé."""
    unknown = [(a["alpha_id"], a.get("horizon")) for a in _shadow_alphas()
               if a.get("horizon") not in sb._HORIZON_HOURS]
    assert not unknown, f"horizons non convertibles en heures : {unknown}"


def _frame(event_times, decided_ats, provenance="FORWARD_LIVE"):
    return pd.DataFrame({
        "event_time": pd.to_datetime(event_times, utc=True),
        "decided_at": pd.to_datetime(decided_ats, utc=True),
        "provenance": [provenance] * len(event_times),
    })


def test_latency_flags_decisions_that_arrive_after_their_own_horizon():
    """RÉGRESSION 2, le cas réel : événement à T, décidé à T+45h, horizon 4h."""
    df = _frame(["2026-09-02T00:00:00Z", "2026-09-02T06:00:00Z"],
                ["2026-09-04T00:00:00Z", "2026-09-04T06:00:00Z"])
    lag, expired = sb.decision_latency(df, "event_time", "fwd_4h")
    assert lag == 48.0
    assert expired == "2/2"


def test_latency_counts_only_the_ones_actually_late():
    df = _frame(["2026-09-02T00:00:00Z", "2026-09-02T00:00:00Z"],
                ["2026-09-02T01:00:00Z", "2026-09-02T09:00:00Z"])   # 1h et 9h
    lag, expired = sb.decision_latency(df, "event_time", "fwd_4h")
    assert lag == 5.0            # médiane de [1, 9]
    assert expired == "1/2"


def test_replay_rows_are_excluded():
    """La latence mesure l'exécutabilité FORWARD ; le backfill historique a par
    construction un `decided_at` très postérieur et fausserait tout."""
    df = _frame(["2020-01-01T00:00:00Z"], ["2026-09-04T00:00:00Z"], provenance="REPLAY")
    assert sb.decision_latency(df, "event_time", "fwd_4h") == (None, None)


@pytest.mark.parametrize("kwargs", [
    {"df": None, "time_col": "event_time", "horizon": "fwd_4h"},
    {"df": _frame(["2026-09-02T00:00:00Z"], ["2026-09-02T01:00:00Z"]).drop(columns=["decided_at"]),
     "time_col": "event_time", "horizon": "fwd_4h"},
    {"df": _frame(["2026-09-02T00:00:00Z"], ["2026-09-02T01:00:00Z"]),
     "time_col": "colonne_absente", "horizon": "fwd_4h"},
])
def test_unmeasurable_latency_is_none_never_zero(kwargs):
    """Une latence inconnue ne doit JAMAIS se lire comme une latence nulle :
    c'est la différence entre « pas mesuré » et « instantané »."""
    assert sb.decision_latency(**kwargs) == (None, None)


def test_unknown_horizon_degrades_explicitly():
    df = _frame(["2026-09-02T00:00:00Z"], ["2026-09-02T01:00:00Z"])
    lag, expired = sb.decision_latency(df, "event_time", "un_horizon_jamais_vu")
    assert lag == 1.0
    assert expired == "horizon_inconnu"
