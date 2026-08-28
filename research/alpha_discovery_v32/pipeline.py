from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

DEV_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

MODEL_PARAMS = {
    "direction": {
        "learning_rate": 0.04,
        "max_iter": 140,
        "max_leaf_nodes": 15,
        "min_samples_leaf": 80,
        "l2_regularization": 2.0,
        "random_state": 41,
    },
    "magnitude": {
        "learning_rate": 0.04,
        "max_iter": 140,
        "max_leaf_nodes": 15,
        "min_samples_leaf": 80,
        "l2_regularization": 2.0,
        "random_state": 43,
    },
}

DEFAULT_SELECTION_QUANTILE = 0.90
DEFAULT_FIT_MONTHS = 24
DEFAULT_CALIB_DAYS = 120
DEFAULT_EMBARGO_HOURS = 8
MIN_MODEL_ROWS = 1000
MIN_CLASS_ROWS = 200
MIN_CAL_HALF_ROWS = 200
MIN_CAL_SELECTED = 40


@dataclass(frozen=True)
class MonthFold:
    test_month: str
    fit_mask: np.ndarray
    calib_fit_mask: np.ndarray
    calib_select_mask: np.ndarray
    test_mask: np.ndarray
    fit_start: pd.Timestamp
    calib_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_month_fold(
    timestamps: pd.Series,
    test_month: str,
    fit_months: int = DEFAULT_FIT_MONTHS,
    calib_days: int = DEFAULT_CALIB_DAYS,
    embargo_hours: int = DEFAULT_EMBARGO_HOURS,
) -> MonthFold:
    ts = pd.to_datetime(timestamps, utc=True)
    test_start = pd.Timestamp(test_month + "-01", tz="UTC")
    test_end = test_start + pd.offsets.MonthBegin(1)
    embargo_start = test_start - pd.Timedelta(hours=embargo_hours)
    calib_start = test_start - pd.Timedelta(days=calib_days)
    fit_start = calib_start - pd.DateOffset(months=fit_months)
    calib_mid = calib_start + (embargo_start - calib_start) / 2

    fit = (ts >= fit_start) & (ts < calib_start)
    calib_fit = (ts >= calib_start) & (ts < calib_mid)
    calib_select = (ts >= calib_mid) & (ts < embargo_start)
    test = (ts >= test_start) & (ts < test_end)
    return MonthFold(
        test_month=test_month,
        fit_mask=fit.to_numpy(),
        calib_fit_mask=calib_fit.to_numpy(),
        calib_select_mask=calib_select.to_numpy(),
        test_mask=test.to_numpy(),
        fit_start=fit_start,
        calib_start=calib_start,
        test_start=test_start,
        test_end=test_end,
    )


def month_sequence(start: str = "2023-01", end: str = "2026-07") -> List[str]:
    p = pd.period_range(start=start, end=end, freq="M")
    return [str(x) for x in p]


def profit_factor(values: np.ndarray) -> float:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan")
    wins = x[x > 0].sum()
    losses = -x[x < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / losses)


def spearman_ic(pred: np.ndarray, target: np.ndarray) -> float:
    p = pd.Series(np.asarray(pred, dtype=float))
    y = pd.Series(np.asarray(target, dtype=float))
    valid = p.notna() & y.notna()
    if int(valid.sum()) < 3:
        return float("nan")
    return float(p[valid].rank().corr(y[valid].rank()))


def balanced_cap(df: pd.DataFrame, max_rows: int, seed: int) -> pd.DataFrame:
    if len(df) <= max_rows:
        return df.copy()
    work = df.copy()
    work["__year"] = pd.to_datetime(work["timestamp"], utc=True).dt.year
    groups = list(work.groupby(["symbol", "__year"], sort=True))
    quota = max(1, max_rows // max(1, len(groups)))
    rng = np.random.default_rng(seed)
    parts = []
    for _, group in groups:
        if len(group) <= quota:
            parts.append(group)
        else:
            idx = np.sort(rng.choice(len(group), size=quota, replace=False))
            parts.append(group.iloc[idx])
    out = pd.concat(parts, ignore_index=True)
    out = out.drop(columns="__year").sort_values("timestamp").reset_index(drop=True)
    if len(out) > max_rows:
        idx = np.linspace(0, len(out) - 1, max_rows, dtype=int)
        out = out.iloc[idx].reset_index(drop=True)
    return out


def _safe_feature_list(df: pd.DataFrame, features: Sequence[str], min_coverage: float = 0.10) -> List[str]:
    if not df.columns.is_unique:
        dup = df.columns[df.columns.duplicated()].unique().tolist()
        raise ValueError("duplicate columns are forbidden: %s" % dup)
    return [c for c in features if c in df.columns and float(df[c].notna().mean()) >= min_coverage]


def _prepare_parts(
    df: pd.DataFrame,
    features: Sequence[str],
    fold: MonthFold,
    max_train_rows: int,
    max_calib_rows: int,
    max_test_rows: int,
    seed: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, List[str]]:
    base = list(dict.fromkeys(list(features) + [
        "timestamp", "symbol", "target_residual_ret_1h", "target_standardized_1h",
        "ex_ante_sigma_1h", "decision_cost_x1", "realized_cost_x1", "realized_cost_x2",
    ]))
    missing = [c for c in base if c not in df.columns]
    if missing:
        raise ValueError("V3.2 missing required columns: %s" % missing)
    parts = []
    for mask in (fold.fit_mask, fold.calib_fit_mask, fold.calib_select_mask, fold.test_mask):
        p = df.loc[mask, base].copy()
        p = p.replace([np.inf, -np.inf], np.nan)
        p = p.dropna(subset=["target_residual_ret_1h", "target_standardized_1h", "ex_ante_sigma_1h"])
        parts.append(p)
    fit, cal_fit, cal_select, test = parts
    used = _safe_feature_list(fit, features)
    fit = balanced_cap(fit, max_train_rows, seed + 1)
    cal_fit = balanced_cap(cal_fit, max_calib_rows, seed + 2)
    cal_select = balanced_cap(cal_select, max_calib_rows, seed + 3)
    test = balanced_cap(test, max_test_rows, seed + 4)
    return fit, cal_fit, cal_select, test, used


class DirectionMagnitudeModel:
    def __init__(self, features: Sequence[str]):
        self.features = list(features)
        self.direction_model = None
        self.mag_up_model = None
        self.mag_down_model = None
        self.dir_calibrator = None
        self.mag_up_calibrator = None
        self.mag_down_calibrator = None

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(p, dtype=float), 1e-5, 1 - 1e-5)
        return np.log(p / (1 - p))

    def fit_models(self, fit: pd.DataFrame) -> None:
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

        x = fit[self.features]
        target = fit["target_residual_ret_1h"].to_numpy(dtype=float)
        sigma = fit["ex_ante_sigma_1h"].to_numpy(dtype=float)
        y_dir = (target > 0).astype(int)
        if len(fit) < MIN_MODEL_ROWS or np.unique(y_dir).size < 2:
            raise ValueError("insufficient directional training data")

        self.direction_model = HistGradientBoostingClassifier(**MODEL_PARAMS["direction"])
        self.direction_model.fit(x, y_dir)

        std_mag = np.abs(target) / np.where(sigma > 0, sigma, np.nan)
        up = target > 0
        down = target < 0
        if int(up.sum()) < MIN_CLASS_ROWS or int(down.sum()) < MIN_CLASS_ROWS:
            raise ValueError("insufficient positive/negative rows for conditional magnitude models")

        self.mag_up_model = HistGradientBoostingRegressor(**MODEL_PARAMS["magnitude"])
        self.mag_down_model = HistGradientBoostingRegressor(**MODEL_PARAMS["magnitude"])
        self.mag_up_model.fit(x.loc[up], std_mag[up])
        self.mag_down_model.fit(x.loc[down], std_mag[down])

    def _raw_components(self, frame: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        x = frame[self.features]
        sigma = frame["ex_ante_sigma_1h"].to_numpy(dtype=float)
        p_up = self.direction_model.predict_proba(x)[:, 1]
        mag_up = np.maximum(0.0, self.mag_up_model.predict(x)) * sigma
        mag_down = np.maximum(0.0, self.mag_down_model.predict(x)) * sigma
        return p_up, mag_up, mag_down

    def fit_calibration(self, cal_fit: pd.DataFrame) -> Dict[str, float]:
        from sklearn.isotonic import IsotonicRegression
        from sklearn.linear_model import LogisticRegression

        if len(cal_fit) < MIN_CAL_HALF_ROWS:
            raise ValueError("insufficient calibration-fit rows")
        p_up, mag_up, mag_down = self._raw_components(cal_fit)
        target = cal_fit["target_residual_ret_1h"].to_numpy(dtype=float)
        y = (target > 0).astype(int)
        if np.unique(y).size < 2:
            raise ValueError("direction calibration contains one class")

        self.dir_calibrator = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500)
        self.dir_calibrator.fit(self._logit(p_up).reshape(-1, 1), y)

        up = target > 0
        down = target < 0
        self.mag_up_calibrator = IsotonicRegression(y_min=0.0, increasing=True, out_of_bounds="clip")
        self.mag_down_calibrator = IsotonicRegression(y_min=0.0, increasing=True, out_of_bounds="clip")
        if int(up.sum()) < 50 or int(down.sum()) < 50:
            raise ValueError("insufficient sign-specific calibration rows")
        self.mag_up_calibrator.fit(mag_up[up], target[up])
        self.mag_down_calibrator.fit(mag_down[down], -target[down])

        p_cal = self.dir_calibrator.predict_proba(self._logit(p_up).reshape(-1, 1))[:, 1]
        brier_raw = float(np.mean((p_up - y) ** 2))
        brier_cal = float(np.mean((p_cal - y) ** 2))
        return {"brier_raw": brier_raw, "brier_calibrated": brier_cal}

    def predict_expected_return(self, frame: pd.DataFrame) -> Dict[str, np.ndarray]:
        p_up, mag_up, mag_down = self._raw_components(frame)
        p_cal = self.dir_calibrator.predict_proba(self._logit(p_up).reshape(-1, 1))[:, 1]
        mu = np.maximum(0.0, self.mag_up_calibrator.predict(mag_up))
        md = np.maximum(0.0, self.mag_down_calibrator.predict(mag_down))
        expected = p_cal * mu - (1.0 - p_cal) * md
        return {
            "p_up_raw": p_up,
            "p_up_cal": p_cal,
            "mag_up_cal": mu,
            "mag_down_cal": md,
            "expected_return": expected,
        }


def _calibration_selector(
    expected: np.ndarray,
    costs: np.ndarray,
    target: np.ndarray,
    q: float,
) -> Dict[str, float]:
    exp_abs = np.abs(np.asarray(expected, dtype=float))
    c = np.asarray(costs, dtype=float)
    y = np.asarray(target, dtype=float)
    edge = exp_abs - c
    finite = np.isfinite(edge) & np.isfinite(y) & np.isfinite(c)
    positive = edge[finite & (edge > 0)]
    if positive.size < MIN_CAL_SELECTED:
        return {"enabled": 0.0, "threshold": np.nan, "n": 0, "net_x1_mean": np.nan, "pf_x1": np.nan}
    threshold = float(np.quantile(positive, q))
    sel = finite & (edge >= threshold) & (edge > 0)
    direction = np.sign(expected[sel])
    gross = direction * y[sel]
    net = gross - c[sel]
    enabled = len(net) >= MIN_CAL_SELECTED and float(np.mean(net)) > 0 and profit_factor(net) > 1.0
    return {
        "enabled": float(bool(enabled)),
        "threshold": threshold,
        "n": int(len(net)),
        "net_x1_mean": float(np.mean(net)) if len(net) else np.nan,
        "pf_x1": profit_factor(net),
        "predicted_edge_mean": float(np.mean(edge[sel])) if len(net) else np.nan,
        "realized_gross_mean": float(np.mean(gross)) if len(net) else np.nan,
    }


def fit_month(
    df: pd.DataFrame,
    features: Sequence[str],
    fold: MonthFold,
    selection_quantile: float = DEFAULT_SELECTION_QUANTILE,
    max_train_rows: int = 500_000,
    max_calib_rows: int = 150_000,
    max_test_rows: int = 250_000,
    seed: int = 41,
) -> Dict[str, object]:
    try:
        fit, cal_fit, cal_select, test, used = _prepare_parts(
            df, features, fold, max_train_rows, max_calib_rows, max_test_rows, seed
        )
    except ValueError as exc:
        return {"test_month": fold.test_month, "status": "SCHEMA_ERROR", "error": str(exc)}

    if len(used) == 0:
        return {"test_month": fold.test_month, "status": "NO_USABLE_FEATURES"}
    if min(len(fit), len(cal_fit), len(cal_select), len(test)) < MIN_CAL_HALF_ROWS:
        return {
            "test_month": fold.test_month,
            "status": "INSUFFICIENT_ROWS",
            "n_fit": len(fit), "n_calib_fit": len(cal_fit),
            "n_calib_select": len(cal_select), "n_test": len(test),
        }

    model = DirectionMagnitudeModel(used)
    try:
        model.fit_models(fit)
        calibration = model.fit_calibration(cal_fit)
    except ValueError as exc:
        return {"test_month": fold.test_month, "status": "MODEL_INSUFFICIENT", "error": str(exc)}

    cal_pred = model.predict_expected_return(cal_select)
    selector = _calibration_selector(
        cal_pred["expected_return"],
        cal_select["decision_cost_x1"].to_numpy(dtype=float),
        cal_select["target_residual_ret_1h"].to_numpy(dtype=float),
        selection_quantile,
    )

    test_pred = model.predict_expected_return(test)
    expected = test_pred["expected_return"]
    target = test["target_residual_ret_1h"].to_numpy(dtype=float)
    c1 = test["realized_cost_x1"].to_numpy(dtype=float)
    c2 = test["realized_cost_x2"].to_numpy(dtype=float)
    decision_c1 = test["decision_cost_x1"].to_numpy(dtype=float)
    exp_edge = np.abs(expected) - decision_c1

    if not bool(selector["enabled"]):
        selected = np.zeros(len(test), dtype=bool)
    else:
        selected = (
            np.isfinite(exp_edge) & np.isfinite(target) & np.isfinite(c1) & np.isfinite(c2)
            & (exp_edge > 0) & (exp_edge >= float(selector["threshold"]))
        )

    direction = np.sign(expected[selected])
    gross = direction * target[selected]
    net1 = gross - c1[selected]
    net2 = gross - c2[selected]
    shares = test.loc[selected, "symbol"].value_counts(normalize=True)

    pred_abs = np.abs(expected[selected])
    calibration_ratio = (
        float(np.mean(np.abs(gross)) / np.mean(pred_abs))
        if len(gross) and float(np.mean(pred_abs)) > 0 else np.nan
    )
    return {
        "test_month": fold.test_month,
        "status": "OK",
        "enabled_by_calibration": bool(selector["enabled"]),
        "n": int(selected.sum()),
        "selection_rate": float(selected.mean()) if len(selected) else 0.0,
        "gross_mean": float(np.mean(gross)) if len(gross) else np.nan,
        "net_x1_mean": float(np.mean(net1)) if len(net1) else np.nan,
        "net_x2_mean": float(np.mean(net2)) if len(net2) else np.nan,
        "pf_x1": profit_factor(net1),
        "pf_x2": profit_factor(net2),
        "ic_spearman": spearman_ic(expected, target),
        "brier_raw": calibration["brier_raw"],
        "brier_calibrated": calibration["brier_calibrated"],
        "selector_threshold_bps": float(selector["threshold"] * 1e4) if np.isfinite(selector["threshold"]) else np.nan,
        "calibration_selected_n": int(selector["n"]),
        "calibration_net_x1_mean": selector["net_x1_mean"],
        "calibration_pf_x1": selector["pf_x1"],
        "predicted_edge_mean_bps": float(np.mean(exp_edge[selected]) * 1e4) if selected.any() else np.nan,
        "calibration_ratio": calibration_ratio,
        "max_symbol_share": float(shares.max()) if len(shares) else np.nan,
        "symbol_hhi": float((shares ** 2).sum()) if len(shares) else np.nan,
        "n_fit": len(fit),
        "n_calib_fit": len(cal_fit),
        "n_calib_select": len(cal_select),
        "n_test": len(test),
        "used_features": used,
    }


def summarize_months(months: Sequence[Dict[str, object]]) -> Dict[str, object]:
    ok = [m for m in months if m.get("status") == "OK"]
    selected = [m for m in ok if int(m.get("n", 0)) > 0]
    if not ok:
        return {"status": "NO_VALID_MONTHS"}

    total_n = int(sum(int(m.get("n", 0)) for m in selected))

    def weighted(key: str) -> float:
        vals = []
        for m in selected:
            v = float(m.get(key, np.nan))
            n = int(m.get("n", 0))
            if np.isfinite(v) and n > 0:
                vals.append((v, n))
        if not vals:
            return float("nan")
        return float(sum(v * n for v, n in vals) / sum(n for _, n in vals))

    def med(key: str) -> float:
        arr = [float(m.get(key, np.nan)) for m in ok]
        return float(np.nanmedian(arr)) if arr else np.nan

    enabled_months = sum(bool(m.get("enabled_by_calibration", False)) for m in ok)
    positive_x1 = sum(float(m.get("net_x1_mean", np.nan)) > 0 for m in selected)
    positive_x2 = sum(float(m.get("net_x2_mean", np.nan)) > 0 for m in selected)
    return {
        "status": "OK",
        "months_ok": len(ok),
        "months_enabled": int(enabled_months),
        "selected_n": total_n,
        "median_ic_spearman": med("ic_spearman"),
        "median_pf_x1": med("pf_x1"),
        "median_pf_x2": med("pf_x2"),
        "median_brier_improvement": float(np.nanmedian([
            float(m.get("brier_raw", np.nan)) - float(m.get("brier_calibrated", np.nan)) for m in ok
        ])),
        "pooled_gross_mean": weighted("gross_mean"),
        "pooled_net_x1_mean": weighted("net_x1_mean"),
        "pooled_net_x2_mean": weighted("net_x2_mean"),
        "positive_net_x1_months": int(positive_x1),
        "positive_net_x2_months": int(positive_x2),
        "positive_net_x1_share": float(positive_x1 / len(selected)) if selected else 0.0,
        "positive_net_x2_share": float(positive_x2 / len(selected)) if selected else 0.0,
        "median_calibration_ratio": med("calibration_ratio"),
        "median_max_symbol_share": med("max_symbol_share"),
    }


def choose_dev_candidate(group_summaries: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    """Pre-registered DEV gate. HOLDOUT is forbidden unless a group clears every gate."""
    candidates = []
    reasons = {}
    for group, s in group_summaries.items():
        group_reasons = []
        if s.get("status") != "OK":
            group_reasons.append("summary_not_ok")
        if int(s.get("months_ok", 0)) < 30:
            group_reasons.append("fewer_than_30_months")
        if int(s.get("months_enabled", 0)) < 12:
            group_reasons.append("fewer_than_12_enabled_months")
        if int(s.get("selected_n", 0)) < 500:
            group_reasons.append("fewer_than_500_selected_trades")
        if float(s.get("pooled_net_x1_mean", np.nan)) <= 0:
            group_reasons.append("net_x1_nonpositive")
        if float(s.get("pooled_net_x2_mean", np.nan)) <= 0:
            group_reasons.append("net_x2_nonpositive")
        if float(s.get("median_pf_x2", np.nan)) < 1.15:
            group_reasons.append("median_pf_x2_below_1.15")
        if float(s.get("positive_net_x2_share", 0.0)) < 0.60:
            group_reasons.append("positive_x2_month_share_below_60pct")
        if float(s.get("median_ic_spearman", np.nan)) <= 0:
            group_reasons.append("median_ic_nonpositive")
        if float(s.get("median_brier_improvement", np.nan)) < 0:
            group_reasons.append("direction_calibration_worsens_brier")
        if float(s.get("median_max_symbol_share", np.nan)) > 0.80:
            group_reasons.append("dev_symbol_concentration_above_80pct")
        reasons[group] = group_reasons
        if not group_reasons:
            candidates.append(group)
    if not candidates:
        return {"status": "NO_CANDIDATE", "selected_group": None, "reasons": reasons}
    selected = max(candidates, key=lambda g: float(group_summaries[g]["pooled_net_x2_mean"]))
    return {"status": "CANDIDATE", "selected_group": selected, "reasons": reasons}
