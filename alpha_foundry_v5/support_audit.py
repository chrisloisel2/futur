from __future__ import annotations

import fnmatch
import hashlib
import json
import math
from typing import Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

from market_physics_v3.information_audit import effective_sample_size


SUPPORT_POLICY_VERSION = "afv5-support-v1"
DEFAULT_LABS = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8")
KNOWN_VENUES = ("binance", "bybit", "okx", "hyperliquid")


LAB_SUPPORT_POLICY = {
    "A1": {
        "groups": [
            {"name": "price_updates", "kind": "clock_changes", "patterns": ["*__price_available_ts_ns"], "min_total": 1000, "min_symbols": 2, "min_venues": 3},
        ],
        "anchors": ["*__price_dislocation_bps", "*__price_spread_bps"],
    },
    "A2": {
        "groups": [
            {"name": "price_updates", "kind": "clock_changes", "patterns": ["*__price_available_ts_ns"], "min_total": 1000, "min_symbols": 2, "min_venues": 3},
        ],
        "anchors": ["*__price_dislocation_bps"],
    },
    "A3": {
        "groups": [
            {"name": "book_depletions", "kind": "count_sum", "patterns": ["*__*remove_count_100ms"], "min_total": 1000, "min_symbols": 2, "min_venues": 2},
            {"name": "trade_events", "kind": "count_sum", "patterns": ["*__trade_count_100ms"], "min_total": 1000, "min_symbols": 2, "min_venues": 3},
        ],
        "anchors": ["*__price_queue_imbalance_l1", "*__queue_imbalance_l5", "*__depletion_pressure_100ms", "*__flow_imbalance_100ms"],
    },
    "A4": {
        "groups": [
            {"name": "book_replenishments", "kind": "count_sum", "patterns": ["*__*add_count_100ms"], "min_total": 1000, "min_symbols": 2, "min_venues": 2},
            {"name": "trade_events", "kind": "count_sum", "patterns": ["*__trade_count_100ms"], "min_total": 1000, "min_symbols": 2, "min_venues": 3},
        ],
        "anchors": ["*__replenishment_imbalance_100ms", "*__trades_per_second_100ms", "*__bid_depth_5bps", "*__ask_depth_5bps"],
    },
    "A5": {
        "groups": [
            {"name": "trade_events", "kind": "count_sum", "patterns": ["*__trade_count_100ms"], "min_total": 1000, "min_symbols": 2, "min_venues": 3},
            {"name": "price_impact_events", "kind": "nonzero", "patterns": ["*__impact_bps_100ms"], "min_total": 100, "min_symbols": 2, "min_venues": 2},
        ],
        "anchors": ["*__flow_imbalance_100ms", "*__impact_bps_100ms", "*__absorption_100ms"],
    },
    "A6": {
        "groups": [
            {"name": "depth_updates", "kind": "clock_changes", "patterns": ["*__depth_available_ts_ns"], "min_total": 1000, "min_symbols": 2, "min_venues": 2},
        ],
        "anchors": ["*__price_spread_bps", "*__bid_depth_5bps", "*__ask_depth_5bps", "*__notional_to_move_5bps"],
    },
    "A7": {
        "groups": [
            {"name": "liquidation_updates", "kind": "clock_changes", "patterns": ["*__liquidation_available_ts_ns"], "min_total": 10, "min_symbols": 2, "min_venues": 2},
            {"name": "oi_economic_changes", "kind": "nonzero", "patterns": ["*__open_interest_change_pct"], "min_total": 100, "min_symbols": 2, "min_venues": 2},
        ],
        "anchors": ["*__liquidation_to_depth_30000ms", "*__liquidation_notional_30000ms", "*__open_interest_change_pct"],
    },
    "A8": {
        "groups": [
            {"name": "oi_economic_changes", "kind": "nonzero", "patterns": ["*__open_interest_change_pct"], "min_total": 100, "min_symbols": 2, "min_venues": 2},
            {"name": "basis_economic_changes", "kind": "nonzero", "patterns": ["*__basis_velocity"], "min_total": 50, "min_symbols": 2, "min_venues": 2},
        ],
        "anchors": ["*__open_interest_change_pct", "*__basis_bps", "*__funding", "deriv__median_oi_change_pct"],
    },
}


def _canonical_digest(payload: Mapping[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def support_policy_digest() -> str:
    return _canonical_digest({"version": SUPPORT_POLICY_VERSION, "labs": LAB_SUPPORT_POLICY})


def _match_columns(columns: Sequence[str], patterns: Sequence[str]) -> List[str]:
    out = []
    for pattern in patterns:
        for column in columns:
            name = str(column)
            if name.startswith("target_"):
                continue
            if fnmatch.fnmatchcase(name, str(pattern)) and name not in out:
                out.append(name)
    return out


def _venue_from_column(column: str) -> str:
    prefix = str(column).split("__", 1)[0].lower()
    return prefix if prefix in KNOWN_VENUES else "cross"


def _effective_count(values: Mapping[str, float]) -> float:
    arr = np.asarray([float(v) for v in values.values() if float(v) > 0], dtype=float)
    if arr.size == 0:
        return 0.0
    shares = arr / float(arr.sum())
    hhi = float(np.sum(shares * shares))
    return float(1.0 / hhi) if hhi > 0 else 0.0


def _clock_change_mask(frame: pd.DataFrame, column: str) -> Tuple[pd.Series, pd.Series]:
    x = pd.to_numeric(frame[column], errors="coerce")
    if "symbol" in frame:
        prev = x.groupby(frame["symbol"], sort=False).shift(1)
    else:
        prev = x.shift(1)
    mask = x.notna() & (prev.isna() | (x != prev))
    weight = mask.astype(float)
    return mask, weight


def _nonzero_mask(frame: pd.DataFrame, column: str) -> Tuple[pd.Series, pd.Series]:
    x = pd.to_numeric(frame[column], errors="coerce")
    mask = x.notna() & (x.abs() > 1e-18)
    return mask, mask.astype(float)


def _count_sum_mask(frame: pd.DataFrame, column: str) -> Tuple[pd.Series, pd.Series]:
    x = pd.to_numeric(frame[column], errors="coerce").fillna(0.0).clip(lower=0.0)
    mask = x > 0
    return mask, x.where(mask, 0.0)


def _source_group_metrics(frame: pd.DataFrame, spec: Mapping[str, object]) -> Tuple[Dict[str, object], pd.Series]:
    columns = _match_columns(list(frame.columns), list(spec["patterns"]))
    union = pd.Series(False, index=frame.index)
    venue_counts = {}  # type: Dict[str, float]
    symbol_counts = {}  # type: Dict[str, float]
    column_counts = {}  # type: Dict[str, float]
    total = 0.0
    kind = str(spec["kind"])
    for column in columns:
        if kind == "clock_changes":
            mask, weight = _clock_change_mask(frame, column)
        elif kind == "nonzero":
            mask, weight = _nonzero_mask(frame, column)
        elif kind == "count_sum":
            mask, weight = _count_sum_mask(frame, column)
        else:
            raise ValueError("unsupported support source kind: %s" % kind)
        union |= mask
        count = float(weight.sum())
        column_counts[column] = count
        total += count
        venue = _venue_from_column(column)
        venue_counts[venue] = venue_counts.get(venue, 0.0) + count
        if "symbol" in frame:
            grouped = weight.groupby(frame["symbol"], sort=False).sum()
            for symbol, value in grouped.items():
                if float(value) > 0:
                    symbol_counts[str(symbol)] = symbol_counts.get(str(symbol), 0.0) + float(value)

    active_symbols = [k for k, v in symbol_counts.items() if v > 0]
    active_venues = [k for k, v in venue_counts.items() if k != "cross" and v > 0]
    passed = bool(
        columns
        and total >= float(spec["min_total"])
        and len(active_symbols) >= int(spec["min_symbols"])
        and len(active_venues) >= int(spec["min_venues"])
    )
    metrics = {
        "name": str(spec["name"]),
        "kind": kind,
        "matched_columns": columns,
        "events_total": total,
        "event_rows": int(union.sum()),
        "events_by_symbol": dict(sorted(symbol_counts.items())),
        "events_by_venue": dict(sorted(venue_counts.items())),
        "effective_symbols": _effective_count(symbol_counts),
        "effective_venues": _effective_count({k: v for k, v in venue_counts.items() if k != "cross"}),
        "min_total": int(spec["min_total"]),
        "min_symbols": int(spec["min_symbols"]),
        "min_venues": int(spec["min_venues"]),
        "pass": passed,
    }
    return metrics, union


def _infer_cadence_ms(frame: pd.DataFrame) -> int:
    if "asof_ns" not in frame:
        raise ValueError("support audit requires asof_ns")
    unique = np.sort(pd.to_numeric(frame["asof_ns"], errors="coerce").dropna().astype(np.int64).unique())
    if len(unique) < 2:
        raise ValueError("cannot infer cadence from fewer than two asof timestamps")
    diffs = np.diff(unique)
    cadence_ns = int(np.median(diffs))
    if cadence_ns <= 0:
        raise ValueError("invalid inferred cadence")
    return max(1, int(round(cadence_ns / 1e6)))


def _event_diversity(frame: pd.DataFrame, event_mask: pd.Series, cadence_ms: int, regime_lookback_ms: int = 10000) -> Dict[str, object]:
    asof = pd.to_numeric(frame["asof_ns"], errors="coerce")
    start = int(asof.min())
    stop = int(asof.max())
    span = max(1, stop - start + 1)
    third = ((asof - start) * 3 // span).clip(lower=0, upper=2)
    thirds = {str(i + 1): int((event_mask & (third == i)).sum()) for i in range(3)}

    up = down = flat = 0
    if "price_fair_value" in frame and "symbol" in frame:
        steps = max(1, int(round(float(regime_lookback_ms) / float(cadence_ms))))
        price = pd.to_numeric(frame["price_fair_value"], errors="coerce")
        past = price.groupby(frame["symbol"], sort=False).shift(steps)
        ret = np.log(price / past)
        up = int((event_mask & (ret > 0)).sum())
        down = int((event_mask & (ret < 0)).sum())
        flat = int((event_mask & (ret == 0)).sum())
    return {
        "chronological_thirds_event_rows": thirds,
        "all_thirds_present": all(v > 0 for v in thirds.values()),
        "past_10s_up_event_rows": up,
        "past_10s_down_event_rows": down,
        "past_10s_flat_event_rows": flat,
        "both_direction_regimes_present": up > 0 and down > 0,
    }


def _anchor_metrics(frame: pd.DataFrame, patterns: Sequence[str], max_features: int = 12, max_lag: int = 200) -> Dict[str, object]:
    columns = _match_columns(list(frame.columns), patterns)[: int(max_features)]
    rows = []
    ess_values = []
    for column in columns:
        for symbol, group in frame.groupby("symbol", sort=True):
            x = pd.to_numeric(group[column], errors="coerce")
            n = int(x.notna().sum())
            if n < 3:
                ess = float(n)
                transitions = 0
            else:
                ess = float(effective_sample_size(x, max_lag=max_lag))
                previous = x.shift(1)
                transitions = int((x.notna() & previous.notna() & (x != previous)).sum())
            if math.isfinite(ess):
                ess_values.append(ess)
            rows.append({
                "feature": column,
                "symbol": str(symbol),
                "n": n,
                "ess": ess,
                "ess_ratio": float(ess / n) if n > 0 else 0.0,
                "transition_count": transitions,
            })
    return {
        "anchor_columns": columns,
        "feature_symbol_pairs": rows,
        "median_ess": float(np.median(ess_values)) if ess_values else 0.0,
        "min_ess": float(np.min(ess_values)) if ess_values else 0.0,
        "pairs_ess_ge_200": int(sum(v >= 200.0 for v in ess_values)),
        "pair_count": int(len(rows)),
    }


def run_mechanism_support_audit(
    frame: pd.DataFrame,
    readiness: Mapping[str, Mapping[str, object]],
    labs: Sequence[str] = DEFAULT_LABS,
) -> Dict[str, object]:
    if frame.empty:
        raise ValueError("empty frame")
    if "symbol" not in frame or "asof_ns" not in frame:
        raise ValueError("support audit requires symbol/asof_ns")
    cadence_ms = _infer_cadence_ms(frame)
    result_labs = {}
    for lab_id in labs:
        lab_id = str(lab_id).upper()
        if lab_id not in LAB_SUPPORT_POLICY:
            raise ValueError("unsupported support-audit lab: %s" % lab_id)
        policy = LAB_SUPPORT_POLICY[lab_id]
        ready = bool((readiness.get(lab_id) or {}).get("ready", False))
        group_rows = []
        event_mask = pd.Series(False, index=frame.index)
        for group_spec in policy["groups"]:
            metrics, mask = _source_group_metrics(frame, group_spec)
            group_rows.append(metrics)
            event_mask |= mask
        diversity = _event_diversity(frame, event_mask, cadence_ms)
        anchors = _anchor_metrics(frame, policy["anchors"])
        groups_pass = bool(group_rows) and all(bool(x["pass"]) for x in group_rows)
        ess_ok = anchors["pair_count"] > 0 and float(anchors["median_ess"]) >= 200.0
        diversity_ok = bool(diversity["all_thirds_present"] and diversity["both_direction_regimes_present"])
        if not ready:
            verdict = "BLOCKED"
            budget = 0
        elif not groups_pass:
            verdict = "THIN_SUPPORT"
            budget = 0
        elif ess_ok and diversity_ok:
            verdict = "STRONG_SUPPORT"
            budget = 8
        else:
            verdict = "ADEQUATE_SUPPORT"
            budget = 4
        result_labs[lab_id] = {
            "data_ready": ready,
            "support_groups": group_rows,
            "independent_event_rows": int(event_mask.sum()),
            "diversity": diversity,
            "anchors": anchors,
            "support_verdict": verdict,
            "recommended_max_hypothesis_tests": budget,
            "groups_pass": groups_pass,
            "anchor_ess_ok": ess_ok,
            "diversity_ok": diversity_ok,
        }

    payload = {
        "policy_version": SUPPORT_POLICY_VERSION,
        "policy_digest": support_policy_digest(),
        "target_free": True,
        "target_columns_used": [],
        "cadence_ms": int(cadence_ms),
        "rows": int(len(frame)),
        "symbols": sorted(str(x) for x in frame["symbol"].dropna().unique()),
        "labs": result_labs,
        "strong_support_labs": [k for k, v in result_labs.items() if v["support_verdict"] == "STRONG_SUPPORT"],
        "adequate_support_labs": [k for k, v in result_labs.items() if v["support_verdict"] == "ADEQUATE_SUPPORT"],
        "thin_or_blocked_labs": [k for k, v in result_labs.items() if v["support_verdict"] in {"THIN_SUPPORT", "BLOCKED"}],
        "scientific_boundary": "support audit uses no future target and does not constitute alpha evidence; it only caps the amount of subsequent hypothesis testing",
    }
    payload["audit_digest"] = _canonical_digest(payload)
    return payload
