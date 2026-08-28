#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.alpha_discovery_v31.pipeline import (  # noqa: E402
    FEATURE_GROUPS, add_costs, common_mask, context, cost, enrich,
    require_unique_columns, unique_names,
)
from research.alpha_discovery_v32.pipeline import (  # noqa: E402
    DEFAULT_SELECTION_QUANTILE, DEV_SYMBOLS, MODEL_PARAMS, choose_dev_candidate,
    fit_month, make_month_fold, month_sequence, summarize_months,
)
from scripts.run_alpha_discovery_v3 import load_symbol_frame  # noqa: E402
from data_v2.events.residuals import (  # noqa: E402
    BETA_WINDOW_BARS, _causal_2factor_betas, _freeze_daily,
)

PANEL_DIR = ROOT / "data_v2/normalized/event_feature_panel/venue=binance"
IM_PATH = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_DIR = ROOT / "reports/alpha_discovery_v32"
PIPELINE_PATH = ROOT / "research/alpha_discovery_v32/pipeline.py"
RUNNER_PATH = ROOT / "scripts/run_alpha_discovery_v32.py"
FREEZE_PATH = OUT_DIR / "FREEZE.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:
        return "UNKNOWN"


def betas_for_symbol(frame: pd.DataFrame, btc: pd.DataFrame, eth: pd.DataFrame, symbol: str):
    if symbol in ("BTCUSDT", "ETHUSDT"):
        return pd.Series(0.0, index=frame.index), pd.Series(0.0, index=frame.index)
    idx = pd.DatetimeIndex(pd.to_datetime(frame["timestamp"], utc=True))
    close = pd.to_numeric(frame["close"], errors="coerce")
    y = pd.Series(np.log(close / close.shift(12)).to_numpy(), index=idx)

    bidx = pd.DatetimeIndex(pd.to_datetime(btc["timestamp"], utc=True))
    eidx = pd.DatetimeIndex(pd.to_datetime(eth["timestamp"], utc=True))
    bc = pd.to_numeric(btc["close"], errors="coerce")
    ec = pd.to_numeric(eth["close"], errors="coerce")
    br = pd.Series(np.log(bc / bc.shift(12)).to_numpy(), index=bidx).reindex(idx)
    er = pd.Series(np.log(ec / ec.shift(12)).to_numpy(), index=eidx).reindex(idx)

    b1, b2 = _causal_2factor_betas(y, br, er, BETA_WINDOW_BARS, BETA_WINDOW_BARS)
    return _freeze_daily(b1).reset_index(drop=True), _freeze_daily(b2).reset_index(drop=True)


def add_hedged_costs(
    enriched: pd.DataFrame,
    raw: pd.DataFrame,
    symbol: str,
    ticks: Dict[str, float],
    btc: pd.DataFrame,
    eth: pd.DataFrame,
) -> pd.DataFrame:
    out = add_costs(enriched, ticks.get(symbol))
    if symbol in ("BTCUSDT", "ETHUSDT"):
        out["beta_btc"] = 0.0
        out["beta_eth"] = 0.0
        out["hedge_gross_notional"] = 1.0
        return out

    b1, b2 = betas_for_symbol(raw, btc, eth, symbol)
    b1 = b1.reindex(out.index).fillna(0).abs()
    b2 = b2.reindex(out.index).fillna(0).abs()

    btc_by_ts = btc.set_index("timestamp")
    eth_by_ts = eth.set_index("timestamp")
    ts = pd.to_datetime(out["timestamp"], utc=True)
    bclose = pd.Series(ts.map(btc_by_ts["close"]), index=out.index)
    eclose = pd.Series(ts.map(eth_by_ts["close"]), index=out.index)
    bopen = pd.Series(ts.map(btc_by_ts["open"].shift(-1)), index=out.index)
    eopen = pd.Series(ts.map(eth_by_ts["open"].shift(-1)), index=out.index)

    out["decision_cost_x1"] = (
        out["decision_cost_x1"]
        + b1 * cost(bclose, ticks.get("BTCUSDT"))
        + b2 * cost(eclose, ticks.get("ETHUSDT"))
    )
    out["realized_cost_x1"] = (
        out["realized_cost_x1"]
        + b1 * cost(bopen, ticks.get("BTCUSDT"))
        + b2 * cost(eopen, ticks.get("ETHUSDT"))
    )
    out["decision_cost_x2"] = 2 * out["decision_cost_x1"]
    out["realized_cost_x2"] = 2 * out["realized_cost_x1"]
    out["beta_btc"] = b1
    out["beta_eth"] = b2
    out["hedge_gross_notional"] = 1 + b1 + b2
    return out


def all_panel_symbols() -> List[str]:
    return sorted(p.name.split("=", 1)[1] for p in PANEL_DIR.glob("symbol=*") if p.is_dir())


def build_dataset(
    symbols: List[str],
    groups: List[str],
    ticks: Dict[str, float],
    background_hours: int,
    stress_threshold: float,
) -> pd.DataFrame:
    btc0 = load_symbol_frame("BTCUSDT")
    eth0 = load_symbol_frame("ETHUSDT")
    if btc0 is None or eth0 is None:
        raise SystemExit("BTC/ETH context missing")
    btc = enrich(btc0)
    eth = enrich(eth0)
    ctx = context(btc, eth)

    selected_features = []
    for group in groups:
        selected_features.extend(FEATURE_GROUPS[group])
    feats = sorted(set(selected_features))
    parts = []

    for i, symbol in enumerate(symbols, 1):
        raw = load_symbol_frame(symbol)
        if raw is None or raw.empty:
            print("[%3d/%d] %-14s SKIP no panel" % (i, len(symbols), symbol), flush=True)
            continue
        e = enrich(raw)
        e = e.merge(ctx, on="timestamp", how="left", validate="many_to_one")
        e = add_hedged_costs(e, raw, symbol, ticks, btc0, eth0)
        mask = common_mask(e, stress_threshold, background_hours)
        base = [
            "timestamp", "target_residual_ret_1h", "target_standardized_1h",
            "ex_ante_sigma_1h", "decision_cost_x1", "decision_cost_x2",
            "realized_cost_x1", "realized_cost_x2", "beta_btc", "beta_eth",
            "hedge_gross_notional",
        ]
        cols = unique_names(base + feats)
        cols = [c for c in cols if c in e.columns]
        sample = e.loc[mask, cols].copy()
        require_unique_columns(sample, "%s V3.2 sample" % symbol)
        sample["symbol"] = symbol
        parts.append(sample)
        print(
            "[%3d/%d] %-14s rows=%8s candidates=%7s" %
            (i, len(symbols), symbol, format(len(e), ","), format(len(sample), ",")),
            flush=True,
        )

    if not parts:
        raise SystemExit("No candidate rows")
    out = pd.concat(parts, ignore_index=True).sort_values("timestamp").reset_index(drop=True)
    require_unique_columns(out, "V3.2 combined dataset")
    return out


def yearly_summary(months: List[dict]) -> Dict[str, dict]:
    out = {}
    for year in sorted(set(str(m.get("test_month"))[:4] for m in months if m.get("test_month"))):
        out[year] = summarize_months([m for m in months if str(m.get("test_month", "")).startswith(year)])
    return out


def run_groups(
    dataset: pd.DataFrame,
    groups: List[str],
    start_month: str,
    end_month: str,
    selection_quantile: float,
    max_train_rows: int,
    max_calib_rows: int,
    max_test_rows: int,
) -> Dict[str, dict]:
    results = {}
    months = month_sequence(start_month, end_month)
    for group in groups:
        rows = []
        for month in months:
            fold = make_month_fold(dataset["timestamp"], month)
            r = fit_month(
                dataset, FEATURE_GROUPS[group], fold,
                selection_quantile=selection_quantile,
                max_train_rows=max_train_rows,
                max_calib_rows=max_calib_rows,
                max_test_rows=max_test_rows,
            )
            rows.append(r)
            print(
                group, month, r.get("status"), "enabled=", r.get("enabled_by_calibration"),
                "IC=", r.get("ic_spearman"), "N=", r.get("n"),
                "netx1=", r.get("net_x1_mean"), "netx2=", r.get("net_x2_mean"),
                flush=True,
            )
        results[group] = {
            "months": rows,
            "yearly": yearly_summary(rows),
            "summary": summarize_months(rows),
        }
    return results


def protocol_payload(groups: List[str], start_month: str, end_month: str, q: float) -> dict:
    return {
        "version": "3.2",
        "dev_symbols": list(DEV_SYMBOLS),
        "target": "next-bar residual 1h standardized by strict-prior 7d volatility",
        "sampling": "V3.1 A-only fixed cadence + first core stress crossing",
        "walk_forward": "monthly; prior 24m fit + prior 120d calibration split 50/50 + 8h embargo",
        "model": "direction classifier + separate positive/negative conditional magnitude regressors",
        "calibration": "logistic direction calibration + sign-specific isotonic magnitude calibration",
        "selection": "fixed from second calibration half; P90 among positive expected net x1 edges; test distribution never sets threshold",
        "calibration_gate": "month disabled unless calibration selection has >=40 trades, positive net x1 mean and PF x1 > 1",
        "costs": "main leg + abs(beta) BTC/ETH hedge legs; decision-time x1 for selection, realized next-open x1/x2 for evaluation",
        "feature_groups": {g: FEATURE_GROUPS[g] for g in groups},
        "model_params": MODEL_PARAMS,
        "selection_quantile": q,
        "test_months": [start_month, end_month],
        "dev_candidate_gate": {
            "months_ok_min": 30,
            "months_enabled_min": 12,
            "selected_trades_min": 500,
            "pooled_net_x1": ">0",
            "pooled_net_x2": ">0",
            "median_pf_x2": ">=1.15",
            "positive_net_x2_month_share": ">=0.60",
            "median_ic": ">0",
            "median_brier_improvement": ">=0",
            "median_max_dev_symbol_share": "<=0.80",
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=lambda x: None if isinstance(x, float) and not np.isfinite(x) else x))


def freeze_dev(dev_results_path: Path, freeze_path: Path) -> None:
    if not dev_results_path.exists():
        raise SystemExit("DEV results missing: %s" % dev_results_path)
    dev = json.loads(dev_results_path.read_text())
    if dev.get("mode") != "dev" or dev.get("protocol", {}).get("version") != "3.2":
        raise SystemExit("Not a V3.2 DEV result")
    if sorted(dev.get("dataset", {}).get("symbols_list", [])) != sorted(DEV_SYMBOLS):
        raise SystemExit("DEV freeze requires exactly BTCUSDT, ETHUSDT, SOLUSDT")
    summaries = {g: v["summary"] for g, v in dev["groups"].items()}
    decision = choose_dev_candidate(summaries)
    payload = {
        "version": "3.2",
        "status": decision["status"],
        "selected_group": decision["selected_group"],
        "reasons": decision["reasons"],
        "git_sha": git_sha(),
        "pipeline_sha256": sha256_file(PIPELINE_PATH),
        "runner_sha256": sha256_file(RUNNER_PATH),
        "dev_results_sha256": sha256_file(dev_results_path),
        "dev_results_path": str(dev_results_path.relative_to(ROOT)),
        "dev_symbols": list(DEV_SYMBOLS),
        "protocol": dev["protocol"],
    }
    write_json(freeze_path, payload)
    print("Wrote", freeze_path)
    print("V3.2 freeze status:", payload["status"], "selected_group=", payload["selected_group"])


def verify_holdout_freeze(freeze_path: Path) -> dict:
    if not freeze_path.exists():
        raise SystemExit("HOLDOUT forbidden: V3.2 FREEZE.json missing")
    freeze = json.loads(freeze_path.read_text())
    if freeze.get("status") != "CANDIDATE" or not freeze.get("selected_group"):
        raise SystemExit("HOLDOUT forbidden: DEV freeze did not produce a CANDIDATE")
    if freeze.get("pipeline_sha256") != sha256_file(PIPELINE_PATH):
        raise SystemExit("HOLDOUT forbidden: pipeline changed after DEV freeze")
    if freeze.get("runner_sha256") != sha256_file(RUNNER_PATH):
        raise SystemExit("HOLDOUT forbidden: runner changed after DEV freeze")
    return freeze


def main() -> None:
    ap = argparse.ArgumentParser(description="Alpha Discovery V3.2")
    ap.add_argument("--mode", choices=["dev", "freeze", "holdout"], default="dev")
    ap.add_argument("--symbols", default=None, help="DEV only; comma-separated subset of BTC/ETH/SOL")
    ap.add_argument("--groups", default="A_CORE,B_NORMALIZED,C_STATE")
    ap.add_argument("--start-month", default="2023-01")
    ap.add_argument("--end-month", default="2026-07")
    ap.add_argument("--background-hours", type=int, default=4)
    ap.add_argument("--stress-threshold", type=float, default=2.0)
    ap.add_argument("--selection-quantile", type=float, default=DEFAULT_SELECTION_QUANTILE)
    ap.add_argument("--max-train-rows", type=int, default=500000)
    ap.add_argument("--max-calib-rows", type=int, default=150000)
    ap.add_argument("--max-test-rows", type=int, default=250000)
    ap.add_argument("--dev-results", default=str(OUT_DIR / "DEV_RESULTS.json"))
    ap.add_argument("--freeze-path", default=str(FREEZE_PATH))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.mode == "freeze":
        freeze_dev(Path(args.dev_results), Path(args.freeze_path))
        return

    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    unknown = [g for g in groups if g not in FEATURE_GROUPS]
    if unknown:
        raise SystemExit("Unknown groups: %s" % unknown)

    all_symbols = all_panel_symbols()
    im = pd.read_parquet(IM_PATH)
    ticks = dict(zip(im["symbol"], pd.to_numeric(im["tick_size"], errors="coerce")))

    freeze = None
    if args.mode == "dev":
        if args.symbols:
            symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
            if any(s not in DEV_SYMBOLS for s in symbols):
                raise SystemExit("DEV mode may only use BTCUSDT, ETHUSDT, SOLUSDT")
        else:
            symbols = list(DEV_SYMBOLS)
    else:
        if args.symbols:
            raise SystemExit("HOLDOUT mode forbids --symbols cherry-picking")
        freeze = verify_holdout_freeze(Path(args.freeze_path))
        groups = [freeze["selected_group"]]
        symbols = [s for s in all_symbols if s not in DEV_SYMBOLS]
        out_path = Path(args.out or str(OUT_DIR / "HOLDOUT_RESULTS.json"))
        if out_path.exists():
            raise SystemExit("HOLDOUT one-shot result already exists: %s" % out_path)

    dataset = build_dataset(symbols, groups, ticks, args.background_hours, args.stress_threshold)
    result = {
        "mode": args.mode,
        "git_sha": git_sha(),
        "protocol": protocol_payload(groups, args.start_month, args.end_month, args.selection_quantile),
        "dataset": {
            "rows": len(dataset),
            "symbols": int(dataset["symbol"].nunique()),
            "symbols_list": sorted(dataset["symbol"].unique().tolist()),
            "start": str(dataset["timestamp"].min()),
            "end": str(dataset["timestamp"].max()),
        },
        "groups": run_groups(
            dataset, groups, args.start_month, args.end_month, args.selection_quantile,
            args.max_train_rows, args.max_calib_rows, args.max_test_rows,
        ),
    }
    if args.mode == "dev":
        summaries = {g: result["groups"][g]["summary"] for g in groups}
        result["candidate_gate_preview"] = choose_dev_candidate(summaries)
        out_path = Path(args.out or args.dev_results)
    else:
        result["freeze_sha256"] = sha256_file(Path(args.freeze_path))
        result["frozen_selected_group"] = freeze["selected_group"]
        out_path = Path(args.out or str(OUT_DIR / "HOLDOUT_RESULTS.json"))

    write_json(out_path, result)
    print("Wrote", out_path)


if __name__ == "__main__":
    main()
