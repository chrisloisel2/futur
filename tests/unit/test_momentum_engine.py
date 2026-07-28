"""
tests/test_momentum_engine.py
─────────────────────────────────────────────────────────────────────────────
Audit du 2026-07-21 (QUARANTINE_2026-07-21.md) : direction du classement,
symétrie de signe long/short, identité comptable indépendante, invariants
quotidiens. Toutes données jouets — aucune dépendance à qbee.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from research.edge_factory.cross_sectional_momentum_v1.momentum_engine import (
    check_daily_invariants, compute_btc_hedge, compute_weights, normalize_capped,
    portfolio_returns)


def test_rank_direction_long_high_score_short_low_score():
    scores = pd.DataFrame({"A": [0.30], "B": [0.10], "C": [-0.10], "D": [-0.30]})
    vol = pd.DataFrame({"A": [0.1], "B": [0.1], "C": [0.1], "D": [0.1]})
    w, is_long, is_short = compute_weights(scores, vol, long_short_frac=0.5,
                                           max_weight_per_name=1.0)
    assert w["A"].iloc[0] > 0
    assert w["B"].iloc[0] > 0
    assert w["C"].iloc[0] < 0
    assert w["D"].iloc[0] < 0
    assert is_long.loc[0, "A"] and is_long.loc[0, "B"]
    assert is_short.loc[0, "C"] and is_short.loc[0, "D"]
    assert not is_long.loc[0, "C"] and not is_short.loc[0, "A"]


def test_normalize_capped_never_violates_cap_with_few_names():
    # 3 noms éligibles, cap 15% : impossible de sommer à 1 sans violer le cap
    # (3 x 0.15 = 0.45 < 1) -- l'ancienne version renormalisait quand même
    # après le clip, ce qui violait le cap. La version corrigée doit accepter
    # une exposition brute < 1 plutôt que de dépasser le cap.
    raw = pd.DataFrame({"A": [10.0], "B": [1.0], "C": [1.0]})
    capped = normalize_capped(raw, cap=0.15)
    assert (capped.iloc[0] <= 0.15 + 1e-9).all()
    assert capped.sum(axis=1).iloc[0] <= 1.0 + 1e-9
    # water-filling : le maximum faisable (3 x 0.15 = 0.45) doit être atteint,
    # pas abandonné après le premier nom plafonné
    assert capped.sum(axis=1).iloc[0] == pytest.approx(0.45, abs=1e-9)


def test_normalize_capped_reaches_exactly_one_when_feasible():
    # 10 noms, cap 15% (max faisable 1.5 >= 1) : la somme DOIT atteindre 1
    # exactement, même si un nom domine au départ -- c'est la différence
    # entre un simple clip (abandonne le budget libéré) et un vrai
    # water-filling (le redistribue aux noms pas encore plafonnés).
    raw = pd.DataFrame([{"N0": 100.0, **{f"N{i}": 1.0 for i in range(1, 10)}}])
    capped = normalize_capped(raw, cap=0.15)
    assert (capped.iloc[0] <= 0.15 + 1e-9).all()
    assert capped.sum(axis=1).iloc[0] == pytest.approx(1.0, abs=1e-9)
    assert capped.iloc[0]["N0"] == pytest.approx(0.15, abs=1e-9)


def test_normalize_capped_dollar_neutrality_matches_across_legs_when_feasible():
    # Deux jambes de même taille (10 noms chacune) mais distributions de
    # vol différentes -- si le budget est atteignable des deux côtés
    # (n x cap >= 1), les deux jambes doivent sommer à EXACTEMENT 1,
    # donc signed_w = long_w - short_w doit sommer à 0 -- c'est
    # l'invariant qui a motivé le passage au water-filling (765/2373
    # jours violés avec le simple clip, cf. commit history).
    long_raw = pd.DataFrame([{f"L{i}": v for i, v in
                             enumerate([50.0] + [1.0] * 9)}])
    short_raw = pd.DataFrame([{f"L{i}": v for i, v in
                              enumerate([1.0] * 9 + [50.0])}])
    long_w = normalize_capped(long_raw, cap=0.15)
    short_w = normalize_capped(short_raw, cap=0.15)
    assert long_w.sum(axis=1).iloc[0] == pytest.approx(1.0, abs=1e-9)
    assert short_w.sum(axis=1).iloc[0] == pytest.approx(1.0, abs=1e-9)


def test_compute_weights_sign_symmetry():
    rng = np.random.RandomState(0)
    dates = pd.date_range("2024-01-01", periods=40, freq="D")
    cols = [f"N{i}" for i in range(10)]
    score = pd.DataFrame(rng.normal(size=(40, 10)), index=dates, columns=cols)
    vol = pd.DataFrame(rng.uniform(0.01, 0.1, size=(40, 10)), index=dates, columns=cols)

    w_pos, _, _ = compute_weights(score, vol, long_short_frac=0.2, max_weight_per_name=0.5)
    w_neg, _, _ = compute_weights(-score, vol, long_short_frac=0.2, max_weight_per_name=0.5)

    pd.testing.assert_frame_equal(w_pos, -w_neg, check_exact=False, atol=1e-9)


def test_portfolio_pnl_sign_symmetry_no_costs():
    rng = np.random.RandomState(1)
    dates = pd.date_range("2024-01-01", periods=60, freq="D")
    cols = [f"N{i}" for i in range(8)]
    score = pd.DataFrame(rng.normal(size=(60, 8)), index=dates, columns=cols)
    vol = pd.DataFrame(rng.uniform(0.01, 0.1, size=(60, 8)), index=dates, columns=cols)
    open_px = pd.DataFrame(100 * (1 + rng.normal(0, 0.02, size=(60, 9))).cumprod(axis=0),
                           index=dates, columns=cols + ["BTC"])
    funding = pd.DataFrame(0.0, index=dates, columns=cols + ["BTC"])
    beta = pd.DataFrame(rng.uniform(0.5, 1.5, size=(60, 8)), index=dates, columns=cols)

    w_pos, _, _ = compute_weights(score, vol, long_short_frac=0.25, max_weight_per_name=1.0)
    w_neg, _, _ = compute_weights(-score, vol, long_short_frac=0.25, max_weight_per_name=1.0)
    hedge_pos = compute_btc_hedge(w_pos, beta)
    hedge_neg = compute_btc_hedge(w_neg, beta)

    pr_pos = portfolio_returns(w_pos, hedge_pos, open_px, funding, cols, "BTC", exec_delay=2)
    pr_neg = portfolio_returns(w_neg, hedge_neg, open_px, funding, cols, "BTC", exec_delay=2)

    pd.testing.assert_series_equal(
        pr_pos["gross_ret"], -pr_neg["gross_ret"], check_exact=False, atol=1e-9)


def test_accounting_identity_matches_manual_loop():
    rng = np.random.RandomState(2)
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    cols = ["A", "B", "C"]
    w = pd.DataFrame(rng.uniform(-0.3, 0.3, size=(30, 3)), index=dates, columns=cols)
    hedge = pd.Series(rng.uniform(-0.2, 0.2, size=30), index=dates)
    open_px = pd.DataFrame(100 * (1 + rng.normal(0, 0.01, size=(30, 4))).cumprod(axis=0),
                           index=dates, columns=cols + ["BTC"])
    funding = pd.DataFrame(rng.normal(0, 0.0001, size=(30, 4)), index=dates, columns=cols + ["BTC"])

    pr = portfolio_returns(w, hedge, open_px, funding, cols, "BTC", exec_delay=2)

    open_ret = open_px.pct_change()
    w_lag = w.shift(2).fillna(0.0)
    hedge_lag = hedge.shift(2).fillna(0.0)
    for dt in dates[5:]:
        manual = sum(w_lag.loc[dt, c] * open_ret.loc[dt, c] for c in cols) \
                + hedge_lag.loc[dt] * open_ret.loc[dt, "BTC"]
        assert abs(pr["gross_ret"].loc[dt] - manual) < 1e-9, dt


def test_execution_delay_is_two_days_close_signal_to_open_open_return():
    # Un signal (poids) daté t=5 doit être exécuté à open(6) et capter le
    # rendement open(6)->open(7), donc apparaître à l'indice t=7 de la série
    # de rendement (shift(2) exact, pas shift(1)).
    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    cols = ["A"]
    w = pd.DataFrame(0.0, index=dates, columns=cols)
    w.loc[dates[5], "A"] = 1.0   # seul jour avec une position non nulle
    hedge = pd.Series(0.0, index=dates)
    open_px = pd.DataFrame({"A": [100.0] * 10, "BTC": [1.0] * 10}, index=dates)
    open_px.loc[dates[7], "A"] = 110.0   # +10% entre open(6) et open(7)
    funding = pd.DataFrame(0.0, index=dates, columns=["A", "BTC"])

    pr = portfolio_returns(w, hedge, open_px, funding, cols, "BTC", exec_delay=2)
    assert abs(pr["gross_ret"].loc[dates[7]] - 0.10) < 1e-9
    # aucun autre jour ne doit porter ce rendement (ni t=5, ni t=6)
    assert abs(pr["gross_ret"].loc[dates[5]]) < 1e-9
    assert abs(pr["gross_ret"].loc[dates[6]]) < 1e-9


def test_invariants_flag_cap_violation_and_beta_non_neutrality():
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    cols = ["A", "B"]
    w_bad = pd.DataFrame({"A": [0.5, 0.1, 0.1], "B": [-0.5, -0.1, -0.1]}, index=dates)
    hedge = pd.Series([0.9, 0.0, 0.0], index=dates)   # bêta net non neutre au jour 0
    beta = pd.DataFrame({"A": [1.0, 1.0, 1.0], "B": [1.0, 1.0, 1.0]}, index=dates)
    net_returns = pd.Series([0.01, -0.02, 0.0], index=dates)

    violations = check_daily_invariants(w_bad, hedge, beta, max_weight_per_name=0.15,
                                        net_returns=net_returns)
    assert violations.get("per_name_cap_violated", 0) >= 1
    assert violations.get("portfolio_beta_not_neutral_gt_0.05", 0) >= 1


def test_invariants_clean_when_well_formed():
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    w_ok = pd.DataFrame({"A": [0.1, 0.1, 0.1], "B": [-0.1, -0.1, -0.1]}, index=dates)
    hedge = pd.Series([0.0, 0.0, 0.0], index=dates)
    beta = pd.DataFrame({"A": [1.0, 1.0, 1.0], "B": [1.0, 1.0, 1.0]}, index=dates)
    net_returns = pd.Series([0.001, -0.002, 0.0005], index=dates)

    violations = check_daily_invariants(w_ok, hedge, beta, max_weight_per_name=0.15,
                                        net_returns=net_returns)
    assert violations == {}
