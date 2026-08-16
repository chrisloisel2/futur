from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .information_audit import spearman_ic
from .phase5_audit import DEFAULT_VENUES, add_targets, prepare_features


def _partial_spearman(x: pd.Series, y: pd.Series, z: pd.Series) -> float:
    valid = x.notna() & y.notna() & z.notna()
    if int(valid.sum()) < 20:
        return float("nan")
    xv = x[valid].reset_index(drop=True)
    yv = y[valid].reset_index(drop=True)
    zv = z[valid].reset_index(drop=True)
    rxy = spearman_ic(xv, yv)
    rxz = spearman_ic(xv, zv)
    ryz = spearman_ic(yv, zv)
    if not all(np.isfinite(v) for v in (rxy, rxz, ryz)):
        return float("nan")
    denom = (1.0 - rxz * rxz) * (1.0 - ryz * ryz)
    if denom <= 1e-12:
        return float("nan")
    return float((rxy - rxz * ryz) / np.sqrt(denom))


def _signed_consistency(values: Iterable[float], sign: int) -> int:
    if sign == 0:
        return 0
    return int(sum(1 for value in values if np.isfinite(value) and int(np.sign(value)) == sign))


def _third_ics(x: pd.Series, y: pd.Series) -> Tuple[float, float, float]:
    valid = x.notna() & y.notna()
    xv = x[valid].reset_index(drop=True)
    yv = y[valid].reset_index(drop=True)
    n = len(xv)
    if n < 30:
        return float("nan"), float("nan"), float("nan")
    cuts = [0, n // 3, (2 * n) // 3, n]
    out = []
    for start, stop in zip(cuts[:-1], cuts[1:]):
        out.append(spearman_ic(xv.iloc[start:stop], yv.iloc[start:stop]))
    return float(out[0]), float(out[1]), float(out[2])


def _regime_ics(x: pd.Series, y: pd.Series, past: pd.Series) -> Tuple[float, float, int, int]:
    valid = x.notna() & y.notna() & past.notna()
    up = valid & (past > 0)
    down = valid & (past < 0)
    up_ic = spearman_ic(x[up], y[up]) if int(up.sum()) >= 20 else float("nan")
    down_ic = spearman_ic(x[down], y[down]) if int(down.sum()) >= 20 else float("nan")
    return float(up_ic), float(down_ic), int(up.sum()), int(down.sum())


def _decile_response(x: pd.Series, y: pd.Series) -> Dict[str, float]:
    valid = x.notna() & y.notna()
    xv = x[valid]
    yv = y[valid]
    if len(xv) < 100:
        return {
            "bottom_decile_mean_bps": float("nan"),
            "top_decile_mean_bps": float("nan"),
            "top_minus_bottom_bps": float("nan"),
        }
    lo = float(xv.quantile(0.10))
    hi = float(xv.quantile(0.90))
    bottom = yv[xv <= lo]
    top = yv[xv >= hi]
    bottom_mean = float(bottom.mean()) if len(bottom) else float("nan")
    top_mean = float(top.mean()) if len(top) else float("nan")
    return {
        "bottom_decile_mean_bps": bottom_mean,
        "top_decile_mean_bps": top_mean,
        "top_minus_bottom_bps": float(top_mean - bottom_mean)
        if np.isfinite(bottom_mean) and np.isfinite(top_mean)
        else float("nan"),
    }


def _feature_venue(feature: str, venues: Sequence[str]) -> Optional[str]:
    if "__" not in str(feature):
        return None
    prefix = str(feature).split("__", 1)[0].lower()
    return prefix if prefix in {str(v).lower() for v in venues} else None


def _loo_fair_value(group: pd.DataFrame, excluded_venue: str, venues: Sequence[str]) -> pd.Series:
    numerator = pd.Series(0.0, index=group.index, dtype=float)
    denominator = pd.Series(0.0, index=group.index, dtype=float)
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        if venue == excluded_venue:
            continue
        mid_col = venue + "__price_mid"
        weight_col = venue + "__price_weight"
        if mid_col not in group:
            continue
        mid = pd.to_numeric(group[mid_col], errors="coerce")
        if weight_col in group:
            weight = pd.to_numeric(group[weight_col], errors="coerce").fillna(0.0).clip(lower=0.0)
        else:
            weight = pd.Series(1.0, index=group.index, dtype=float)
        good = mid.notna() & np.isfinite(mid) & (mid > 0) & np.isfinite(weight) & (weight > 0)
        numerator = numerator + mid.where(good, 0.0) * weight.where(good, 0.0)
        denominator = denominator + weight.where(good, 0.0)
    return numerator / denominator.where(denominator > 0)


def _loo_targets(group: pd.DataFrame, excluded_venue: str, horizon_ms: int, cadence_ms: int, venues: Sequence[str]) -> Tuple[pd.Series, pd.Series]:
    fv = _loo_fair_value(group, excluded_venue, venues)
    steps = int(horizon_ms) // int(cadence_ms)
    future = fv.shift(-steps)
    past = fv.shift(steps)
    target = 1e4 * np.log(future / fv)
    past_return = 1e4 * np.log(fv / past)
    return target, past_return


def run_mechanism_diagnostics(
    frame: pd.DataFrame,
    mechanisms: pd.DataFrame,
    cadence_ms: int = 100,
    venues: Sequence[str] = DEFAULT_VENUES,
    classifications: Sequence[str] = ("GENERAL_CANDIDATE",),
    progress: bool = False,
) -> Dict[str, object]:
    selected = mechanisms[mechanisms["classification"].isin(list(classifications))].copy()
    if selected.empty:
        raise ValueError("no mechanisms match requested classifications")

    horizons = sorted({int(v) for v in selected["horizon_ms"].tolist()})
    prepared, registry = prepare_features(frame, venues=venues)
    prepared = add_targets(prepared, cadence_ms=cadence_ms, horizons_ms=horizons)

    rows: List[Dict[str, object]] = []
    for position, mechanism in enumerate(selected.itertuples(index=False), 1):
        feature = str(mechanism.feature)
        horizon = int(mechanism.horizon_ms)
        if feature not in registry:
            continue
        venue = _feature_venue(feature, venues)
        if progress:
            print(
                "[phase5.1] mechanism %s/%s %s h=%sms"
                % (position, len(selected), feature, horizon),
                flush=True,
            )
        for symbol, group_raw in prepared.groupby("symbol", sort=True):
            group = group_raw.sort_values("asof_ns").reset_index(drop=True)
            x = pd.to_numeric(group[feature], errors="coerce")
            y = pd.to_numeric(group["target_%sms_bps" % horizon], errors="coerce")
            past = pd.to_numeric(group["past_%sms_bps" % horizon], errors="coerce")
            valid = x.notna() & y.notna()
            n = int(valid.sum())
            ic = spearman_ic(x, y)
            reverse_ic = spearman_ic(x, past)
            momentum_ic = spearman_ic(past, y)
            partial_ic = _partial_spearman(x, y, past)
            t1, t2, t3 = _third_ics(x, y)
            up_ic, down_ic, up_n, down_n = _regime_ics(x, y, past)
            decile = _decile_response(x, y)

            loo_ic = float("nan")
            loo_partial_ic = float("nan")
            loo_reverse_ic = float("nan")
            if venue is not None:
                loo_y, loo_past = _loo_targets(group, venue, horizon, cadence_ms, venues)
                loo_ic = spearman_ic(x, loo_y)
                loo_partial_ic = _partial_spearman(x, loo_y, loo_past)
                loo_reverse_ic = spearman_ic(x, loo_past)

            sign = int(np.sign(ic)) if np.isfinite(ic) and ic != 0 else 0
            third_same_sign = _signed_consistency((t1, t2, t3), sign)
            regime_same_sign = _signed_consistency((up_ic, down_ic), sign)
            partial_retention = (
                float(abs(partial_ic) / abs(ic))
                if np.isfinite(partial_ic) and np.isfinite(ic) and abs(ic) > 1e-12
                else float("nan")
            )
            loo_retention = (
                float(abs(loo_ic) / abs(ic))
                if np.isfinite(loo_ic) and np.isfinite(ic) and abs(ic) > 1e-12
                else float("nan")
            )
            flags = []
            if np.isfinite(reverse_ic) and np.isfinite(ic) and abs(reverse_ic) > abs(ic):
                flags.append("REVERSE_DOMINANT")
            if np.isfinite(partial_ic) and sign and int(np.sign(partial_ic)) != sign:
                flags.append("PARTIAL_SIGN_FLIP")
            if np.isfinite(loo_ic) and sign and int(np.sign(loo_ic)) != sign:
                flags.append("LOO_SIGN_FLIP")
            if third_same_sign < 2:
                flags.append("TIME_UNSTABLE")
            if np.isfinite(up_ic) and np.isfinite(down_ic) and regime_same_sign < 2:
                flags.append("REGIME_SIGN_FLIP")
            if feature.endswith("spread_bps"):
                flags.append("UNSIGNED_DIRECTIONAL_FEATURE")

            row = {
                "feature": feature,
                "family": str(mechanism.family),
                "horizon_ms": horizon,
                "symbol": str(symbol),
                "venue": venue or "",
                "n": n,
                "ic": float(ic),
                "reverse_ic": float(reverse_ic),
                "momentum_ic": float(momentum_ic),
                "partial_ic_controlling_past": float(partial_ic),
                "partial_retention": partial_retention,
                "loo_ic": float(loo_ic),
                "loo_partial_ic": float(loo_partial_ic),
                "loo_reverse_ic": float(loo_reverse_ic),
                "loo_retention": loo_retention,
                "third1_ic": float(t1),
                "third2_ic": float(t2),
                "third3_ic": float(t3),
                "thirds_same_sign": int(third_same_sign),
                "past_up_ic": float(up_ic),
                "past_down_ic": float(down_ic),
                "past_up_n": int(up_n),
                "past_down_n": int(down_n),
                "regimes_same_sign": int(regime_same_sign),
                "confound_flags": ",".join(flags) if flags else "NONE",
            }
            row.update(decile)
            rows.append(row)

    diagnostics = pd.DataFrame(rows)
    mechanism_rows = []
    for (feature, horizon), group in diagnostics.groupby(["feature", "horizon_ms"], sort=True):
        raw_median = float(group["ic"].median())
        sign = int(np.sign(raw_median)) if np.isfinite(raw_median) and raw_median != 0 else 0
        mechanism_rows.append({
            "feature": feature,
            "family": str(group["family"].iloc[0]),
            "horizon_ms": int(horizon),
            "raw_median_ic": raw_median,
            "partial_median_ic": float(group["partial_ic_controlling_past"].median()),
            "loo_median_ic": float(group["loo_ic"].median()),
            "loo_partial_median_ic": float(group["loo_partial_ic"].median()),
            "raw_same_sign_symbols": _signed_consistency(group["ic"], sign),
            "partial_same_sign_symbols": _signed_consistency(group["partial_ic_controlling_past"], sign),
            "loo_same_sign_symbols": _signed_consistency(group["loo_ic"], sign),
            "three_thirds_same_sign_symbols": int((group["thirds_same_sign"] == 3).sum()),
            "two_regimes_same_sign_symbols": int((group["regimes_same_sign"] == 2).sum()),
            "reverse_dominant_symbols": int(group["confound_flags"].str.contains("REVERSE_DOMINANT", regex=False).sum()),
            "note": "EXPLORATORY_DEV_DIAGNOSTIC_ONLY",
        })
    mechanism_summary = pd.DataFrame(mechanism_rows)

    return {
        "diagnostics": diagnostics,
        "mechanisms": mechanism_summary,
        "summary": {
            "verdict": "EXPLORATORY_DEV_DIAGNOSTIC_ONLY",
            "mechanisms": int(len(mechanism_summary)),
            "symbol_rows": int(len(diagnostics)),
            "classifications": list(classifications),
            "causal_claim": False,
            "economic_claim": False,
        },
    }


def write_mechanism_diagnostics(result: Dict[str, object], out_dir: str) -> Dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    diagnostics = result["diagnostics"]
    mechanisms = result["mechanisms"]
    summary = result["summary"]
    diagnostics_path = out / "symbol_diagnostics.csv"
    mechanisms_path = out / "mechanism_diagnostics.csv"
    summary_path = out / "SUMMARY.json"
    diagnostics.to_csv(diagnostics_path, index=False)
    mechanisms.to_csv(mechanisms_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    return {
        "symbol_diagnostics": str(diagnostics_path),
        "mechanism_diagnostics": str(mechanisms_path),
        "summary": str(summary_path),
    }
