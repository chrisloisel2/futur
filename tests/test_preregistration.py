"""Le pré-enregistrement — cinq hypothèses, et un seuil qu'on ne peut pas oublier.

Le round 4 a testé ~700 mécanismes sans en valider un seul. À 700 essais le
seuil à franchir n'était plus celui qu'on regardait, et personne ne l'a
recalculé — parce que rien ne le recalculait.

Ce fichier protège la seule propriété qui empêche de recommencer : **le seuil
est dérivé du compte, jamais écrit**. Enregistrer cinq hypothèses de plus fait
monter la barre des cinq premières, mécaniquement, sans que quiconque ait à s'en
souvenir un soir de déception.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.preregistration import (
    FAMILY_ALPHA,
    MAX_HYPOTHESES_PER_BATCH,
    Hypothesis,
    PreregistrationError,
    current_threshold,
    register_batch,
    registered_count,
    retroactive_penalty,
    threshold_t,
    verify_ledger,
)


def _h(n: int, **over) -> Hypothesis:
    base = dict(
        hypothesis_id=f"H{n}", family="residual_reversal_xs",
        claim="le résidu sur-réagit puis revient",
        payer_who="un intervenant qui a dû se retourner vite",
        payer_why="son urgence lui coûte moins que son risque",
        payer_stop="si l'internalisation absorbait ce flux avant le marché",
        residual_definition="rendement moins bêta BTC sur 30 jours glissants",
        params="fenêtre 30 j, horizon 2 j, seuil 1.5 sigma, panier 10 long / 10 short",
        horizon_days=2.0, feeds=("bars_1h",),
    )
    base.update(over)
    return Hypothesis(**base)


# ── le seuil est dérivé, jamais écrit ──────────────────────────────────────

def test_the_threshold_rises_with_the_count():
    assert threshold_t(1) < threshold_t(5) < threshold_t(10) < threshold_t(700)
    assert threshold_t(5) == pytest.approx(2.3263, abs=1e-3)
    assert threshold_t(10) == pytest.approx(2.5758, abs=1e-3)


def test_the_threshold_is_never_stored_only_computed(tmp_path):
    """La propriété centrale : ajouter un lot relève la barre du lot précédent."""
    ledger = tmp_path / "PREREG.jsonl"
    register_batch("lot1", "f", [_h(i) for i in range(5)], "digest", "sha", path=ledger)
    before = current_threshold(ledger)["threshold_t"]

    register_batch("lot2", "f", [_h(i + 100) for i in range(5)], "digest", "sha", path=ledger)
    after = current_threshold(ledger)["threshold_t"]

    assert before == pytest.approx(threshold_t(5), abs=1e-3)
    assert after == pytest.approx(threshold_t(10), abs=1e-3)
    assert after > before, "tester cinq de plus DOIT coûter quelque chose"


def test_the_cost_of_an_extension_can_be_read_before_paying_it(tmp_path):
    ledger = tmp_path / "PREREG.jsonl"
    register_batch("lot1", "f", [_h(i) for i in range(5)], "digest", "sha", path=ledger)
    view = current_threshold(ledger, extra=5)
    assert view["n_if_extended"] == 10
    assert view["cost_of_extension_t"] > 0
    assert view["threshold_t_if_extended"] == pytest.approx(threshold_t(10), abs=1e-3)


def test_retroactive_penalty_names_what_a_sealed_batch_now_owes(tmp_path):
    ledger = tmp_path / "PREREG.jsonl"
    register_batch("lot1", "f", [_h(i) for i in range(5)], "d", "s", path=ledger)
    register_batch("lot2", "f", [_h(i + 100) for i in range(5)], "d", "s", path=ledger)
    penalty = retroactive_penalty(ledger)
    first = penalty["batches"][0]
    assert first["threshold_at_seal"] == pytest.approx(threshold_t(5), abs=1e-3)
    assert first["threshold_now"] == pytest.approx(threshold_t(10), abs=1e-3)
    assert first["penalty_t"] > 0


# ── la sixième est refusée ─────────────────────────────────────────────────

def test_a_sixth_hypothesis_in_one_batch_is_refused(tmp_path):
    ledger = tmp_path / "PREREG.jsonl"
    with pytest.raises(PreregistrationError) as raised:
        register_batch("lot1", "f", [_h(i) for i in range(6)], "d", "s", path=ledger)
    assert "plafond" in str(raised.value)
    assert not ledger.exists(), "un lot refusé ne doit rien écrire"


def test_a_batch_cannot_be_resealed_or_its_ids_reused(tmp_path):
    ledger = tmp_path / "PREREG.jsonl"
    register_batch("lot1", "f", [_h(i) for i in range(5)], "d", "s", path=ledger)
    with pytest.raises(PreregistrationError):
        register_batch("lot1", "f", [_h(i + 200) for i in range(2)], "d", "s", path=ledger)
    with pytest.raises(PreregistrationError):
        register_batch("lot2", "f", [_h(0)], "d", "s", path=ledger)


# ── une hypothèse mal formée n'est pas une hypothèse ───────────────────────

def test_a_hypothesis_without_a_falsifier_is_refused():
    with pytest.raises(PreregistrationError):
        _h(1, payer_stop="")


def test_a_parameter_range_is_refused():
    """Une plage est une famille d'hypothèses déguisée en une seule — la façon
    la plus courante de tester quinze choses en croyant en tester une."""
    for bad in ("horizon entre 1 et 3 jours, panier 10",
                "fenêtre 20 à 40 jours, horizon 2 j",
                "fenêtre 30..60 j, horizon 2 j"):
        with pytest.raises(PreregistrationError) as raised:
            _h(1, params=bad)
        assert "PLAGE" in str(raised.value)


def test_a_single_parameter_set_passes():
    h = _h(1, params="fenêtre 30 j, horizon 2 j, seuil 1.5 sigma, panier 10/10")
    assert h.digest()


# ── la chaîne détecte la retouche ──────────────────────────────────────────

def test_the_ledger_detects_a_hypothesis_added_after_the_fact(tmp_path):
    ledger = tmp_path / "PREREG.jsonl"
    register_batch("lot1", "f", [_h(i) for i in range(3)], "d", "s", path=ledger)
    assert verify_ledger(ledger)["ok"] is True

    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    record["hypotheses"].append({"hypothesis_id": "SMUGGLED"})
    ledger.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
                      encoding="utf-8")
    assert verify_ledger(ledger)["ok"] is False


def test_the_ledger_detects_a_threshold_that_no_longer_matches_its_count(tmp_path):
    """Le scénario qui compte : quelqu'un ajoute des hypothèses et laisse le
    seuil où il était."""
    ledger = tmp_path / "PREREG.jsonl"
    register_batch("lot1", "f", [_h(i) for i in range(5)], "d", "s", path=ledger)
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    record["threshold_t"] = 1.64          # « on garde le seuil d'une seule hypothèse »
    import hashlib
    payload = {k: v for k, v in record.items() if k != "record_hash"}
    record["record_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    ledger.write_text(json.dumps(record, sort_keys=True, separators=(",", ":"), default=str) + "\n",
                      encoding="utf-8")
    # La chaîne est intacte — c'est le SEUIL qui trahit.
    result = verify_ledger(ledger)
    assert result["ok"] is False
    assert "seuil" in result["error"]


def test_an_empty_batch_is_not_a_preregistration(tmp_path):
    with pytest.raises(PreregistrationError):
        register_batch("lot1", "f", [], "d", "s", path=tmp_path / "L.jsonl")


# ── la contamination : une partition de données, pas un seuil ──────────────

def test_a_contamination_burns_a_period_without_charging_trials(tmp_path):
    """La multiplicité s'applique aux tests sur les MÊMES données. Des regards
    sur 2022-2025 n'inflatent pas le taux de faux positifs d'un test mené sur
    2026. Facturer en plus des essais paierait deux fois la même chose."""
    from src.institutional.live_alpha_lab.preregistration import (
        burned_periods, record_contamination)
    ledger = tmp_path / "PREREG.jsonl"
    register_batch("lot1", "f", [_h(i) for i in range(3)], "d", "s", path=ledger)
    before = current_threshold(ledger)["threshold_t"]

    record_contamination(
        record_id="CONTAM_1", family="residual_reversal_xs",
        burned_periods=[("2022-03-22", "2025-12-31")],
        untouched_periods=[("2026-01-01", "2026-08-31")],
        what_was_measured="IC transversaux et rendements futurs à 1/2/3 j",
        n_looks=601, path=ledger)

    assert current_threshold(ledger)["threshold_t"] == before
    assert verify_ledger(ledger)["ok"] is True


def test_burned_periods_are_scoped_to_their_family(tmp_path):
    """Une contamination de F3 ne brûle pas la période pour F1 : ce sont deux
    questions différentes posées à la même donnée."""
    from src.institutional.live_alpha_lab.preregistration import (
        burned_periods, record_contamination)
    ledger = tmp_path / "PREREG.jsonl"
    record_contamination(
        record_id="C1", family="residual_reversal_xs",
        burned_periods=[("2022-03-22", "2025-12-31")],
        untouched_periods=[], what_was_measured="IC", n_looks=10, path=ledger)
    assert burned_periods("residual_reversal_xs", ledger) == [("2022-03-22", "2025-12-31")]
    assert burned_periods("leverage_crowding", ledger) == []


def test_a_contamination_without_a_burned_period_is_refused(tmp_path):
    from src.institutional.live_alpha_lab.preregistration import record_contamination
    with pytest.raises(PreregistrationError):
        record_contamination(record_id="C", family="f", burned_periods=[],
                             untouched_periods=[], what_was_measured="x", n_looks=1,
                             path=tmp_path / "L.jsonl")


def test_a_contamination_must_say_what_was_measured(tmp_path):
    """Sans ça, la portée de la contamination est invérifiable."""
    from src.institutional.live_alpha_lab.preregistration import record_contamination
    with pytest.raises(PreregistrationError):
        record_contamination(record_id="C", family="f",
                             burned_periods=[("2022-01-01", "2025-12-31")],
                             untouched_periods=[], what_was_measured="  ", n_looks=1,
                             path=tmp_path / "L.jsonl")
