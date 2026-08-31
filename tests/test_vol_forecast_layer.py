"""tests/test_vol_forecast_layer.py — VOL_FORECAST_LAYER_V1 (Live Alpha Lab).

Covers: causal z-scoring (no lookahead), signal orientation, combination
(direction/confidence), realized-vol formula, the forecast/actual-RV
backfill mechanism (not-yet-elapsed stays null, elapsed gets filled,
idempotent, never overwrites an already-filled cell), empty-input handling,
and universe/registry fail-closed behavior of the runner.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.vol_forecast_layer.combine import (
    DIRECTION_Z_THRESHOLD, IC_CONFIDENCE_ANCHOR, ORIENTATION_SIGN,
    REFERENCE_ABS_IC, SIGNAL_COLUMNS, Z_WINDOW_DAYS,
    add_causal_zscores, causal_zscore, combine_forecast,
)
from src.institutional.engines.vol_forecast_layer.realized_vol import (
    ANNUALIZATION_FACTOR, compute_daily_realized_vol,
)
from src.institutional.engines.vol_forecast_layer.backfill import (
    backfill_actual_realized_rv,
)


# ── causal_zscore : no lookahead ────────────────────────────────────────────

def test_causal_zscore_prefix_consistency_no_lookahead():
    """Propriété clé de causalité : le z-score au jour t ne doit dépendre que
    des observations <= t. Le résultat tronqué à une date de coupure D doit
    être identique à celui obtenu en calculant directement sur l'historique
    tronqué à D (même principe que test_funding_basis_disagreement.py
    ::test_decluster_prefix_consistency_no_lookahead)."""
    rng = np.random.default_rng(42)
    n = 400
    s = pd.Series(rng.normal(size=n))

    cutoff = 250
    z_full = causal_zscore(s, window_days=180)
    z_prefix = causal_zscore(s.iloc[:cutoff], window_days=180)

    assert np.allclose(
        z_full.iloc[:cutoff].to_numpy(),
        z_prefix.to_numpy(),
        equal_nan=True,
    )


def test_causal_zscore_empty_and_warmup_are_nan():
    s = pd.Series([1.0, 2.0, 3.0])  # bien en dessous de min_periods=90
    z = causal_zscore(s, window_days=180)
    assert z.isna().all()


def test_causal_zscore_constant_series_is_nan_not_inf():
    """std==0 (série constante) -> NaN, jamais une division par zéro / inf."""
    s = pd.Series([5.0] * 200)
    z = causal_zscore(s, window_days=180)
    assert not np.isinf(z.dropna()).any()


# ── orientation ──────────────────────────────────────────────────────────────

def test_orientation_signs_match_source_report():
    """Les signes d'orientation doivent correspondre exactement à ceux
    documentés dans freeze_spec.json (copiés du rapport source) : spread
    HAUT -> RV forward plus BASSE (donc oriented = -z) ; far_otm_put_share et
    block_count_24h HAUTS -> RV forward plus HAUTE (oriented = +z)."""
    assert ORIENTATION_SIGN["rv_iv_spread"] == -1.0
    assert ORIENTATION_SIGN["far_otm_put_share"] == +1.0
    assert ORIENTATION_SIGN["block_count_24h"] == +1.0


def test_add_causal_zscores_orientation_applied():
    n = 200
    day = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    panel = pd.DataFrame({
        "day": day,
        "rv_iv_spread": np.linspace(0, 10, n),
        "far_otm_put_share": np.linspace(0, 1, n),
        "block_count_24h": np.linspace(0, 100, n),
    })
    out = add_causal_zscores(panel)
    valid = out.dropna(subset=["rv_iv_spread_z"])
    assert (valid["rv_iv_spread_oriented_z"] == -valid["rv_iv_spread_z"]).all()
    assert (valid["far_otm_put_share_oriented_z"] == valid["far_otm_put_share_z"]).all()
    assert (valid["block_count_24h_oriented_z"] == valid["block_count_24h_z"]).all()


# ── combine_forecast ─────────────────────────────────────────────────────────

def _row(**kwargs):
    base = {f"{c}_oriented_z": np.nan for c in SIGNAL_COLUMNS}
    base.update(kwargs)
    return base


def test_combine_forecast_equal_weight_average():
    panel = pd.DataFrame([
        _row(rv_iv_spread_oriented_z=1.0, far_otm_put_share_oriented_z=2.0, block_count_24h_oriented_z=3.0),
    ])
    out = combine_forecast(panel)
    assert out["n_signals_available"].iloc[0] == 3
    assert out["combined_forecast_z"].iloc[0] == pytest.approx(2.0)  # (1+2+3)/3, poids EGAUX


def test_combine_forecast_direction_thresholds():
    panel = pd.DataFrame([
        _row(rv_iv_spread_oriented_z=DIRECTION_Z_THRESHOLD + 0.1),
        _row(rv_iv_spread_oriented_z=-(DIRECTION_Z_THRESHOLD + 0.1)),
        _row(rv_iv_spread_oriented_z=0.0),
    ])
    out = combine_forecast(panel)
    assert list(out["forecast_direction"]) == ["RV_UP", "RV_DOWN", "NEUTRAL"]


def test_combine_forecast_confidence_full_agreement():
    """Les 3 signaux d'accord (même signe) -> agreement=1.0, confidence =
    min(avg_ref_ic/anchor, 1.0)."""
    panel = pd.DataFrame([
        _row(rv_iv_spread_oriented_z=1.0, far_otm_put_share_oriented_z=1.0, block_count_24h_oriented_z=1.0),
    ])
    out = combine_forecast(panel)
    avg_ref_ic = np.mean(list(REFERENCE_ABS_IC.values()))
    expected = min(avg_ref_ic / IC_CONFIDENCE_ANCHOR, 1.0)
    assert out["confidence"].iloc[0] == pytest.approx(expected)


def test_combine_forecast_confidence_zero_when_combined_is_zero():
    panel = pd.DataFrame([
        _row(rv_iv_spread_oriented_z=1.0, far_otm_put_share_oriented_z=-1.0, block_count_24h_oriented_z=0.0),
    ])
    out = combine_forecast(panel)
    assert out["combined_forecast_z"].iloc[0] == pytest.approx(0.0)
    assert out["confidence"].iloc[0] == 0.0


def test_combine_forecast_partial_availability():
    """Un seul signal disponible -> n_signals_available=1, combined_forecast_z
    == ce signal seul (moyenne d'un seul élément)."""
    panel = pd.DataFrame([_row(far_otm_put_share_oriented_z=2.5)])
    out = combine_forecast(panel)
    assert out["n_signals_available"].iloc[0] == 1
    assert out["combined_forecast_z"].iloc[0] == pytest.approx(2.5)


def test_combine_forecast_empty_input():
    out = combine_forecast(pd.DataFrame())
    assert out.empty
    assert "combined_forecast_z" in out.columns


def test_combine_forecast_never_uses_ic_to_weight_direction():
    """Rappel de conception : la combinaison est à POIDS ÉGAUX (pas pondérée
    par IC) -- vérifié en confirmant qu'un signal à IC de référence FAIBLE
    (M17=0.0996) pèse EXACTEMENT pareil qu'un signal à IC FORT (M2=0.1622)
    dans combined_forecast_z."""
    panel_m17_only = pd.DataFrame([_row(block_count_24h_oriented_z=4.0)])
    panel_m2_only = pd.DataFrame([_row(rv_iv_spread_oriented_z=4.0)])
    out_m17 = combine_forecast(panel_m17_only)
    out_m2 = combine_forecast(panel_m2_only)
    assert out_m17["combined_forecast_z"].iloc[0] == out_m2["combined_forecast_z"].iloc[0] == pytest.approx(4.0)


# ── realized_vol ─────────────────────────────────────────────────────────────

def _hourly_df(day_str: str, log_returns: list[float]) -> pd.DataFrame:
    ts = pd.date_range(f"{day_str}T00:00:00Z", periods=len(log_returns), freq="h")
    return pd.DataFrame({"datetime": ts, "log_return_1": log_returns})


def test_compute_daily_realized_vol_formula():
    """sameday_rv(day) = std(log-returns du jour) * sqrt(24*365) * 100 --
    vérifié contre un calcul manuel sur des données synthétiques."""
    rng = np.random.default_rng(7)
    rets = rng.normal(scale=0.005, size=24).tolist()
    df = _hourly_df("2026-01-01", rets)
    out = compute_daily_realized_vol("BTCUSDT", hourly_df=df)
    assert len(out) == 1
    expected = np.std(rets, ddof=1) * ANNUALIZATION_FACTOR * 100.0
    assert out["sameday_rv"].iloc[0] == pytest.approx(expected)


def test_compute_daily_realized_vol_drops_low_coverage_days():
    """Un jour avec moins de MIN_HOURLY_BARS_PER_DAY (12) barres valides est
    exclu -- pas de RV calculée sur une couverture insuffisante."""
    df = _hourly_df("2026-01-01", [0.001] * 5)  # seulement 5 barres
    out = compute_daily_realized_vol("BTCUSDT", hourly_df=df)
    assert out.empty


def test_compute_daily_realized_vol_empty_input():
    out = compute_daily_realized_vol("BTCUSDT", hourly_df=pd.DataFrame(columns=["datetime", "log_return_1"]))
    assert out.empty
    assert list(out.columns) == ["day", "sameday_rv", "n_hourly_bars"]


def test_compute_daily_realized_vol_no_lookahead_across_days():
    """La RV du jour D ne doit dépendre que des barres du jour D -- vérifié
    en tronquant l'historique après D et en confirmant que la RV de D est
    inchangée."""
    rng = np.random.default_rng(3)
    d1 = _hourly_df("2026-01-01", rng.normal(scale=0.004, size=24).tolist())
    d2 = _hourly_df("2026-01-02", rng.normal(scale=0.004, size=24).tolist())
    full = pd.concat([d1, d2], ignore_index=True)

    out_full = compute_daily_realized_vol("BTCUSDT", hourly_df=full)
    out_d1_only = compute_daily_realized_vol("BTCUSDT", hourly_df=d1)

    rv_full_d1 = out_full[out_full["day"] == pd.Timestamp("2026-01-01", tz="UTC")]["sameday_rv"].iloc[0]
    rv_d1_only = out_d1_only["sameday_rv"].iloc[0]
    assert rv_full_d1 == pytest.approx(rv_d1_only)


# ── backfill_actual_realized_rv ───────────────────────────────────────────────

def _decision_row(day: str, actual_realized_rv=None, rv_backfilled_at=None):
    d = pd.Timestamp(day, tz="UTC")
    return {
        "event_time": d,
        "target_period_start": d + pd.Timedelta(days=1),
        "target_period_end": d + pd.Timedelta(days=2),
        "target_realized_at": d + pd.Timedelta(days=2),
        "actual_realized_rv": actual_realized_rv,
        "rv_backfilled_at": rv_backfilled_at,
    }


def test_backfill_not_yet_elapsed_stays_null():
    """Horizon PAS ENCORE écoulé (target_realized_at > now) -> reste NULL,
    même si une RV est disponible pour le jour cible."""
    decisions = pd.DataFrame([_decision_row("2026-06-01")])
    now = pd.Timestamp("2026-06-02T00:00:00Z")  # target_realized_at = 2026-06-03, pas encore atteint
    rv_daily = pd.DataFrame({
        "day": [pd.Timestamp("2026-06-02", tz="UTC")],
        "sameday_rv": [42.0],
    })
    out = backfill_actual_realized_rv(decisions, now=now, rv_daily=rv_daily)
    assert pd.isna(out["actual_realized_rv"].iloc[0])
    assert out["rv_backfilled_at"].iloc[0] is None


def test_backfill_elapsed_gets_filled():
    """Horizon écoulé (target_realized_at <= now) ET RV du jour cible
    disponible -> actual_realized_rv rempli."""
    decisions = pd.DataFrame([_decision_row("2026-06-01")])
    now = pd.Timestamp("2026-06-05T00:00:00Z")  # bien après target_realized_at=2026-06-03
    rv_daily = pd.DataFrame({
        "day": [pd.Timestamp("2026-06-02", tz="UTC")],  # target_period_start
        "sameday_rv": [37.5],
    })
    out = backfill_actual_realized_rv(decisions, now=now, rv_daily=rv_daily)
    assert out["actual_realized_rv"].iloc[0] == pytest.approx(37.5)
    assert out["rv_backfilled_at"].iloc[0] is not None


def test_backfill_elapsed_but_data_not_yet_available_stays_null():
    """Horizon écoulé mais la RV du jour cible n'est pas (encore) dans
    rv_daily (collecte pas rafraîchie jusque-là) -> reste NULL, retenté plus
    tard, PAS de crash ni de valeur inventée."""
    decisions = pd.DataFrame([_decision_row("2026-06-01")])
    now = pd.Timestamp("2026-06-05T00:00:00Z")
    rv_daily = pd.DataFrame(columns=["day", "sameday_rv"])
    out = backfill_actual_realized_rv(decisions, now=now, rv_daily=rv_daily)
    assert pd.isna(out["actual_realized_rv"].iloc[0])


def test_backfill_never_overwrites_already_filled_cell():
    """Idempotence stricte : une ligne déjà backfillée (actual_realized_rv
    non-null) ne doit JAMAIS être réécrite, même si rv_daily contient
    maintenant une valeur DIFFÉRENTE pour ce jour (aucune réécriture
    rétroactive, discipline du ledger)."""
    decisions = pd.DataFrame([_decision_row("2026-06-01", actual_realized_rv=99.9, rv_backfilled_at="2026-06-05T00:00:00+00:00")])
    now = pd.Timestamp("2026-06-10T00:00:00Z")
    rv_daily = pd.DataFrame({
        "day": [pd.Timestamp("2026-06-02", tz="UTC")],
        "sameday_rv": [1.0],   # valeur DIFFÉRENTE -- ne doit jamais écraser 99.9
    })
    out = backfill_actual_realized_rv(decisions, now=now, rv_daily=rv_daily)
    assert out["actual_realized_rv"].iloc[0] == pytest.approx(99.9)


def test_backfill_idempotent_two_runs_identical():
    decisions = pd.DataFrame([
        _decision_row("2026-06-01"),
        _decision_row("2026-06-02"),
    ])
    now = pd.Timestamp("2026-06-10T00:00:00Z")
    rv_daily = pd.DataFrame({
        "day": [pd.Timestamp("2026-06-02", tz="UTC"), pd.Timestamp("2026-06-03", tz="UTC")],
        "sameday_rv": [10.0, 20.0],
    })
    out1 = backfill_actual_realized_rv(decisions, now=now, rv_daily=rv_daily)
    out2 = backfill_actual_realized_rv(out1, now=now, rv_daily=rv_daily)
    assert (out1["actual_realized_rv"].to_numpy() == out2["actual_realized_rv"].to_numpy()).all()


def test_backfill_empty_input():
    out = backfill_actual_realized_rv(pd.DataFrame())
    assert out.empty


def test_backfill_missing_columns_raises():
    with pytest.raises(ValueError):
        backfill_actual_realized_rv(pd.DataFrame([{"foo": 1}]))


# ── universe / registry fail-closed (mirrors test_funding_basis_disagreement.py) ──

def test_universe_hash_deterministic():
    from scripts.run_vol_forecast_layer_shadow import universe_hash
    a = universe_hash(["BTCUSDT"])
    b = universe_hash(["BTCUSDT"])
    assert a == b
    c = universe_hash(["ETHUSDT"])
    assert a != c


def test_load_universe_matches_expected_frozen_list():
    from scripts.run_vol_forecast_layer_shadow import EXPECTED_UNIVERSE, load_universe
    assert load_universe() == sorted(EXPECTED_UNIVERSE)
    assert EXPECTED_UNIVERSE == ["BTCUSDT"]


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_vol_forecast_layer_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID")


def test_check_registry_freeze_passes_for_vol_forecast_layer():
    from scripts.run_vol_forecast_layer_shadow import check_registry_freeze
    check_registry_freeze("VOL_FORECAST_LAYER_V1")


def test_check_registry_freeze_fails_closed_for_merged_options_entries():
    """Les 3 alphas fusionnés (operational_status=MERGED_INTO_VOL_FORECAST_LAYER_V1)
    ne doivent JAMAIS pouvoir écrire de décisions sous leur propre alpha_id."""
    from scripts.run_vol_forecast_layer_shadow import check_registry_freeze
    for alpha_id in ("OPTIONS_RV_IV_SPREAD_V1", "OPTIONS_FAR_OTM_PUT_SHARE_V1", "OPTIONS_BLOCK_FLOW_TO_RV_V1"):
        with pytest.raises(RuntimeError, match="operational_status="):
            check_registry_freeze(alpha_id)
