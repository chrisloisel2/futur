from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

from .information_audit import block_shuffle_null, effective_sample_size, spearman_ic

DEFAULT_HORIZONS_MS = (100, 500, 1000, 2000, 5000, 10000, 30000)
DEFAULT_VENUES = ("binance", "bybit", "okx", "hyperliquid")

PRICE_SUFFIXES = (
    "price_dislocation_bps",
    "price_microprice_offset_bps",
    "price_queue_imbalance_l1",
    "price_ofi_l1_grid",
    "price_spread_bps",
)
DEPTH_SUFFIXES = (
    "queue_imbalance_l1",
    "queue_imbalance_l5",
    "queue_imbalance_l10",
    "ofi_l1_grid",
)


def load_parquet_dataset(path: str) -> pd.DataFrame:
    root = Path(path)
    if root.is_file():
        return pd.read_parquet(root)
    parts = sorted(root.glob("part-*.parquet"))
    if not parts:
        raise ValueError("no parquet parts found under %s" % root)
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)


def _asym(a: pd.Series, b: pd.Series) -> pd.Series:
    denom = a.abs() + b.abs()
    return (a - b) / denom.where(denom > 0)


def prepare_features(frame: pd.DataFrame, venues: Sequence[str] = DEFAULT_VENUES) -> Tuple[pd.DataFrame, Dict[str, str]]:
    out = frame.copy()
    registry: Dict[str, str] = {}
    if "price_ready" not in out or "price_fair_value" not in out:
        raise ValueError("Phase 4.1 price fields are required")

    for venue_raw in venues:
        venue = str(venue_raw).lower()
        prefix = venue + "__"
        for suffix in PRICE_SUFFIXES:
            col = prefix + suffix
            if col in out:
                registry[col] = "price"

        depth_mask = out.get(prefix + "depth_fresh", pd.Series(False, index=out.index)).fillna(False).astype(bool)
        for suffix in DEPTH_SUFFIXES:
            col = prefix + suffix
            if col in out:
                out.loc[~depth_mask, col] = np.nan
                registry[col] = "depth"

        derived = [
            ("depth_5bps_imbalance", prefix + "bid_depth_5bps", prefix + "ask_depth_5bps"),
            ("depth_25bps_imbalance", prefix + "bid_depth_25bps", prefix + "ask_depth_25bps"),
            ("notional_10bps_imbalance", prefix + "sell_notional_10bps", prefix + "buy_notional_10bps"),
            ("weighted_distance_imbalance", prefix + "ask_weighted_distance_bps", prefix + "bid_weighted_distance_bps"),
        ]
        for name, left, right in derived:
            if left in out and right in out:
                col = prefix + name
                out[col] = _asym(pd.to_numeric(out[left], errors="coerce"), pd.to_numeric(out[right], errors="coerce"))
                out.loc[~depth_mask, col] = np.nan
                registry[col] = "depth"

        if prefix + "bid_qty_per_order_l10" in out and prefix + "ask_qty_per_order_l10" in out:
            col = prefix + "qty_per_order_imbalance_l10"
            out[col] = _asym(
                pd.to_numeric(out[prefix + "bid_qty_per_order_l10"], errors="coerce"),
                pd.to_numeric(out[prefix + "ask_qty_per_order_l10"], errors="coerce"),
            )
            out.loc[~depth_mask, col] = np.nan
            if out[col].notna().any():
                registry[col] = "depth"

    disloc_cols = [v + "__price_dislocation_bps" for v in venues if v + "__price_dislocation_bps" in out]
    if disloc_cols:
        out["cross__dislocation_range_bps"] = out[disloc_cols].max(axis=1) - out[disloc_cols].min(axis=1)
        out["cross__dislocation_absmax_bps"] = out[disloc_cols].abs().max(axis=1)
        registry["cross__dislocation_range_bps"] = "price"
        registry["cross__dislocation_absmax_bps"] = "price"
    if "price_dispersion_bps" in out:
        registry["price_dispersion_bps"] = "price"

    return out, registry


def add_targets(frame: pd.DataFrame, cadence_ms: int, horizons_ms: Sequence[int] = DEFAULT_HORIZONS_MS) -> pd.DataFrame:
    if cadence_ms <= 0:
        raise ValueError("cadence_ms must be positive")
    out = frame.sort_values(["symbol", "asof_ns"]).reset_index(drop=True).copy()
    current = pd.to_numeric(out["price_fair_value"], errors="coerce").where(out["price_ready"].fillna(False).astype(bool))
    out["_current_fv"] = current
    for horizon in horizons_ms:
        if int(horizon) % int(cadence_ms) != 0:
            raise ValueError("horizon %sms is not divisible by cadence %sms" % (horizon, cadence_ms))
        steps = int(horizon) // int(cadence_ms)
        future = current.groupby(out["symbol"], sort=False).shift(-steps)
        past = current.groupby(out["symbol"], sort=False).shift(steps)
        out["target_%sms_bps" % horizon] = 1e4 * np.log(future / current)
        out["past_%sms_bps" % horizon] = 1e4 * np.log(current / past)
    return out


def _ess_pvalue(ic: float, ess: float) -> float:
    if not np.isfinite(ic) or not np.isfinite(ess) or ess <= 3:
        return float("nan")
    r = max(-0.999999, min(0.999999, float(ic)))
    z = abs(math.atanh(r)) * math.sqrt(max(float(ess) - 3.0, 1.0))
    return float(math.erfc(z / math.sqrt(2.0)))


def _bh_qvalues(pvalues: pd.Series) -> pd.Series:
    p = pd.to_numeric(pvalues, errors="coerce")
    valid = p.dropna().sort_values()
    q = pd.Series(np.nan, index=p.index, dtype=float)
    if valid.empty:
        return q
    m = float(len(valid))
    raw = pd.Series([min(1.0, float(v) * m / (i + 1.0)) for i, v in enumerate(valid)], index=valid.index)
    monotone = raw.iloc[::-1].cummin().iloc[::-1]
    q.loc[monotone.index] = monotone
    return q


def run_information_audit(
    frame: pd.DataFrame,
    cadence_ms: int,
    horizons_ms: Sequence[int] = DEFAULT_HORIZONS_MS,
    venues: Sequence[str] = DEFAULT_VENUES,
    min_duration_hours: float = 6.0,
    allow_short_smoke: bool = False,
    block_shuffle_repeats: int = 100,
    max_block_shortlist: int = 40,
) -> Dict[str, object]:
    if frame.empty:
        raise ValueError("empty state tape")
    duration_hours = float((int(frame["asof_ns"].max()) - int(frame["asof_ns"].min())) / 3.6e12)
    if duration_hours < float(min_duration_hours) and not allow_short_smoke:
        raise ValueError(
            "state tape duration %.3fh < preregistered %.3fh DEV_PILOT minimum"
            % (duration_hours, min_duration_hours)
        )

    prepared, registry = prepare_features(frame, venues=venues)
    prepared = add_targets(prepared, cadence_ms, horizons_ms=horizons_ms)
    feature_ess: Dict[Tuple[str, str], float] = {}
    target_ess: Dict[Tuple[str, int], float] = {}
    rows: List[Dict[str, object]] = []

    for symbol, group in prepared.groupby("symbol", sort=True):
        group = group.reset_index(drop=True)
        for feature in sorted(registry):
            series = pd.to_numeric(group[feature], errors="coerce")
            feature_ess[(str(symbol), feature)] = effective_sample_size(series, max_lag=200)
        for horizon in horizons_ms:
            target = pd.to_numeric(group["target_%sms_bps" % horizon], errors="coerce")
            target_ess[(str(symbol), int(horizon))] = effective_sample_size(target, max_lag=200)

        for feature in sorted(registry):
            xall = pd.to_numeric(group[feature], errors="coerce")
            for horizon in horizons_ms:
                yall = pd.to_numeric(group["target_%sms_bps" % horizon], errors="coerce")
                past = pd.to_numeric(group["past_%sms_bps" % horizon], errors="coerce")
                valid = xall.notna() & yall.notna()
                n = int(valid.sum())
                if n < 20:
                    ic = float("nan")
                    first_ic = float("nan")
                    second_ic = float("nan")
                    reverse_ic = float("nan")
                else:
                    x = xall[valid].reset_index(drop=True)
                    y = yall[valid].reset_index(drop=True)
                    ic = spearman_ic(x, y)
                    split = len(x) // 2
                    first_ic = spearman_ic(x.iloc[:split], y.iloc[:split]) if split >= 3 else float("nan")
                    second_ic = spearman_ic(x.iloc[split:], y.iloc[split:]) if len(x) - split >= 3 else float("nan")
                    reverse_ic = spearman_ic(xall, past)
                ess = min(
                    float(feature_ess[(str(symbol), feature)]),
                    float(target_ess[(str(symbol), int(horizon))]),
                    float(n),
                )
                sign_consistent = bool(
                    np.isfinite(first_ic) and np.isfinite(second_ic) and first_ic != 0 and second_ic != 0
                    and np.sign(first_ic) == np.sign(second_ic)
                )
                rows.append({
                    "symbol": str(symbol),
                    "feature": feature,
                    "family": registry[feature],
                    "horizon_ms": int(horizon),
                    "n": n,
                    "ess": float(ess),
                    "ic": float(ic),
                    "first_half_ic": float(first_ic),
                    "second_half_ic": float(second_ic),
                    "sign_consistent_halves": sign_consistent,
                    "reverse_ic": float(reverse_ic),
                    "ess_p": _ess_pvalue(ic, ess),
                    "block_p": float("nan"),
                })

    tests = pd.DataFrame(rows)
    tests["q_bh"] = _bh_qvalues(tests["ess_p"])
    shortlist = tests[
        (tests["n"] >= 1000)
        & (tests["ess"] >= 200)
        & (tests["ic"].abs() >= 0.01)
        & (tests["q_bh"] <= 0.10)
        & (tests["sign_consistent_halves"])
    ].copy()
    shortlist = shortlist.sort_values(["q_bh", "ic"], ascending=[True, False]).head(int(max_block_shortlist))

    for idx, row in shortlist.iterrows():
        group = prepared[prepared["symbol"] == row["symbol"]].reset_index(drop=True)
        x = pd.to_numeric(group[row["feature"]], errors="coerce")
        y = pd.to_numeric(group["target_%sms_bps" % int(row["horizon_ms"])], errors="coerce")
        block_steps = max(1, int(max(30000, 10 * int(row["horizon_ms"])) // int(cadence_ms)))
        null = block_shuffle_null(
            x,
            y,
            block=block_steps,
            repeats=int(block_shuffle_repeats),
            seed=17 + int(idx),
        )
        tests.loc[idx, "block_p"] = float(null["p_two_sided"])

    tests["symbol_candidate"] = (
        (tests["n"] >= 1000)
        & (tests["ess"] >= 200)
        & (tests["ic"].abs() >= 0.015)
        & (tests["q_bh"] <= 0.05)
        & (tests["block_p"] <= 0.05)
        & (tests["sign_consistent_halves"])
    )

    mechanism_rows = []
    for (feature, horizon), group in tests.groupby(["feature", "horizon_ms"], sort=True):
        finite = group[np.isfinite(group["ic"])]
        median_ic = float(finite["ic"].median()) if not finite.empty else float("nan")
        sign = int(np.sign(median_ic)) if np.isfinite(median_ic) and median_ic != 0 else 0
        same_sign = int(((np.sign(finite["ic"]) == sign) & (np.sign(finite["ic"]) != 0)).sum()) if sign else 0
        candidate_same_sign = int(
            ((group["symbol_candidate"]) & (np.sign(group["ic"]) == sign)).sum()
        ) if sign else 0
        if candidate_same_sign >= 2:
            classification = "GENERAL_CANDIDATE"
        elif bool(group["symbol_candidate"].any()):
            classification = "SINGLE_SYMBOL_WATCH"
        else:
            classification = "NO_CANDIDATE_YET"
        mechanism_rows.append({
            "feature": feature,
            "family": str(group["family"].iloc[0]),
            "horizon_ms": int(horizon),
            "median_ic": median_ic,
            "same_sign_symbols": same_sign,
            "candidate_symbols": int(group["symbol_candidate"].sum()),
            "classification": classification,
        })
    mechanisms = pd.DataFrame(mechanism_rows).sort_values(
        ["classification", "median_ic"], ascending=[True, False]
    )

    verdict = "DEV_PILOT" if duration_hours >= float(min_duration_hours) else "SHORT_SMOKE_ONLY"
    return {
        "verdict": verdict,
        "duration_hours": duration_hours,
        "cadence_ms": int(cadence_ms),
        "horizons_ms": [int(x) for x in horizons_ms],
        "feature_count": int(len(registry)),
        "test_count": int(len(tests)),
        "general_candidates": int((mechanisms["classification"] == "GENERAL_CANDIDATE").sum()),
        "single_symbol_watches": int((mechanisms["classification"] == "SINGLE_SYMBOL_WATCH").sum()),
        "tests": tests,
        "mechanisms": mechanisms,
    }
