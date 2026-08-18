from __future__ import annotations

import fnmatch
import math
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from market_physics_v3.information_audit import effective_sample_size

from .labs.registry import LabRegistry
from .support_audit import (
    DEFAULT_LABS,
    KNOWN_VENUES,
    LAB_SUPPORT_POLICY,
    SUPPORT_POLICY_VERSION,
    _canonical_digest,
    _effective_count,
    _match_columns,
    support_policy_digest,
)
from .support_io import parquet_union_schema, support_projection_columns


Progress = Optional[Callable[[str], None]]
AUDIT_CLOCK_SUFFIXES = ("_available_ts_ns", "_receive_ts_ns")
META_COLUMNS = {"asof_ns", "symbol"}
PROVENANCE_META_SUFFIXES = ("_available_ts_ns",)


def _progress(cb: Progress, message: str) -> None:
    if cb is not None:
        cb(message)


def _venue_from_column(column: str) -> str:
    prefix = str(column).split("__", 1)[0].lower()
    return prefix if prefix in KNOWN_VENUES else "cross"


def _is_audit_metadata(column: str) -> bool:
    return str(column).endswith(AUDIT_CLOCK_SUFFIXES) or str(column) == "asof_ns"


def _is_provenance_metadata(column: str) -> bool:
    return str(column) in META_COLUMNS or str(column).endswith(PROVENANCE_META_SUFFIXES)


def _read_part(part: Path, requested: Sequence[str], available: Sequence[str]) -> pd.DataFrame:
    have = set(str(x) for x in available)
    columns = [str(c) for c in requested if str(c) in have]
    if "asof_ns" not in columns or "symbol" not in columns:
        raise ValueError("streaming support part lost asof_ns/symbol: %s" % part)
    frame = pd.read_parquet(part, columns=columns)
    return frame.sort_values(["asof_ns", "symbol"], kind="mergesort").reset_index(drop=True)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _nonnull_count(frame: pd.DataFrame, column: str) -> int:
    if column not in frame.columns:
        return 0
    return int(frame[column].notna().sum())


def _active_count(frame: pd.DataFrame, column: str) -> int:
    x = _numeric(frame, column).to_numpy(dtype=float)
    return int(np.sum(np.isfinite(x) & (np.abs(x) > 1e-15)))


def _readiness_candidate_columns(all_columns: Sequence[str], labs: Sequence[str], registry: LabRegistry) -> Tuple[str, ...]:
    columns = tuple(str(c) for c in all_columns)
    selected = []
    seen = set()
    for raw_lab in labs:
        spec = registry.spec(str(raw_lab).upper())
        patterns = list(spec.required_column_patterns)
        for group in spec.required_any_groups:
            patterns.extend(group)
        for pattern, _min_rows in spec.activity_requirements:
            patterns.append(pattern)
        for pattern in patterns:
            for column in columns:
                if _is_audit_metadata(column):
                    continue
                if fnmatch.fnmatchcase(column, str(pattern)) and column not in seen:
                    seen.add(column)
                    selected.append(column)
    return tuple(selected)


def _build_readiness(
    all_columns: Sequence[str],
    labs: Sequence[str],
    registry: LabRegistry,
    total_rows: int,
    symbols: Sequence[str],
    nonnull: Mapping[str, int],
    active: Mapping[str, int],
) -> Dict[str, Dict[str, object]]:
    columns = tuple(str(c) for c in all_columns)
    out = {}

    def matches(pattern: str) -> List[str]:
        return [c for c in columns if not _is_audit_metadata(c) and fnmatch.fnmatchcase(c, str(pattern))]

    def coverage(column: str) -> float:
        return float(nonnull.get(column, 0)) / float(max(1, total_rows))

    for raw_lab in labs:
        lab_id = str(raw_lab).upper()
        spec = registry.spec(lab_id)
        missing = []
        coverage_by_requirement = {}
        for pattern in spec.required_column_patterns:
            candidates = matches(pattern)
            best = max([coverage(c) for c in candidates], default=0.0)
            coverage_by_requirement[pattern] = best
            if best < float(spec.min_coverage):
                missing.append(pattern)

        missing_groups = []
        for group in spec.required_any_groups:
            candidates = [c for c in columns if not _is_audit_metadata(c) and any(fnmatch.fnmatchcase(c, p) for p in group)]
            best = max([coverage(c) for c in candidates], default=0.0)
            coverage_by_requirement["ANY:" + "|".join(group)] = best
            if best < float(spec.min_coverage):
                missing_groups.append(tuple(group))

        activity = {}
        missing_activity = []
        for pattern, min_rows in spec.activity_requirements:
            candidates = matches(pattern)
            best = max([int(active.get(c, 0)) for c in candidates], default=0)
            activity[pattern] = {"active_rows": int(best), "min_active_rows": int(min_rows)}
            if best < int(min_rows):
                missing_activity.append(pattern)

        symbol_count = int(len(symbols))
        symbol_ready = symbol_count >= int(spec.min_symbols)
        data_ready = not missing and not missing_groups and not missing_activity
        out[lab_id] = {
            "ready": bool(data_ready and symbol_ready),
            "data_ready": bool(data_ready),
            "symbol_ready": bool(symbol_ready),
            "symbol_count": symbol_count,
            "min_symbols": int(spec.min_symbols),
            "min_coverage": float(spec.min_coverage),
            "coverage": coverage_by_requirement,
            "activity": activity,
            "missing_patterns": tuple(missing),
            "missing_any_groups": tuple(missing_groups),
            "missing_activity": tuple(missing_activity),
        }
    return out


def _group_template(spec: Mapping[str, object], matched_columns: Sequence[str]) -> Dict[str, object]:
    return {
        "spec": spec,
        "columns": tuple(str(c) for c in matched_columns),
        "events_total": 0.0,
        "event_rows": 0,
        "events_by_symbol": defaultdict(float),
        "events_by_venue": defaultdict(float),
        "events_by_column": defaultdict(float),
    }


def _clock_change(
    frame: pd.DataFrame,
    column: str,
    previous: Dict[Tuple[str, str], float],
) -> Tuple[np.ndarray, np.ndarray]:
    x = _numeric(frame, column).to_numpy(dtype=float)
    mask = np.zeros(len(frame), dtype=bool)
    weight = np.zeros(len(frame), dtype=float)
    symbols = frame["symbol"].astype(str).to_numpy()
    for symbol in pd.unique(symbols):
        idx = np.flatnonzero(symbols == symbol)
        if idx.size == 0:
            continue
        values = x[idx]
        prev = np.empty(values.shape[0], dtype=float)
        prev[0] = previous.get((column, str(symbol)), np.nan)
        if values.shape[0] > 1:
            prev[1:] = values[:-1]
        valid = np.isfinite(values)
        changed = valid & (~np.isfinite(prev) | (values != prev))
        mask[idx] = changed
        weight[idx] = changed.astype(float)
        previous[(column, str(symbol))] = float(values[-1]) if np.isfinite(values[-1]) else np.nan
    return mask, weight


def _support_mask(
    frame: pd.DataFrame,
    column: str,
    kind: str,
    previous: Dict[Tuple[str, str], float],
) -> Tuple[np.ndarray, np.ndarray]:
    if kind == "clock_changes":
        return _clock_change(frame, column, previous)
    x = _numeric(frame, column).to_numpy(dtype=float)
    if kind == "nonzero":
        mask = np.isfinite(x) & (np.abs(x) > 1e-18)
        return mask, mask.astype(float)
    if kind == "count_sum":
        weight = np.where(np.isfinite(x), np.maximum(x, 0.0), 0.0)
        mask = weight > 0.0
        return mask, weight
    raise ValueError("unsupported support source kind: %s" % kind)


def _regime_signs(
    frame: pd.DataFrame,
    history: Dict[str, List[float]],
    steps: int,
) -> np.ndarray:
    signs = np.zeros(len(frame), dtype=np.int8)
    if "price_fair_value" not in frame.columns:
        return signs
    symbols = frame["symbol"].astype(str).to_numpy()
    price = _numeric(frame, "price_fair_value").to_numpy(dtype=float)
    for symbol in pd.unique(symbols):
        idx = np.flatnonzero(symbols == symbol)
        values = price[idx]
        old = np.asarray(history.get(str(symbol), []), dtype=float)
        combined = np.concatenate([old, values]) if old.size else values.copy()
        offset = old.size
        past = np.full(values.shape[0], np.nan, dtype=float)
        positions = np.arange(offset, offset + values.shape[0]) - int(steps)
        valid_pos = positions >= 0
        if np.any(valid_pos):
            past[valid_pos] = combined[positions[valid_pos]]
        valid = np.isfinite(values) & np.isfinite(past) & (values > 0) & (past > 0)
        local = np.zeros(values.shape[0], dtype=np.int8)
        local[valid & (values > past)] = 1
        local[valid & (values < past)] = -1
        signs[idx] = local
        history[str(symbol)] = combined[-int(steps):].tolist() if steps > 0 else []
    return signs


def _anchor_columns(all_columns: Sequence[str], labs: Sequence[str]) -> Dict[str, Tuple[str, ...]]:
    return {
        str(lab).upper(): tuple(_match_columns(all_columns, LAB_SUPPORT_POLICY[str(lab).upper()]["anchors"])[:12])
        for lab in labs
    }


def _stream_anchor_metrics(
    parts: Sequence[Path],
    by_part: Mapping[str, Sequence[str]],
    anchor_by_lab: Mapping[str, Sequence[str]],
    symbols: Sequence[str],
    progress: Progress,
    batch_size: int = 4,
    max_lag: int = 200,
) -> Dict[Tuple[str, str], Dict[str, object]]:
    ordered = []
    seen = set()
    for columns in anchor_by_lab.values():
        for column in columns:
            if column not in seen:
                seen.add(column)
                ordered.append(column)
    metrics = {}
    for start in range(0, len(ordered), int(batch_size)):
        batch = ordered[start:start + int(batch_size)]
        _progress(progress, "ESS batch %d-%d/%d" % (start + 1, min(start + len(batch), len(ordered)), len(ordered)))
        values = {column: {symbol: [] for symbol in symbols} for column in batch}
        for part in parts:
            available = set(str(x) for x in by_part[str(part)])
            columns = ["asof_ns", "symbol"] + [c for c in batch if c in available]
            frame = pd.read_parquet(part, columns=columns)
            frame = frame.sort_values(["asof_ns", "symbol"], kind="mergesort").reset_index(drop=True)
            sym = frame["symbol"].astype(str)
            for symbol in symbols:
                idx = np.flatnonzero(sym.to_numpy() == str(symbol))
                if idx.size == 0:
                    continue
                for column in batch:
                    if column in frame.columns:
                        arr = pd.to_numeric(frame.iloc[idx][column], errors="coerce").to_numpy(dtype=float)
                    else:
                        arr = np.full(idx.size, np.nan, dtype=float)
                    values[column][str(symbol)].append(arr)
        for column in batch:
            for symbol in symbols:
                chunks = values[column][str(symbol)]
                arr = np.concatenate(chunks) if chunks else np.asarray([], dtype=float)
                n = int(np.isfinite(arr).sum())
                if n < 3:
                    ess = float(n)
                    transitions = 0
                else:
                    series = pd.Series(arr)
                    ess = float(effective_sample_size(series, max_lag=max_lag))
                    previous = series.shift(1)
                    transitions = int((series.notna() & previous.notna() & (series != previous)).sum())
                metrics[(column, str(symbol))] = {
                    "feature": column,
                    "symbol": str(symbol),
                    "n": n,
                    "ess": ess,
                    "ess_ratio": float(ess / n) if n > 0 else 0.0,
                    "transition_count": transitions,
                }
        del values
    return metrics


def _lab_anchor_summary(
    columns: Sequence[str],
    symbols: Sequence[str],
    metrics: Mapping[Tuple[str, str], Mapping[str, object]],
) -> Dict[str, object]:
    rows = []
    ess_values = []
    for column in columns:
        for symbol in symbols:
            row = dict(metrics.get((column, str(symbol))) or {
                "feature": column,
                "symbol": str(symbol),
                "n": 0,
                "ess": 0.0,
                "ess_ratio": 0.0,
                "transition_count": 0,
            })
            rows.append(row)
            ess = float(row["ess"])
            if math.isfinite(ess):
                ess_values.append(ess)
    return {
        "anchor_columns": list(columns),
        "feature_symbol_pairs": rows,
        "median_ess": float(np.median(ess_values)) if ess_values else 0.0,
        "min_ess": float(np.min(ess_values)) if ess_values else 0.0,
        "pairs_ess_ge_200": int(sum(v >= 200.0 for v in ess_values)),
        "pair_count": int(len(rows)),
    }


def run_streaming_mechanism_support_audit(
    root: str,
    provenance: Mapping[str, object],
    registry: LabRegistry,
    labs: Sequence[str] = DEFAULT_LABS,
    progress: Progress = None,
) -> Dict[str, object]:
    path = Path(root)
    if not path.is_dir():
        raise ValueError("streaming support audit requires a parquet directory")
    selected = tuple(str(x).upper() for x in labs)
    parts, all_columns, by_part = parquet_union_schema(str(path))
    projection = support_projection_columns(all_columns, selected, registry)
    readiness_columns = _readiness_candidate_columns(all_columns, selected, registry)

    declared = dict(provenance.get("features") or {})
    frame_features = tuple(c for c in all_columns if not _is_provenance_metadata(c))
    undeclared = tuple(sorted(c for c in frame_features if c not in declared))
    if undeclared:
        raise ValueError("streaming provenance schema audit failed; undeclared=%s" % (undeclared[:20],))
    missing_declared_clocks = set()
    for feature in projection:
        spec = declared.get(feature) or {}
        for clock in spec.get("governing_clocks") or ():
            if str(clock) not in all_columns:
                missing_declared_clocks.add(str(clock))
    if missing_declared_clocks:
        raise ValueError("streaming provenance missing governing clocks: %s" % sorted(missing_declared_clocks))

    group_state = {}
    for lab_id in selected:
        policy = LAB_SUPPORT_POLICY[lab_id]
        group_state[lab_id] = [
            _group_template(spec, _match_columns(all_columns, list(spec["patterns"])))
            for spec in policy["groups"]
        ]

    # Small first pass: chronology/cadence metadata only.
    unique_asof_chunks = []
    start_ns = None
    stop_ns = None
    total_rows = 0
    for part in parts:
        meta = pd.read_parquet(part, columns=["asof_ns", "symbol"])
        total_rows += int(len(meta))
        asof = pd.to_numeric(meta["asof_ns"], errors="coerce").dropna().astype(np.int64)
        if not asof.empty:
            lo = int(asof.min())
            hi = int(asof.max())
            start_ns = lo if start_ns is None else min(start_ns, lo)
            stop_ns = hi if stop_ns is None else max(stop_ns, hi)
            unique_asof_chunks.append(np.sort(asof.unique()))
    if total_rows <= 0 or start_ns is None or stop_ns is None:
        raise ValueError("empty streaming support tensor")
    unique_asof = np.unique(np.concatenate(unique_asof_chunks)) if unique_asof_chunks else np.asarray([], dtype=np.int64)
    if unique_asof.size < 2:
        raise ValueError("cannot infer cadence from fewer than two timestamps")
    cadence_ns = int(np.median(np.diff(unique_asof)))
    if cadence_ns <= 0:
        raise ValueError("invalid inferred cadence")
    cadence_ms = max(1, int(round(cadence_ns / 1e6)))
    del unique_asof_chunks, unique_asof

    _progress(progress, "streaming %d rows across %d parts; projection=%d/%d columns" % (total_rows, len(parts), len(projection), len(all_columns)))

    nonnull = defaultdict(int)
    active = defaultdict(int)
    clock_nonnull = defaultdict(int)
    future_violations = 0
    duplicate_keys = 0
    nonmonotonic_asof = 0
    last_asof_by_symbol = {}
    previous_clock_value = {}
    symbols_seen = set()
    lab_event_rows = defaultdict(int)
    lab_thirds = {lab: [0, 0, 0] for lab in selected}
    lab_regimes = {lab: [0, 0, 0] for lab in selected}  # up, down, flat/unknown-zero
    price_history = {}
    regime_steps = max(1, int(round(10000.0 / float(cadence_ms))))
    governing_clock_active = defaultdict(int)

    for part_index, part in enumerate(parts, 1):
        available = by_part[str(part)]
        frame = _read_part(part, projection, available)
        _progress(progress, "part %d/%d rows=%d columns=%d" % (part_index, len(parts), len(frame), len(frame.columns)))
        symbols = frame["symbol"].astype(str)
        symbols_seen.update(symbols.dropna().unique())
        asof = pd.to_numeric(frame["asof_ns"], errors="coerce").to_numpy(dtype=float)

        # Structural chronology and duplicate-key check remain bounded to one chunk.
        duplicate_keys += int(frame.duplicated(["asof_ns", "symbol"], keep=False).sum())
        for symbol, group in frame.groupby("symbol", sort=False):
            values = pd.to_numeric(group["asof_ns"], errors="coerce").to_numpy(dtype=float)
            if values.size == 0:
                continue
            nonmonotonic_asof += int(np.sum(np.diff(values) < 0))
            previous = last_asof_by_symbol.get(str(symbol))
            if previous is not None and np.isfinite(values[0]) and float(values[0]) < float(previous):
                nonmonotonic_asof += 1
            if previous is not None and np.isfinite(values[0]) and float(values[0]) == float(previous):
                duplicate_keys += 2
            last_asof_by_symbol[str(symbol)] = float(values[-1])

        # Full clock audit, but only the clock columns are materialized.
        for column in projection:
            if not str(column).endswith(AUDIT_CLOCK_SUFFIXES):
                continue
            x = _numeric(frame, column)
            clock_nonnull[column] += int(x.notna().sum())
            mask = x.notna() & pd.Series(np.isfinite(asof), index=frame.index)
            if bool(mask.any()):
                future_violations += int((x[mask].to_numpy(dtype=float) > asof[mask.to_numpy()]).sum())

        # Readiness statistics are exact across sparse chunks without concatenation.
        for column in readiness_columns:
            nonnull[column] += _nonnull_count(frame, column)
            active[column] += _active_count(frame, column)

        # Track projected feature activity so governing clocks can be validated.
        for feature in projection:
            if feature in META_COLUMNS or str(feature).endswith(AUDIT_CLOCK_SUFFIXES):
                continue
            n = _nonnull_count(frame, feature)
            if n <= 0:
                continue
            spec = declared.get(feature) or {}
            for clock in spec.get("governing_clocks") or ():
                governing_clock_active[str(clock)] += n

        regime = _regime_signs(frame, price_history, regime_steps)
        part_lab_masks = {lab: np.zeros(len(frame), dtype=bool) for lab in selected}
        sym_arr = symbols.to_numpy()

        for lab_id in selected:
            for group in group_state[lab_id]:
                spec = group["spec"]
                kind = str(spec["kind"])
                union = np.zeros(len(frame), dtype=bool)
                for column in group["columns"]:
                    mask, weight = _support_mask(frame, column, kind, previous_clock_value)
                    union |= mask
                    count = float(np.sum(weight))
                    group["events_total"] += count
                    group["events_by_column"][column] += count
                    venue = _venue_from_column(column)
                    group["events_by_venue"][venue] += count
                    if count > 0:
                        for symbol in pd.unique(sym_arr):
                            idx = sym_arr == symbol
                            value = float(np.sum(weight[idx]))
                            if value > 0:
                                group["events_by_symbol"][str(symbol)] += value
                group["event_rows"] += int(np.sum(union))
                part_lab_masks[lab_id] |= union

        span = max(1, int(stop_ns) - int(start_ns) + 1)
        thirds = np.clip(((asof - float(start_ns)) * 3.0 // float(span)).astype(np.int8), 0, 2)
        for lab_id in selected:
            event_mask = part_lab_masks[lab_id]
            lab_event_rows[lab_id] += int(np.sum(event_mask))
            for i in range(3):
                lab_thirds[lab_id][i] += int(np.sum(event_mask & (thirds == i)))
            lab_regimes[lab_id][0] += int(np.sum(event_mask & (regime > 0)))
            lab_regimes[lab_id][1] += int(np.sum(event_mask & (regime < 0)))
            lab_regimes[lab_id][2] += int(np.sum(event_mask & (regime == 0)))
        del frame, part_lab_masks, regime

    if future_violations or nonmonotonic_asof or duplicate_keys:
        raise ValueError(
            "streaming PIT audit failed: future=%d nonmonotonic=%d duplicates=%d"
            % (future_violations, nonmonotonic_asof, duplicate_keys)
        )
    checked_clocks = tuple(c for c in projection if str(c).endswith(AUDIT_CLOCK_SUFFIXES))
    if not checked_clocks:
        raise ValueError("streaming PIT audit has no availability/receive clocks")

    # Only projected active features matter to this target-free audit. Their
    # declared governing clocks must have observations somewhere in the tape.
    empty_required_clocks = sorted(
        clock for clock, activity_rows in governing_clock_active.items()
        if activity_rows > 0 and int(clock_nonnull.get(clock, 0)) <= 0
    )
    if empty_required_clocks:
        raise ValueError("streaming provenance has active features with empty clocks: %s" % empty_required_clocks)

    symbols_sorted = sorted(str(x) for x in symbols_seen)
    readiness = _build_readiness(
        all_columns,
        selected,
        registry,
        total_rows,
        symbols_sorted,
        nonnull,
        active,
    )

    anchor_by_lab = _anchor_columns(all_columns, selected)
    anchor_metrics = _stream_anchor_metrics(parts, by_part, anchor_by_lab, symbols_sorted, progress)

    result_labs = {}
    for lab_id in selected:
        groups = []
        for state in group_state[lab_id]:
            spec = state["spec"]
            venue_counts = dict(state["events_by_venue"])
            symbol_counts = dict(state["events_by_symbol"])
            active_symbols = [k for k, v in symbol_counts.items() if float(v) > 0]
            active_venues = [k for k, v in venue_counts.items() if k != "cross" and float(v) > 0]
            passed = bool(
                state["columns"]
                and float(state["events_total"]) >= float(spec["min_total"])
                and len(active_symbols) >= int(spec["min_symbols"])
                and len(active_venues) >= int(spec["min_venues"])
            )
            groups.append({
                "name": str(spec["name"]),
                "kind": str(spec["kind"]),
                "matched_columns": list(state["columns"]),
                "events_total": float(state["events_total"]),
                "event_rows": int(state["event_rows"]),
                "events_by_symbol": dict(sorted(symbol_counts.items())),
                "events_by_venue": dict(sorted(venue_counts.items())),
                "effective_symbols": _effective_count(symbol_counts),
                "effective_venues": _effective_count({k: v for k, v in venue_counts.items() if k != "cross"}),
                "min_total": int(spec["min_total"]),
                "min_symbols": int(spec["min_symbols"]),
                "min_venues": int(spec["min_venues"]),
                "pass": passed,
            })

        third_values = lab_thirds[lab_id]
        regime_values = lab_regimes[lab_id]
        diversity = {
            "chronological_thirds_event_rows": {str(i + 1): int(third_values[i]) for i in range(3)},
            "all_thirds_present": all(int(v) > 0 for v in third_values),
            "past_10s_up_event_rows": int(regime_values[0]),
            "past_10s_down_event_rows": int(regime_values[1]),
            "past_10s_flat_event_rows": int(regime_values[2]),
            "both_direction_regimes_present": int(regime_values[0]) > 0 and int(regime_values[1]) > 0,
        }
        anchors = _lab_anchor_summary(anchor_by_lab[lab_id], symbols_sorted, anchor_metrics)
        ready = bool(readiness[lab_id]["ready"])
        groups_pass = bool(groups) and all(bool(x["pass"]) for x in groups)
        ess_ok = anchors["pair_count"] > 0 and float(anchors["median_ess"]) >= 200.0
        diversity_ok = bool(diversity["all_thirds_present"] and diversity["both_direction_regimes_present"])
        if not ready:
            verdict, budget = "BLOCKED", 0
        elif not groups_pass:
            verdict, budget = "THIN_SUPPORT", 0
        elif ess_ok and diversity_ok:
            verdict, budget = "STRONG_SUPPORT", 8
        else:
            verdict, budget = "ADEQUATE_SUPPORT", 4
        result_labs[lab_id] = {
            "data_ready": ready,
            "support_groups": groups,
            "independent_event_rows": int(lab_event_rows[lab_id]),
            "diversity": diversity,
            "anchors": anchors,
            "support_verdict": verdict,
            "recommended_max_hypothesis_tests": int(budget),
            "groups_pass": groups_pass,
            "anchor_ess_ok": ess_ok,
            "diversity_ok": diversity_ok,
        }

    load_report = {
        "mode": "parquet_partition_streaming",
        "parts": int(len(parts)),
        "logical_columns": int(len(all_columns)),
        "loaded_columns": int(len(projection)),
        "pruned_columns": int(len(all_columns) - len(projection)),
        "max_resident_parts": 1,
        "ess_anchor_batch_size": 4,
        "full_frame_concat": False,
    }
    payload = {
        "policy_version": SUPPORT_POLICY_VERSION,
        "policy_digest": support_policy_digest(),
        "target_free": True,
        "target_columns_used": [],
        "cadence_ms": int(cadence_ms),
        "rows": int(total_rows),
        "symbols": symbols_sorted,
        "labs": result_labs,
        "strong_support_labs": [k for k, v in result_labs.items() if v["support_verdict"] == "STRONG_SUPPORT"],
        "adequate_support_labs": [k for k, v in result_labs.items() if v["support_verdict"] == "ADEQUATE_SUPPORT"],
        "thin_or_blocked_labs": [k for k, v in result_labs.items() if v["support_verdict"] in {"THIN_SUPPORT", "BLOCKED"}],
        "scientific_boundary": "support audit uses no future target and does not constitute alpha evidence; it only caps the amount of subsequent hypothesis testing",
        "feature_provenance_digest": str(provenance.get("manifest_digest") or ""),
        "pit_proof_level": "FULL_FEATURE_PROVENANCE",
        "load_report": load_report,
    }
    digest_body = dict(payload)
    digest_body.pop("feature_provenance_digest", None)
    digest_body.pop("pit_proof_level", None)
    digest_body.pop("load_report", None)
    payload["audit_digest"] = _canonical_digest(digest_body)
    return payload
