"""Le choix du design est-il vraiment aveugle au résultat ?

Le balayage du budget d'épisodes a produit 27 configurations, et l'une d'elles
affichait `t = 2,77` sur des paniers ALÉATOIRES. Si un t-stat avait informé le
choix de la configuration retenue, ces 27 regards entreraient dans le budget
d'essais et le seuil passerait de 2,326 à 2,955.

La réponse ne doit pas être une phrase dans un rapport. Ce fichier la rend
vérifiable : on perturbe les moyennes et on montre que le choix ne bouge pas.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_episode_budget import select_design, viable_configs


def _rows():
    """Un jeu de configurations dont les σ, n et moyennes sont indépendants."""
    rng = np.random.default_rng(3)
    out = []
    for horizon in (1.0, 2.0, 3.0):
        for rebalance in (1.0, 2.0, 3.0, 5.0):
            for k in (5, 10, 15):
                n = int(rng.integers(200, 1200))
                sigma = float(rng.uniform(90, 600))
                out.append({
                    "horizon_days": horizon, "rebalance_days": rebalance, "k": k,
                    "n_episodes": n, "sigma_bps": sigma,
                    "se_bps": sigma / np.sqrt(n),
                    "mde_preregistered_bps": (2.326 + 0.8416) * sigma / np.sqrt(n),
                    "mean_bps": float(rng.normal(0, 40)),
                })
    return out


def test_the_choice_does_not_move_when_every_mean_is_shuffled():
    """La preuve directe : les moyennes n'entrent pas dans la décision."""
    rows = _rows()
    reference = select_design(rows)
    rng = np.random.default_rng(11)
    for _ in range(50):
        means = [r["mean_bps"] for r in rows]
        rng.shuffle(means)
        for row, value in zip(rows, means):
            row["mean_bps"] = value
        assert select_design(rows) == reference


def test_the_choice_does_not_move_when_a_config_is_made_spectacular():
    """Le test qui compte vraiment : on fabrique un t-stat énorme sur une
    configuration qui n'était PAS retenue, et on vérifie qu'elle ne le devient
    pas. Si elle le devenait, le balayage aurait informé le design."""
    rows = _rows()
    reference = select_design(rows)
    loser = next(r for r in viable_configs(rows) if r is not reference)
    loser["mean_bps"] = 10_000.0        # t astronomique
    assert select_design(rows) == reference


def test_the_choice_does_move_when_sigma_or_n_change():
    """Le contrôle négatif : la règle DOIT réagir à σ et n, sinon le test
    précédent passerait pour une raison triviale (une règle qui ne lit rien)."""
    rows = _rows()
    reference = select_design(rows)
    loser = next(r for r in viable_configs(rows) if r is not reference)
    loser["mde_preregistered_bps"] = reference["mde_preregistered_bps"] / 10.0
    assert select_design(rows) is loser


def test_configs_that_chain_under_v1_are_excluded_on_structure_alone():
    """L'autre filtre appliqué au design : un pas ≤ fenêtre est écarté parce
    qu'il fond l'historique en un épisode — critère structurel, pas un résultat."""
    rows = _rows()
    for row in viable_configs(rows):
        assert row["rebalance_days"] * 24.0 > 24.0
    assert all(r["rebalance_days"] > 1.0 for r in viable_configs(rows))
