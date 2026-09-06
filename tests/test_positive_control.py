"""POSITIVE_CONTROL_ORACLE_V1 — l'instrument qui mesure ce que la chaîne RETIRE.

Le placebo borne l'appareil par le bas (un signal sans edge doit ressortir à
zéro). Celui-ci le borne par le haut : un edge connu doit ressortir intact.
Sans les deux, « aucun alpha n'a d'edge net du marché » ne se distingue pas de
« l'appareil ne sait pas voir un edge ».

Ce fichier protège les trois propriétés sans lesquelles l'instrument devient
dangereux plutôt qu'utile :

  1. il ne peut JAMAIS recevoir de capital — un alpha construit avec
     look-ahead affiche par construction un rendement magnifique ;
  2. son look-ahead est déclaré sur chaque ligne, pas seulement au registre ;
  3. l'injection est MESURÉE et non supposée — quand la cible dépasse le
     maximum de la coupe transversale, l'injection réelle est plus faible, et
     comparer au Δ nominal produirait une fausse atténuation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import scripts.run_positive_control_oracle as C
from src.institutional.live_alpha_lab import outcomes as O
from src.institutional.live_alpha_lab.eligibility import (
    EligibilityReason, ValidationLink, is_forward_eligible)
from src.institutional.live_alpha_lab.schema import (
    SYMBOL_COL_BY_ALPHA, TIME_COL_BY_ALPHA)

ALPHA = "POSITIVE_CONTROL_ORACLE_V1"
BAR = pd.Timestamp("2026-08-20T00:00:00Z")


# ── 1. jamais de capital, et pas promouvable par une édition ────────────────

def test_positive_control_never_receives_capital():
    v = is_forward_eligible({"alpha_id": ALPHA, "operational_status": "SIGNAL_SHADOW",
                             "scientific_status": "POSITIVE_CONTROL", "horizon": "fwd_4h"}, {})
    assert not v.eligible
    assert v.reason is EligibilityReason.BLOCK_POSITIVE_CONTROL


def test_it_cannot_be_promoted_by_editing_the_validation_registry():
    """La porte est AVANT la consultation du registre — comme pour le placebo,
    et pour une raison plus forte encore : son rendement simulé est garanti."""
    alpha = {"alpha_id": ALPHA, "operational_status": "SIGNAL_SHADOW",
             "scientific_status": "POSITIVE_CONTROL", "horizon": "fwd_4h"}
    fully_validated = {ALPHA: [ValidationLink("X", "VALIDATED_FOR_FORWARD", True, 999.0)]}
    v = is_forward_eligible(alpha, fully_validated)
    assert not v.eligible
    assert v.reason is EligibilityReason.BLOCK_POSITIVE_CONTROL


def test_registry_declares_the_lookahead_and_no_expected_edge():
    reg = yaml.safe_load((ROOT / "configs" / "live_alpha_registry.yaml").read_text())
    entry = next(a for a in reg["alphas"] if a["alpha_id"] == ALPHA)
    assert entry["scientific_status"] == "POSITIVE_CONTROL"
    assert entry["uses_lookahead"] is True
    assert entry["data_live"] is False
    # Une espérance ici produirait un edge_retention calculé contre un chiffre
    # obtenu en trichant.
    assert entry["expected_net_bps"] is None


# ── 2. l'injection, mesurée et non supposée ─────────────────────────────────

def _cross_section(values):
    return {f"S{i}": float(v) for i, v in enumerate(values)}


def test_level_zero_reduces_exactly_to_the_placebo():
    """Δ=0 doit rendre le symbole TIRÉ lui-même : c'est un second contrôle
    négatif, indépendant de PLACEBO_RANDOM_V1."""
    excess = _cross_section([-50, -10, 0, 10, 50])
    picked = C.select_for_bar(BAR, excess, 0.0, 3)
    assert picked
    for rec in picked:
        assert rec["symbol"] == rec["draw_symbol"]
        assert rec["injected_measured_bps"] == pytest.approx(0.0)


def test_a_positive_level_displaces_the_selection_upward_on_average():
    """En moyenne, pas décision par décision — et l'exception est instructive.

    Le tirage se fait SANS REMISE dans la barre : si le symbole le plus proche
    de la cible a déjà été retenu, on prend le suivant, qui peut se trouver
    SOUS le tirage. Sur une coupe de 49 symboles et 4 décisions l'effet est
    marginal, mais il existe — et c'est précisément pour ça que l'injection est
    mesurée ligne à ligne au lieu d'être supposée égale à Δ.
    """
    excess = _cross_section(np.linspace(0, 100, 30))
    picked = C.select_for_bar(BAR, excess, 20.0, 4)
    assert picked
    mean_injection = float(np.mean([r["injected_measured_bps"] for r in picked]))
    assert mean_injection > 0.0
    for rec in picked:
        assert rec["injected_measured_bps"] == pytest.approx(
            rec["oracle_excess_at_selection_bps"] - rec["oracle_excess_of_draw_bps"])


def test_without_replacement_can_force_a_pick_below_the_draw():
    """La propriété ci-dessus, épinglée explicitement plutôt que découverte
    une seconde fois : sur une coupe étroite, la contrainte sans remise dilue
    l'injection, et le ledger doit le montrer."""
    excess = _cross_section([0, 10, 20, 30, 40, 50])
    picked = C.select_for_bar(BAR, excess, 20.0, 5)
    assert any(r["injected_measured_bps"] < 0 for r in picked), (
        "sur une coupe de 6 symboles et 5 tirages, au moins une sélection doit "
        "être contrainte sous son tirage")


def test_the_injection_recorded_is_the_one_obtained_not_the_one_asked():
    """Cible au-delà du maximum de la coupe : le plus proche est le maximum, et
    l'injection réelle est PLUS FAIBLE que Δ. La comparer au Δ nominal
    fabriquerait une atténuation qui n'existe pas."""
    excess = _cross_section([0.0, 1.0, 2.0])
    picked = C.select_for_bar(BAR, excess, 1000.0, 1)
    assert len(picked) == 1
    rec = picked[0]
    assert rec["injection_target_bps"] == 1000.0
    assert rec["injected_measured_bps"] < 10.0
    assert rec["injected_measured_bps"] == pytest.approx(
        rec["oracle_excess_at_selection_bps"] - rec["oracle_excess_of_draw_bps"])


def test_selection_is_deterministic_for_a_given_bar_and_level():
    excess = _cross_section(np.linspace(-100, 100, 20))
    a = C.select_for_bar(BAR, excess, 29.0, 4)
    b = C.select_for_bar(BAR, excess, 29.0, 4)
    assert [r["symbol"] for r in a] == [r["symbol"] for r in b]


def test_two_levels_on_the_same_bar_are_not_the_same_draw():
    """Les niveaux tournent sur des barres disjointes précisément parce qu'ils
    pourraient sinon se marcher dessus ; la graine dépend du niveau."""
    excess = _cross_section(np.linspace(-100, 100, 20))
    assert (C.select_for_bar(BAR, excess, 0.0, 4)[0]["draw_seed"]
            != C.select_for_bar(BAR, excess, 29.0, 4)[0]["draw_seed"])


def test_no_symbol_is_selected_twice_within_a_bar():
    excess = _cross_section(np.linspace(-100, 100, 12))
    picked = C.select_for_bar(BAR, excess, 15.0, 6)
    symbols = [r["symbol"] for r in picked]
    assert len(symbols) == len(set(symbols))


# ── 3. il traverse la vraie chaîne, et il est reconnaissable ───────────────

def test_it_is_labelled_by_the_same_labeller_as_the_real_alphas():
    assert ALPHA in O.LABELABLE
    spec = O.LABELABLE[ALPHA]
    assert (spec.time_col, spec.symbol_col, spec.horizon) == ("event_time", "symbol", "fwd_4h")


def test_it_is_in_the_canonical_schema_table():
    assert TIME_COL_BY_ALPHA[ALPHA] == "event_time"
    assert SYMBOL_COL_BY_ALPHA[ALPHA] == "symbol"


def test_every_written_decision_declares_its_lookahead():
    """Le drapeau au registre ne suffit pas : un ledger lu hors contexte doit
    dire lui-même qu'il a été construit en connaissant le futur."""
    ledger = ROOT / "reports" / "live_alpha_lab" / ALPHA / "decisions.parquet"
    if not ledger.exists():
        pytest.skip("contrôle pas encore construit")
    df = pd.read_parquet(ledger)
    assert df["uses_lookahead"].all()
    assert (df["provenance"] == "FORWARD_LIVE").all()
    assert (df["decided_at"] == df["event_time"]).all(), "latence nulle par construction"


def test_the_chain_returns_exactly_what_was_injected():
    """La propriété centrale. L'oracle et le labelliseur lisent la même archive
    avec les mêmes objets : par décision, l'écart doit être nul. Un écart non
    nul signifierait qu'une couche intermédiaire — decluster, filtre des refus —
    déforme la mesure."""
    outcomes = O.load_outcomes(ALPHA)
    ledger = ROOT / "reports" / "live_alpha_lab" / ALPHA / "decisions.parquet"
    if outcomes is None or outcomes.empty or not ledger.exists():
        pytest.skip("contrôle pas encore labellisé")
    decisions = pd.read_parquet(ledger)
    for frame in (outcomes, decisions):
        frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    joined = outcomes.merge(
        decisions[["symbol", "direction", "event_time", "oracle_excess_at_selection_bps"]],
        on=["symbol", "direction", "event_time"], how="inner")
    ok = joined[(joined["dec_status"] == "OK") & joined["dec_excess_bps"].notna()]
    assert len(ok) > 100
    gap = (ok["dec_excess_bps"] - ok["oracle_excess_at_selection_bps"]).abs().max()
    assert gap == pytest.approx(0.0, abs=1e-9)
