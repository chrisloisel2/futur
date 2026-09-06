"""
tests/test_placebo_control.py — le contrôle à signal aléatoire (item D3).

Un placebo n'a de valeur que s'il satisfait trois propriétés, et chacune peut
se perdre silencieusement :
  1. il ne reçoit JAMAIS de capital, et ce refus ne dépend d'aucune donnée
     éditable ailleurs (sinon on pourrait le promouvoir par accident) ;
  2. il est DÉTERMINISTE : un placebo qu'on peut re-tirer jusqu'à obtenir le
     résultat voulu n'est pas un contrôle ;
  3. il traverse EXACTEMENT la même chaîne de mesure que les vrais — s'il en
     sortait, il ne contrôlerait plus rien.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import scripts.run_placebo_random_shadow as P
from src.institutional.live_alpha_lab import outcomes as O
from src.institutional.live_alpha_lab.eligibility import (
    EligibilityReason, ValidationLink, is_forward_eligible)
from src.institutional.live_alpha_lab.schema import (
    SYMBOL_COL_BY_ALPHA, TIME_COL_BY_ALPHA)

ALPHA = "PLACEBO_RANDOM_V1"


# ── 1. jamais de capital ─────────────────────────────────────────────────────

def test_placebo_never_receives_capital():
    v = is_forward_eligible({"alpha_id": ALPHA, "operational_status": "SIGNAL_SHADOW",
                             "scientific_status": "PLACEBO", "horizon": "fwd_4h"}, {})
    assert not v.eligible
    assert v.reason is EligibilityReason.BLOCK_PLACEBO


def test_placebo_cannot_be_promoted_by_editing_the_validation_registry():
    """Le refus est placé AVANT toute consultation du registre de validation :
    marquer le placebo VALIDATED_FOR_FORWARD ne l'ouvre pas."""
    alpha = {"alpha_id": ALPHA, "operational_status": "SIGNAL_SHADOW",
             "scientific_status": "PLACEBO", "horizon": "fwd_4h"}
    fully_validated = {ALPHA: [ValidationLink("X", "VALIDATED_FOR_FORWARD", True, 99.0)]}
    v = is_forward_eligible(alpha, fully_validated)
    assert not v.eligible
    assert v.reason is EligibilityReason.BLOCK_PLACEBO


def test_registry_declares_it_placebo_with_no_expected_edge():
    reg = yaml.safe_load((ROOT / "configs" / "live_alpha_registry.yaml").read_text())
    e = next(a for a in reg["alphas"] if a["alpha_id"] == ALPHA)
    assert e["scientific_status"] == "PLACEBO"
    # un placebo n'a pas d'espérance à confirmer -- une valeur ici produirait
    # un edge_retention qui n'aurait aucun sens
    assert e["expected_net_bps"] is None
    assert e["expected_net_bps_basis"] is None


# ── 2. déterminisme ──────────────────────────────────────────────────────────

def test_the_draw_is_reproducible_for_a_given_bar():
    univ = [f"S{i}USDT" for i in range(50)]
    bar = pd.Timestamp("2026-09-06T09:45:00Z")
    a, sa = P.draw(univ, bar, 4)
    b, sb = P.draw(univ, bar, 4)
    assert a == b and sa == sb


def test_different_bars_draw_differently():
    univ = [f"S{i}USDT" for i in range(50)]
    a, _ = P.draw(univ, pd.Timestamp("2026-09-06T09:45:00Z"), 4)
    b, _ = P.draw(univ, pd.Timestamp("2026-09-06T09:50:00Z"), 4)
    assert a != b


def test_the_draw_has_no_repeats_within_a_bar():
    univ = [f"S{i}USDT" for i in range(50)]
    picks, _ = P.draw(univ, pd.Timestamp("2026-09-06T09:45:00Z"), 4)
    assert len(set(picks)) == len(picks) == 4


def test_it_never_anchors_on_the_bar_still_being_written():
    """Un vrai détecteur ne voit un événement qu'une fois sa barre close.
    Donner au placebo une longueur d'avance en ferait un contrôle plus
    favorable que ce qu'il contrôle."""
    now = pd.Timestamp("2026-09-06T09:47:31Z")
    bar = P.current_bar(now)
    assert bar == pd.Timestamp("2026-09-06T09:40:00Z")
    assert bar < now.floor("5min")


# ── 3. même chaîne de mesure que les vrais ───────────────────────────────────

def test_it_is_labelled_by_the_same_labeller():
    assert ALPHA in O.LABELABLE
    spec = O.LABELABLE[ALPHA]
    real = O.LABELABLE["LIQ_CASCADE_REPEAT_V1"]
    assert spec.horizon == real.horizon        # même horizon que ce qu'il contrôle
    assert spec.cross_sectional is False


def test_it_is_in_the_canonical_schema_table():
    assert TIME_COL_BY_ALPHA[ALPHA] == "event_time"
    assert SYMBOL_COL_BY_ALPHA[ALPHA] == "symbol"


def test_it_is_long_only_like_the_alphas_it_controls():
    """Le contrôle doit partager l'exposition directionnelle des cinq alphas
    labellisables, sinon il contrôlerait autre chose qu'eux."""
    led = ROOT / "reports" / "live_alpha_lab" / ALPHA / "decisions.parquet"
    if not led.exists():
        pytest.skip("le placebo n'a pas encore tourné sur cette machine")
    assert set(pd.read_parquet(led)["direction"]) == {"LONG"}


def test_the_cycle_actually_runs_it():
    """Un contrôle qui ne tourne pas ne contrôle rien — le mode d'échec exact
    qui avait laissé AMIHUD sans décision forward après son freeze."""
    cfg = yaml.safe_load((ROOT / "configs" / "live_alpha_runners.yaml").read_text())
    assert ALPHA in {r["alpha_id"] for r in cfg["runners"]}


# ── l'empreinte de règle est par alpha ───────────────────────────────────────

def test_adding_the_placebo_did_not_perturb_the_other_alphas_digest():
    """Une empreinte globale aurait changé pour TOUS les alphas dès l'ajout du
    placebo : leurs lignes suivantes auraient porté une empreinte différente
    des précédentes, ce qui se lirait comme « la règle a changé pour eux »
    alors que rien de les concernant n'a bougé. Une empreinte qui crie au loup
    cesse d'être lue."""
    digests = {a: O.label_params_digest(a) for a in O.LABELABLE}
    assert len(set(digests.values())) == len(digests)
    assert O.label_params_digest("LIQ_CASCADE_REPEAT_V1") != O.label_params_digest(ALPHA)
