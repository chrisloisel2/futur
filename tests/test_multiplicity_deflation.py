"""
tests/test_multiplicity_deflation.py — déflation par le nombre d'essais
(src/institutional/live_alpha_lab/multiplicity.py, item D2).

Ce que ces tests protègent : que le seuil CROISSE avec le nombre d'essais, et
que la dérivation d'un t-stat depuis un intervalle bootstrap soit correcte —
c'est elle qui rend la déflation applicable aux candidats dont le registre ne
conserve pas le t-stat.
"""
from __future__ import annotations

import math

import pytest

from src.institutional.live_alpha_lab.multiplicity import (
    _norm_cdf, _norm_ppf, deflate, expected_max_null_tstat,
    tstat_from_one_sided_bound, tstat_from_two_sided_ci,
)


def test_a_single_trial_needs_no_haircut():
    assert expected_max_null_tstat(1) == 0.0


def test_the_threshold_grows_with_the_number_of_trials():
    """Le cœur du mécanisme : plus on teste, plus il faut être grand pour ne
    pas être simplement le maximum d'un grand nombre de tirages sans edge."""
    seuils = [expected_max_null_tstat(n) for n in (2, 10, 100, 904, 2000)]
    assert seuils == sorted(seuils)
    assert all(b > a for a, b in zip(seuils, seuils[1:]))


def test_the_threshold_matches_the_classical_extreme_value_asymptotic():
    """Recoupement INDÉPENDANT de l'implémentation d'Acklam, par l'asymptotique
    classique du maximum de N gaussiennes (Cramér) :

        a_N = sqrt(2 ln N) - (ln ln N + ln 4π) / (2 sqrt(2 ln N))
        E[max] ≈ a_N + γ / a_N

    Le premier terme seul, sqrt(2 ln N), est une borne SUPÉRIEURE grossière
    (3,03 contre 2,53 à N=100) : s'en servir comme référence ferait échouer un
    calcul pourtant correct. C'est le développement au second ordre qui est le
    bon point de comparaison."""
    for n in (100, 1000, 10_000):
        root = math.sqrt(2 * math.log(n))
        a_n = root - (math.log(math.log(n)) + math.log(4 * math.pi)) / (2 * root)
        asymptotic = a_n + 0.5772156649015329 / a_n
        assert expected_max_null_tstat(n) == pytest.approx(asymptotic, rel=0.05)
        # et le premier terme seul reste bien au-dessus
        assert expected_max_null_tstat(n) < root


def test_deflation_flips_a_candidate_that_survives_a_small_search():
    """Le même t-stat survit à 10 essais et pas à 2 000 — c'est exactement le
    point : un edge n'est pas une propriété du candidat seul, mais du candidat
    ET du nombre de mécanismes qu'il a fallu essayer pour le trouver."""
    petit = deflate(3.3, 10)
    grand = deflate(3.3, 2000)
    assert petit["survives"] is True
    assert grand["survives"] is False
    assert petit["p_deflated"] > grand["p_deflated"]


def test_deflation_carries_its_own_caveat():
    d = deflate(3.3, 904)
    assert "BORNE BASSE" in d["note"]
    assert "MULTIPLICITÉ" in d["note"]


def test_normal_quantile_and_cdf_are_mutual_inverses():
    for p in (0.01, 0.25, 0.5, 0.75, 0.99, 0.999):
        assert _norm_cdf(_norm_ppf(p)) == pytest.approx(p, abs=1e-6)


def test_tstat_derived_from_the_bootstrap_matches_the_declared_one():
    """LE recoupement qui valide la méthode : BTC_LEAD_ALT_CASCADE est le seul
    candidat portant à la fois un t-stat déclaré (3,315) et un p05 bootstrap
    (23,70 pour une moyenne de 46,87). La dérivation doit retomber dessus."""
    t = tstat_from_one_sided_bound(46.87, 23.70)
    assert t == pytest.approx(3.315, abs=0.03)


def test_tstat_from_a_two_sided_ci():
    # moyenne 22.12, IC95 [9.38, 35.88] -> SE = 13.25/1.96
    t = tstat_from_two_sided_ci(22.12, 9.38, 35.88)
    assert t == pytest.approx(22.12 / (13.25 / 1.959964), rel=1e-3)


def test_a_degenerate_interval_yields_none_not_infinity():
    """Un IC de largeur nulle donnerait un t infini — le refuser vaut mieux
    que publier une significativité créée par une division par zéro."""
    assert tstat_from_two_sided_ci(10.0, 5.0, 5.0) is None
    assert tstat_from_one_sided_bound(10.0, 10.0) is None


def test_zero_trials_is_rejected():
    with pytest.raises(ValueError):
        expected_max_null_tstat(0)
