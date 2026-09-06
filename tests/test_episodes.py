"""tests/test_episodes.py — declustering pour FORWARD_LIVE (item 11 :
raw_signals != independent evidence)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.live_alpha_lab.episodes import decluster, summarize


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


def test_close_same_symbol_signals_form_one_cluster():
    df = pd.DataFrame([
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T00:00:00Z")},
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T02:00:00Z")},   # 2h après -> même cluster (<24h)
    ])
    summary = summarize(df, "ts", cluster_window_hours=24.0)
    assert summary.raw_signals == 2
    assert summary.independent_episodes == 1


def test_far_apart_same_symbol_signals_are_separate_episodes():
    df = pd.DataFrame([
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T00:00:00Z")},
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-05T00:00:00Z")},   # 4j après -> nouveau cluster
    ])
    summary = summarize(df, "ts", cluster_window_hours=24.0)
    assert summary.independent_episodes == 2


def test_different_symbols_never_share_a_cluster():
    df = pd.DataFrame([
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T00:00:00Z")},
        {"symbol": "ETHUSDT", "ts": _ts("2026-09-01T00:00:01Z")},   # même instant, symbole différent
    ])
    summary = summarize(df, "ts", cluster_window_hours=24.0)
    assert summary.independent_episodes == 2


def test_empty_input():
    summary = summarize(pd.DataFrame(), "ts")
    assert summary.raw_signals == 0 and summary.independent_episodes == 0


def test_decluster_adds_cluster_id_without_dropping_rows():
    df = pd.DataFrame([
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T00:00:00Z")},
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T01:00:00Z")},
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-10T00:00:00Z")},
    ])
    out = decluster(df, "ts", cluster_window_hours=24.0)
    assert len(out) == 3   # aucune ligne supprimée
    assert out["cluster_id"].nunique() == 2


# ═══════════════════════════════════════════════════════════════════════════
# LE CHAÎNAGE PAR LIEN SIMPLE — découvert le 2026-09-06 en simulant le null du
# placebo, et épinglé ici pour ne pas être redécouvert une troisième fois.
#
# `decluster` ouvre un nouvel épisode quand l'écart avec l'observation
# PRÉCÉDENTE dépasse la fenêtre — pas avec le DÉBUT de l'épisode courant. Des
# décisions régulièrement espacées d'un intervalle inférieur à la fenêtre
# s'enchaînent donc indéfiniment dans un seul épisode.
#
# Ce n'est pas un défaut à corriger en silence : changer la règle réécrirait
# toutes les tailles d'échantillon déjà publiées. C'est une contrainte de
# CONCEPTION à respecter — un alpha doit tirer à un pas STRICTEMENT supérieur à
# la fenêtre, sinon son n s'effondre sans prévenir.
# ═══════════════════════════════════════════════════════════════════════════

import pandas as pd
import pytest

from src.institutional.live_alpha_lab.episodes import decluster

_T0 = pd.Timestamp("2026-01-01T00:00:00Z")


def _series(step_hours, n, symbol="BTCUSDT"):
    return pd.DataFrame([
        {"event_time": _T0 + pd.Timedelta(hours=step_hours * i), "symbol": symbol, "v": 1.0}
        for i in range(n)])


def test_regular_sampling_below_the_window_collapses_to_one_episode():
    """80 décisions sur 10 jours, espacées de 3 h : un seul épisode."""
    out = decluster(_series(3.0, 80), "event_time", "symbol", 24.0)
    assert out["cluster_id"].nunique() == 1


def test_the_same_volume_in_separated_bursts_gives_many_episodes():
    rows = []
    for day in range(10):
        for i in range(8):
            rows.append({"event_time": _T0 + pd.Timedelta(days=2 * day, minutes=15 * i),
                         "symbol": "BTCUSDT", "v": 1.0})
    out = decluster(pd.DataFrame(rows), "event_time", "symbol", 24.0)
    assert out["cluster_id"].nunique() == 10


def test_a_step_exactly_equal_to_the_window_still_chains():
    """Le fil du rasoir, et il coupe du mauvais côté : la comparaison est
    STRICTE (`>`), donc un pas de 24 h avec une fenêtre de 24 h chaîne tout.

    C'est le cas d'un livre transversal rééquilibré quotidiennement : 2190
    rééquilibrages sur six ans deviennent UN épisode.
    """
    assert decluster(_series(24.0, 2190), "event_time", "symbol", 24.0)["cluster_id"].nunique() == 1
    # Une heure de moins sur la fenêtre, et l'échantillon passe de 1 à 2190.
    assert decluster(_series(24.0, 2190), "event_time", "symbol", 23.0)["cluster_id"].nunique() == 2190


def test_a_step_strictly_above_the_window_is_the_safe_design():
    for step in (25.0, 48.0, 72.0):
        n = 100
        out = decluster(_series(step, n), "event_time", "symbol", 24.0)
        assert out["cluster_id"].nunique() == n, (
            f"pas de {step} h avec une fenêtre de 24 h : chaque tirage doit être "
            f"son propre épisode")


def test_the_episode_count_of_a_continuous_sampler_is_capped_by_its_universe():
    """Conséquence pour tout alpha à cadence rapide : son n plafonne au nombre
    de symboles, quelle que soit la durée de collecte. C'est ce qui plafonne le
    placebo à ~49 épisodes."""
    rows = []
    for symbol in [f"S{i}" for i in range(12)]:
        rows.extend(_series(3.0, 200, symbol).to_dict("records"))
    out = decluster(pd.DataFrame(rows), "event_time", "symbol", 24.0)
    assert out["cluster_id"].nunique() == 12
