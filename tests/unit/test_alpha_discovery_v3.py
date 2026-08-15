import numpy as np
import pandas as pd

from research.alpha_discovery_v3.pipeline import (
    DERIVED_FEATURES,
    build_forward_target,
    causal_candidate_mask,
    enrich_symbol_frame,
    evaluate_selected,
    fit_predict_histgb,
    make_year_fold,
    trailing_zscore,
)


def _frame(n=700):
    ts = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    x = np.linspace(100, 120, n)
    return pd.DataFrame({
        "timestamp": ts, "open": x, "residual_logret_5m": 0.001,
        "residual_return_15m": 0.003, "residual_return_1h": 0.012,
        "residual_std_30d": 0.02, "oi": 1000 + np.arange(n), "oi_delta_pct_1h": 0.01,
        "aggressive_buy_usd": 100.0, "aggressive_sell_usd": 80.0, "signed_volume": 20.0,
        "CVD": np.arange(n) * 20.0, "funding_rate": 0.0001,
        "funding_rate_percentile_90d": 0.7, "basis": 0.001, "basis_z_1d": 1.0,
        "basis_z_7d": 0.5, "volume": 1000.0, "large_trade_buy_usd": 30.0,
        "large_trade_sell_usd": 10.0, "trade_count": 100.0, "avg_trade_size_usd": 50.0,
        "p95_trade_size_usd": 200.0, "buy_vwap": 100.1, "sell_vwap": 99.9,
        "sum_open_interest_value": 100000.0, "count_toptrader_long_short_ratio": 1.1,
        "sum_toptrader_long_short_ratio": 1.2, "count_long_short_ratio": 1.0,
        "sum_taker_long_short_vol_ratio": 1.05,
    })


def test_forward_target_excludes_current_bar():
    s = pd.Series([0.10, 0.01, 0.02, 0.03])
    target, complete = build_forward_target(s, 2)
    assert np.isclose(target.iloc[0], np.expm1(0.03))
    assert complete.iloc[0]


def test_trailing_zscore_is_strictly_prior():
    s = pd.Series([1.0, 1.0, 2.0, 10.0])
    z = trailing_zscore(s, 3)
    s2 = s.copy()
    s2.iloc[3] = 20.0
    z2 = trailing_zscore(s2, 3)
    assert np.isfinite(z.iloc[3])
    assert z2.iloc[3] > z.iloc[3]


def test_enrichment_and_entry_shift():
    df = enrich_symbol_frame(_frame())
    for col in DERIVED_FEATURES:
        assert col in df.columns
    assert df["entry_price"].iloc[0] == df["open"].iloc[1]
    assert df["target_path_complete_1h"].iloc[0]


def test_candidate_mask_uses_threshold_crossing_not_future_peak():
    df = enrich_symbol_frame(_frame())
    df["stress_score"] = 0.0
    df.loc[25:27, "stress_score"] = 3.0
    mask = causal_candidate_mask(df)
    assert mask.iloc[25]
    assert not mask.iloc[26]


def test_year_fold_is_chronological_and_embargoed():
    ts = pd.Series(pd.date_range("2022-01-01", "2024-12-31", freq="1D", tz="UTC"))
    fold = make_year_fold(ts, 2024)
    assert ts[fold.fit_mask].max() < ts[fold.calib_mask].min()
    assert ts[fold.calib_mask].max() < pd.Timestamp("2024-01-01", tz="UTC")
    assert ts[fold.test_mask].min() == pd.Timestamp("2024-01-01", tz="UTC")


def test_selected_return_metrics_are_directional_and_costed():
    pred = np.array([1.0, -1.0, 0.1])
    target = np.array([0.02, -0.03, 0.5])
    c1 = np.array([0.001, 0.001, 0.001])
    result = evaluate_selected(pred, target, c1, 2 * c1, 0.5)
    assert result["n"] == 2
    assert result["gross_mean"] > 0
    assert result["net_x2_mean"] > 0


def test_histgb_walkforward_recovers_known_oos_signal():
    rng = np.random.default_rng(7)
    ts = pd.date_range("2020-01-01", "2024-12-31 23:00", freq="1h", tz="UTC")
    n = len(ts)
    signal = rng.normal(size=n)
    df = pd.DataFrame({
        "timestamp": ts,
        "f": signal,
        "target_residual_ret_1h": 0.002 * signal + rng.normal(scale=0.001, size=n),
        "cost_x1": 0.0002,
        "cost_x2": 0.0004,
    })
    fold = make_year_fold(df["timestamp"], 2024)
    result = fit_predict_histgb(
        df, ["f"], fold, max_train_rows=20000, max_calib_rows=4000, max_test_rows=9000,
    )
    assert result["status"] == "OK"
    assert result["ic_spearman"] > 0.5
    assert result["net_x2_mean"] > 0
