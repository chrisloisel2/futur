from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Sequence

import numpy as np
import pandas as pd

from .information_audit import block_shuffle_null, effective_sample_size, spearman_ic
from .phase5_audit import DEFAULT_VENUES, prepare_features
from .phase5_mechanism import _decile_response, _loo_targets, _partial_spearman, _regime_ics, _third_ics

LOCKED_FEATURE = "okx__queue_imbalance_l5"
LOCKED_HORIZON_MS = 30000
LOCKED_SIGN = 1
PRIMARY_SYMBOLS = ("BTCUSDT", "ETHUSDT")
SUPPORT_SYMBOLS = ("SOLUSDT",)
DISCOVERY_STOP_NS = 1786852443241777168

GATES = {
    "min_n": 10000,
    "min_ess": 400.0,
    "min_loo_ic": 0.05,
    "min_loo_partial_ic": 0.03,
    "min_partial_retention": 0.50,
    "min_positive_thirds": 3,
    "min_positive_regimes": 2,
    "max_block_p": 0.05,
    "min_top_minus_bottom_bps": 0.0,
}


def _positive_count(values) -> int:
    return int(sum(1 for value in values if np.isfinite(value) and float(value) > 0.0))


def run_locked_confirmation(
    frame: pd.DataFrame,
    cadence_ms: int = 100,
    venues: Sequence[str] = DEFAULT_VENUES,
    min_duration_hours: float = 12.0,
    discovery_stop_ns: int = DISCOVERY_STOP_NS,
    block_shuffle_repeats: int = 100,
    progress: bool = False,
) -> Dict[str, object]:
    if frame.empty:
        raise ValueError("empty confirmation tape")
    if int(cadence_ms) != 100:
        raise ValueError("Phase 5.2 cadence is locked to 100ms")

    first_ns = int(frame["asof_ns"].min())
    last_ns = int(frame["asof_ns"].max())
    duration_hours = float((last_ns - first_ns) / 3.6e12)
    if first_ns <= int(discovery_stop_ns):
        raise ValueError("confirmation tape overlaps or predates DEV discovery window")
    if duration_hours < float(min_duration_hours):
        raise ValueError(
            "confirmation tape duration %.3fh < preregistered %.3fh minimum"
            % (duration_hours, min_duration_hours)
        )

    prepared, registry = prepare_features(frame, venues=venues)
    if LOCKED_FEATURE not in registry:
        raise ValueError("locked feature %s is unavailable" % LOCKED_FEATURE)

    all_symbols = tuple(PRIMARY_SYMBOLS) + tuple(SUPPORT_SYMBOLS)
    rows = []
    for symbol in all_symbols:
        group = prepared[prepared["symbol"] == symbol].sort_values("asof_ns").reset_index(drop=True)
        if group.empty:
            raise ValueError("missing locked symbol %s" % symbol)
        if progress:
            print("[phase5.2] %s rows=%s" % (symbol, len(group)), flush=True)

        x = pd.to_numeric(group[LOCKED_FEATURE], errors="coerce")
        loo_y, loo_past = _loo_targets(
            group,
            excluded_venue="okx",
            horizon_ms=LOCKED_HORIZON_MS,
            cadence_ms=cadence_ms,
            venues=venues,
        )
        valid = x.notna() & loo_y.notna()
        n = int(valid.sum())
        loo_ic = spearman_ic(x, loo_y)
        loo_partial_ic = _partial_spearman(x, loo_y, loo_past)
        retention = (
            float(abs(loo_partial_ic) / abs(loo_ic))
            if np.isfinite(loo_partial_ic) and np.isfinite(loo_ic) and abs(loo_ic) > 1e-12
            else float("nan")
        )
        feature_ess = effective_sample_size(x, max_lag=200)
        target_ess = effective_sample_size(loo_y, max_lag=200)
        ess = float(min(float(feature_ess), float(target_ess), float(n)))
        t1, t2, t3 = _third_ics(x, loo_y)
        up_ic, down_ic, up_n, down_n = _regime_ics(x, loo_y, loo_past)
        decile = _decile_response(x, loo_y)

        block_steps = max(
            1,
            int(max(30000, 10 * LOCKED_HORIZON_MS) // int(cadence_ms)),
        )
        null = block_shuffle_null(
            x,
            loo_y,
            block=block_steps,
            repeats=int(block_shuffle_repeats),
            seed=1701 + len(rows),
        )
        block_p = float(null["p_two_sided"])
        positive_thirds = _positive_count((t1, t2, t3))
        positive_regimes = _positive_count((up_ic, down_ic))

        checks = {
            "n": n >= int(GATES["min_n"]),
            "ess": ess >= float(GATES["min_ess"]),
            "loo_ic": np.isfinite(loo_ic) and loo_ic >= float(GATES["min_loo_ic"]),
            "loo_partial_ic": np.isfinite(loo_partial_ic)
            and loo_partial_ic >= float(GATES["min_loo_partial_ic"]),
            "partial_retention": np.isfinite(retention)
            and retention >= float(GATES["min_partial_retention"]),
            "thirds": positive_thirds >= int(GATES["min_positive_thirds"]),
            "regimes": positive_regimes >= int(GATES["min_positive_regimes"]),
            "block_p": np.isfinite(block_p) and block_p <= float(GATES["max_block_p"]),
            "decile": np.isfinite(decile["top_minus_bottom_bps"])
            and decile["top_minus_bottom_bps"] > float(GATES["min_top_minus_bottom_bps"]),
        }
        symbol_pass = bool(all(checks.values()))

        row = {
            "symbol": symbol,
            "role": "PRIMARY" if symbol in PRIMARY_SYMBOLS else "SUPPORT",
            "feature": LOCKED_FEATURE,
            "horizon_ms": LOCKED_HORIZON_MS,
            "expected_sign": LOCKED_SIGN,
            "n": n,
            "ess": ess,
            "loo_ic": float(loo_ic),
            "loo_partial_ic": float(loo_partial_ic),
            "partial_retention": retention,
            "third1_ic": float(t1),
            "third2_ic": float(t2),
            "third3_ic": float(t3),
            "positive_thirds": positive_thirds,
            "past_up_ic": float(up_ic),
            "past_down_ic": float(down_ic),
            "past_up_n": int(up_n),
            "past_down_n": int(down_n),
            "positive_regimes": positive_regimes,
            "block_p": block_p,
            "top_minus_bottom_bps": float(decile["top_minus_bottom_bps"]),
            "symbol_pass": symbol_pass,
            "failed_gates": ",".join(k for k, ok in checks.items() if not ok) if not symbol_pass else "NONE",
        }
        rows.append(row)

    results = pd.DataFrame(rows)
    primary = results[results["role"] == "PRIMARY"]
    confirmed = bool(len(primary) == len(PRIMARY_SYMBOLS) and primary["symbol_pass"].all())
    verdict = "CONFIRMED_INFORMATION_CANDIDATE" if confirmed else "NOT_CONFIRMED"
    summary = {
        "verdict": verdict,
        "feature": LOCKED_FEATURE,
        "horizon_ms": LOCKED_HORIZON_MS,
        "expected_sign": LOCKED_SIGN,
        "primary_symbols": list(PRIMARY_SYMBOLS),
        "support_symbols": list(SUPPORT_SYMBOLS),
        "duration_hours": duration_hours,
        "first_asof_ns": first_ns,
        "last_asof_ns": last_ns,
        "discovery_stop_ns": int(discovery_stop_ns),
        "independent_window": bool(first_ns > int(discovery_stop_ns)),
        "gates": dict(GATES),
        "primary_passes": int(primary["symbol_pass"].sum()),
        "primary_required": int(len(PRIMARY_SYMBOLS)),
        "economic_claim": False,
    }
    return {"summary": summary, "symbols": results}


def write_locked_confirmation(result: Dict[str, object], out_dir: str) -> Dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "SUMMARY.json"
    symbols_path = out / "symbol_confirmation.csv"
    summary_path.write_text(json.dumps(result["summary"], indent=2, sort_keys=True))
    result["symbols"].to_csv(symbols_path, index=False)
    return {"summary": str(summary_path), "symbols": str(symbols_path)}
