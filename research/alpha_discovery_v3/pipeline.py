from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

BARS_PER_HOUR = 12
BARS_PER_DAY = 288

V1_FEATURES = [
    "residual_return_15m", "residual_return_1h", "residual_std_30d", "oi_delta_pct_1h",
    "aggressive_buy_usd", "aggressive_sell_usd", "signed_volume", "CVD", "funding_rate",
    "funding_rate_percentile_90d", "basis", "basis_z_1d", "basis_z_7d", "volume",
]
RAW_EXTRA_FEATURES = [
    "sum_open_interest_value", "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
    "count_long_short_ratio", "sum_taker_long_short_vol_ratio", "trade_count", "large_trade_buy_usd",
    "large_trade_sell_usd", "avg_trade_size_usd", "p95_trade_size_usd", "buy_vwap", "sell_vwap",
]
DERIVED_FEATURES = [
    "residual_z_1h", "residual_accel_1h", "realized_vol_1h", "realized_vol_4h",
    "oi_delta_pct_5m", "oi_delta_pct_15m", "oi_delta_pct_4h", "oi_accel_1h", "oi_delta_z_1h",
    "flow_imbalance", "flow_imbalance_z", "flow_accel_1h", "cvd_velocity_1h", "cvd_accel_1h",
    "large_trade_imbalance", "large_trade_share", "large_trade_imbalance_z", "trade_count_z",
    "trade_size_tail_ratio", "vwap_spread_bps", "basis_delta_1h", "basis_curve", "funding_centered_rank",
    "crowding_interaction", "oi_flow_interaction", "residual_flow_interaction", "stress_score",
]
FEATURE_GROUPS = {
    "A_V1": V1_FEATURES,
    "B_RAW": V1_FEATURES + RAW_EXTRA_FEATURES,
    "C_DERIVED": V1_FEATURES + RAW_EXTRA_FEATURES + DERIVED_FEATURES,
}


def trailing_zscore(s: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """Strict-prior z-score: current value never contributes to its own baseline."""
    mp = window if min_periods is None else min_periods
    hist = s.shift(1)
    mu = hist.rolling(window, min_periods=mp).mean()
    sd = hist.rolling(window, min_periods=mp).std(ddof=0)
    return (s - mu) / sd.replace(0, np.nan)


def _pct_change_no_fill(s: pd.Series, bars: int) -> pd.Series:
    return s.pct_change(bars, fill_method=None)


def build_forward_target(logret_5m: pd.Series, horizon_bars: int = BARS_PER_HOUR) -> tuple[pd.Series, pd.Series]:
    """Forward residual return from the NEXT bar onward; row t's own increment is excluded."""
    parts = [logret_5m.shift(-k) for k in range(1, horizon_bars + 1)]
    mat = pd.concat(parts, axis=1)
    complete = mat.notna().all(axis=1)
    summed = mat.sum(axis=1, min_count=horizon_bars)
    target = pd.Series(np.expm1(summed), index=logret_5m.index).where(complete)
    return target, complete


def enrich_symbol_frame(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.sort_values("timestamp").reset_index(drop=True).copy()
    needed = set(V1_FEATURES + RAW_EXTRA_FEATURES + ["oi", "residual_logret_5m", "open"])
    for c in needed:
        if c not in df:
            df[c] = np.nan

    df["residual_z_1h"] = df["residual_return_1h"] / df["residual_std_30d"].replace(0, np.nan)
    df["residual_accel_1h"] = df["residual_return_1h"] - df["residual_return_1h"].shift(BARS_PER_HOUR)
    df["realized_vol_1h"] = df["residual_logret_5m"].shift(1).rolling(BARS_PER_HOUR, min_periods=BARS_PER_HOUR).std(ddof=0)
    df["realized_vol_4h"] = df["residual_logret_5m"].shift(1).rolling(4 * BARS_PER_HOUR, min_periods=4 * BARS_PER_HOUR).std(ddof=0)

    df["oi_delta_pct_5m"] = _pct_change_no_fill(df["oi"], 1)
    df["oi_delta_pct_15m"] = _pct_change_no_fill(df["oi"], 3)
    df["oi_delta_pct_4h"] = _pct_change_no_fill(df["oi"], 48)
    df["oi_accel_1h"] = df["oi_delta_pct_1h"] - df["oi_delta_pct_1h"].shift(BARS_PER_HOUR)
    df["oi_delta_z_1h"] = trailing_zscore(df["oi_delta_pct_1h"], BARS_PER_DAY)

    total_flow = df["aggressive_buy_usd"] + df["aggressive_sell_usd"]
    df["flow_imbalance"] = df["signed_volume"] / total_flow.replace(0, np.nan)
    df["flow_imbalance_z"] = trailing_zscore(df["flow_imbalance"], BARS_PER_DAY)
    df["flow_accel_1h"] = df["flow_imbalance"] - df["flow_imbalance"].shift(BARS_PER_HOUR)
    cvd_delta = df["CVD"].diff()
    df["cvd_velocity_1h"] = cvd_delta.rolling(BARS_PER_HOUR, min_periods=BARS_PER_HOUR).sum()
    df["cvd_accel_1h"] = df["cvd_velocity_1h"] - df["cvd_velocity_1h"].shift(BARS_PER_HOUR)

    large_total = df["large_trade_buy_usd"] + df["large_trade_sell_usd"]
    df["large_trade_imbalance"] = (df["large_trade_buy_usd"] - df["large_trade_sell_usd"]) / large_total.replace(0, np.nan)
    df["large_trade_share"] = large_total / total_flow.replace(0, np.nan)
    df["large_trade_imbalance_z"] = trailing_zscore(df["large_trade_imbalance"], BARS_PER_DAY)
    df["trade_count_z"] = trailing_zscore(np.log1p(df["trade_count"].clip(lower=0)), BARS_PER_DAY)
    df["trade_size_tail_ratio"] = df["p95_trade_size_usd"] / df["avg_trade_size_usd"].replace(0, np.nan)
    mid_vwap = (df["buy_vwap"] + df["sell_vwap"]) / 2
    df["vwap_spread_bps"] = 1e4 * (df["buy_vwap"] - df["sell_vwap"]) / mid_vwap.replace(0, np.nan)

    df["basis_delta_1h"] = df["basis"] - df["basis"].shift(BARS_PER_HOUR)
    df["basis_curve"] = df["basis_z_1d"] - df["basis_z_7d"]
    df["funding_centered_rank"] = df["funding_rate_percentile_90d"] - 0.5
    df["crowding_interaction"] = df["funding_centered_rank"] * df["basis_z_1d"] * df["oi_delta_pct_1h"]
    df["oi_flow_interaction"] = df["oi_delta_z_1h"] * df["flow_imbalance_z"]
    df["residual_flow_interaction"] = df["residual_z_1h"] * df["flow_imbalance_z"]

    stress_components = pd.concat([
        df["residual_z_1h"].abs(), df["oi_delta_z_1h"].abs(), df["flow_imbalance_z"].abs(),
        df["basis_z_1d"].abs(), df["large_trade_imbalance_z"].abs(),
    ], axis=1)
    df["stress_score"] = stress_components.max(axis=1, skipna=True)

    target, complete = build_forward_target(df["residual_logret_5m"])
    df["target_residual_ret_1h"] = target
    df["target_path_complete_1h"] = complete
    df["entry_price"] = df["open"].shift(-1)
    return df


def causal_candidate_mask(df: pd.DataFrame, stress_threshold: float = 2.0, background_hours: int = 4) -> pd.Series:
    """Fixed-cadence background plus first causal stress-threshold crossing; no future local maxima."""
    ts = pd.to_datetime(df["timestamp"], utc=True)
    background = (ts.dt.minute == 0) & (ts.dt.second == 0) & ((ts.dt.hour % background_hours) == 0)
    stress = df["stress_score"] >= stress_threshold
    crossing = stress & ~stress.shift(1, fill_value=False)
    return (background | crossing) & df["target_path_complete_1h"].fillna(False)


def profit_factor(r: pd.Series) -> float:
    x = pd.Series(r).dropna()
    wins = x[x > 0].sum()
    losses = -x[x < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / losses)


def evaluate_selected(pred: np.ndarray, target: np.ndarray, cost_x1: np.ndarray, cost_x2: np.ndarray, threshold: float) -> dict:
    pred = np.asarray(pred, dtype=float)
    target = np.asarray(target, dtype=float)
    c1 = np.asarray(cost_x1, dtype=float)
    c2 = np.asarray(cost_x2, dtype=float)
    valid = np.isfinite(pred) & np.isfinite(target)
    selected = valid & (np.abs(pred) >= threshold)
    if not selected.any():
        return {"n": 0, "gross_mean": np.nan, "net_x1_mean": np.nan, "net_x2_mean": np.nan,
                "pf_x1": np.nan, "pf_x2": np.nan, "cost_coverage": 0.0}
    direction = np.sign(pred[selected])
    gross = direction * target[selected]
    c1s, c2s = c1[selected], c2[selected]
    coverage = float(np.isfinite(c1s).mean())
    net1 = gross - c1s
    net2 = gross - c2s
    return {
        "n": int(selected.sum()), "gross_mean": float(np.nanmean(gross)),
        "net_x1_mean": float(np.nanmean(net1)), "net_x2_mean": float(np.nanmean(net2)),
        "pf_x1": profit_factor(pd.Series(net1)), "pf_x2": profit_factor(pd.Series(net2)),
        "win_rate_gross": float(np.nanmean(gross > 0)), "cost_coverage": coverage,
    }


@dataclass(frozen=True)
class Fold:
    test_year: int
    fit_mask: np.ndarray
    calib_mask: np.ndarray
    test_mask: np.ndarray


def make_year_fold(timestamps: pd.Series, test_year: int, calibration_months: int = 6, embargo_hours: int = 8) -> Fold:
    ts = pd.to_datetime(timestamps, utc=True)
    test_start = pd.Timestamp(f"{test_year}-01-01", tz="UTC")
    test_end = pd.Timestamp(f"{test_year + 1}-01-01", tz="UTC")
    calib_start = test_start - pd.DateOffset(months=calibration_months)
    embargo_start = test_start - pd.Timedelta(hours=embargo_hours)
    fit = ts < calib_start
    calib = (ts >= calib_start) & (ts < embargo_start)
    test = (ts >= test_start) & (ts < test_end)
    return Fold(test_year, fit.to_numpy(), calib.to_numpy(), test.to_numpy())


def feature_columns(group: str, available: Iterable[str]) -> list[str]:
    av = set(available)
    return [c for c in FEATURE_GROUPS[group] if c in av]


def deterministic_cap(df: pd.DataFrame, max_rows: int, seed: int) -> pd.DataFrame:
    if len(df) <= max_rows:
        return df
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(df), size=max_rows, replace=False))
    return df.iloc[idx].reset_index(drop=True)


def spearman_ic(pred: np.ndarray, target: np.ndarray) -> float:
    p, y = pd.Series(pred), pd.Series(target)
    valid = p.notna() & y.notna()
    if valid.sum() < 3:
        return float("nan")
    return float(p[valid].rank(method="average").corr(y[valid].rank(method="average")))


def fit_predict_histgb(df: pd.DataFrame, features: list[str], fold: Fold, *, max_train_rows: int = 500_000,
                       max_calib_rows: int = 150_000, max_test_rows: int = 250_000,
                       selection_quantile: float = 0.95, random_state: int = 17) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor

    base_cols = features + ["target_residual_ret_1h", "cost_x1", "cost_x2"]
    fit_df = df.loc[fold.fit_mask, base_cols].copy()
    calib_df = df.loc[fold.calib_mask, base_cols].copy()
    test_df = df.loc[fold.test_mask, base_cols].copy()
    for part in (fit_df, calib_df, test_df):
        part.replace([np.inf, -np.inf], np.nan, inplace=True)
        part.dropna(subset=["target_residual_ret_1h"], inplace=True)
    if len(fit_df) < 1000 or len(calib_df) < 200 or len(test_df) < 200:
        return {"test_year": fold.test_year, "status": "INSUFFICIENT_ROWS",
                "n_fit": len(fit_df), "n_calib": len(calib_df), "n_test": len(test_df)}

    used = [c for c in features if fit_df[c].notna().mean() >= 0.05]
    if not used:
        return {"test_year": fold.test_year, "status": "NO_USABLE_FEATURES"}

    fit_df = deterministic_cap(fit_df, max_train_rows, random_state + fold.test_year)
    calib_df = deterministic_cap(calib_df, max_calib_rows, random_state + 100 + fold.test_year)
    test_df = deterministic_cap(test_df, max_test_rows, random_state + 200 + fold.test_year)

    model = HistGradientBoostingRegressor(loss="squared_error", learning_rate=0.05, max_iter=120,
                                          max_leaf_nodes=15, min_samples_leaf=50,
                                          l2_regularization=1.0, random_state=random_state)
    model.fit(fit_df[used], fit_df["target_residual_ret_1h"])
    calib_pred = model.predict(calib_df[used])
    threshold = float(np.nanquantile(np.abs(calib_pred), selection_quantile))
    test_pred = model.predict(test_df[used])
    metrics = evaluate_selected(test_pred, test_df["target_residual_ret_1h"].to_numpy(),
                                test_df["cost_x1"].to_numpy(), test_df["cost_x2"].to_numpy(), threshold)
    sel = np.abs(test_pred) >= threshold
    gross = np.sign(test_pred[sel]) * test_df["target_residual_ret_1h"].to_numpy()[sel]
    metrics["stress_30bp_mean"] = float(np.mean(gross - 0.003)) if len(gross) else np.nan
    metrics["stress_60bp_mean"] = float(np.mean(gross - 0.006)) if len(gross) else np.nan
    metrics.update({"test_year": fold.test_year, "status": "OK", "threshold": threshold,
                    "ic_spearman": spearman_ic(test_pred, test_df["target_residual_ret_1h"].to_numpy()),
                    "n_fit": len(fit_df), "n_calib": len(calib_df), "n_test": len(test_df),
                    "used_features": used})
    return metrics


def summarize_folds(folds: list[dict]) -> dict:
    ok = [f for f in folds if f.get("status") == "OK"]
    if not ok:
        return {"status": "NO_VALID_FOLDS"}

    def med(k: str) -> float:
        return float(np.nanmedian([f.get(k, np.nan) for f in ok]))

    total_n = int(sum(f.get("n", 0) for f in ok))
    weighted = {}
    for k in ["gross_mean", "net_x1_mean", "net_x2_mean", "stress_30bp_mean", "stress_60bp_mean"]:
        pairs = [(f.get(k, np.nan), f.get("n", 0)) for f in ok]
        pairs = [(v, n) for v, n in pairs if np.isfinite(v) and n > 0]
        weighted[k] = float(sum(v * n for v, n in pairs) / sum(n for _, n in pairs)) if pairs else np.nan
    return {
        "status": "OK", "folds_ok": len(ok), "selected_n": total_n,
        "median_ic_spearman": med("ic_spearman"), "median_pf_x1": med("pf_x1"),
        "median_pf_x2": med("pf_x2"), "median_cost_coverage": med("cost_coverage"),
        **{f"pooled_{k}": v for k, v in weighted.items()},
        "positive_net_x2_years": int(sum(f.get("net_x2_mean", np.nan) > 0 for f in ok)),
        "positive_stress_60bp_years": int(sum(f.get("stress_60bp_mean", np.nan) > 0 for f in ok)),
    }
