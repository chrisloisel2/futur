#!/usr/bin/env python3
"""
scripts/run_stress_gate_dispersion_v2_primary_test.py
─────────────────────────────────────────────────────────────────────────────
Phase 2 — UN SEUL run primaire préenregistré (research/edge_factory/
basis_dispersion/stress_gate_dispersion_v2/PREREGISTRATION.md, amendement
septies). Contrat : préflight hash -> reçu d'unblinding -> cibles 24h ->
seuil causal groupé (asset x intervalle) -> classification stress/non-stress
-> effet primaire -> inférence préenregistrée -> artefact PRIMARY_RESULT
immuable -> arrêt. Pas de leave-one-out, pas de seuil alternatif, pas
d'ablation portefeuille ici.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.normalize_stress_gate_dispersion_v2 as N

MANIFEST_PATH = (ROOT / "research" / "edge_factory" / "basis_dispersion" /
                 "stress_gate_dispersion_v2" / "DATASET_MANIFEST.yaml")
RESULT_DIR = (ROOT / "research" / "edge_factory" / "basis_dispersion" /
             "stress_gate_dispersion_v2" / "results")

Z_WIN, Z_MIN, Q = 270, 180, 0.95
BLOCK_DAYS = 7
N_RESAMPLES = 10_000
SEED = 20260721


class PreflightError(RuntimeError):
    pass


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── préflight : recalcule tout et compare au manifeste gelé ────────────────

def run_preflight() -> Dict:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text())
    frozen = manifest["hashes"]

    cadence_reports, unique_by_venue_symbol = {}, {}
    for symbol in N.SYMBOLS:
        for venue in ("binance", "bybit"):
            rep, unique = N.cadence_report_funding(venue, symbol)
            cadence_reports[f"{venue}/{symbol}"] = rep
            unique_by_venue_symbol[(venue, symbol)] = unique

    coverage_reports = {}
    for symbol in N.SYMBOLS:
        cov = N.cross_venue_coverage(symbol, unique_by_venue_symbol[("binance", symbol)],
                                     unique_by_venue_symbol[("bybit", symbol)])
        if symbol == "SOLUSDT":
            cov["extra_events_diagnosis"] = N.sol_extra_events_diagnosis(
                cadence_reports["binance/SOLUSDT"]["cadence_regimes"],
                cadence_reports["bybit/SOLUSDT"]["cadence_regimes"])
        coverage_reports[symbol] = cov

    markprice_reports, markprice_unique = {}, {}
    for symbol in N.SYMBOLS:
        rep, unique = N.markprice_coverage(symbol)
        b_ts = sorted(t for t in unique_by_venue_symbol[("binance", symbol)]
                     if N.SIGNAL_START_MS <= t < N.SIGNAL_END_MS)
        rep["forward_window_completeness"] = N.forward_window_completeness(b_ts, set(unique.keys()))
        markprice_reports[symbol] = rep
        markprice_unique[symbol] = unique

    mini_audits = {}
    for symbol in N.SYMBOLS:
        b_ts = sorted(t for t in unique_by_venue_symbol[("binance", symbol)]
                     if N.SIGNAL_START_MS <= t < N.SIGNAL_END_MS)
        y_ts = sorted(t for t in unique_by_venue_symbol[("bybit", symbol)]
                     if N.SIGNAL_START_MS <= t < N.SIGNAL_END_MS)
        mini_audits[symbol] = N.run_mini_audit(symbol, b_ts, y_ts)

    panel_reports, all_panel_rows = {}, []
    for symbol in N.SYMBOLS:
        rows = N.build_primary_panel(symbol, unique_by_venue_symbol[("binance", symbol)],
                                     unique_by_venue_symbol[("bybit", symbol)],
                                     set(markprice_unique[symbol].keys()))
        all_panel_rows.extend(rows)
        from collections import Counter
        by_reason = Counter(r.get("primary_rejection_reason") for r in rows if not r["eligible_primary"])
        panel_reports[symbol] = {"n_pairs_considered": len(rows),
                                 "n_eligible_primary": sum(1 for r in rows if r["eligible_primary"]),
                                 "rejections_by_reason": dict(by_reason)}

    eligible_rows = [{k: v for k, v in r.items()
                      if k not in ("eligible_primary", "primary_rejection_reason")}
                     for r in all_panel_rows if r["eligible_primary"]]
    eligible_rows_sorted = sorted(eligible_rows, key=str)
    mark_price_bars = N.mark_price_bars_used(eligible_rows, markprice_unique)

    raw_file_hashes = []
    for f in sorted(N.RAW.rglob("*.json")):
        raw_file_hashes.append((str(f.relative_to(N.RAW)), hashlib.sha256(f.read_bytes()).hexdigest()))

    funding_rows = []
    for symbol in N.SYMBOLS:
        for venue in ("binance", "bybit"):
            for ts, r in sorted(unique_by_venue_symbol[(venue, symbol)].items()):
                if N.SIGNAL_START_MS <= ts < N.SIGNAL_END_MS:
                    funding_rows.append({"venue": venue, "symbol": symbol, "settlement_ts": ts,
                                         "funding_rate_raw": r.get("fundingRate")})
    price_rows = []
    for symbol in N.SYMBOLS:
        for ts in sorted(markprice_unique[symbol]):
            if N.PRICE_START_MS <= ts < N.PRICE_END_MS:
                price_rows.append({"symbol": symbol, "open_time": ts,
                                   "close": markprice_unique[symbol][ts][4]})

    recomputed = {
        "raw_envelope_manifest_hash": N.sha256_of(raw_file_hashes),
        "semantic_raw_content_hash": N.sha256_of(
            {f"{v}/{s}": sorted(unique_by_venue_symbol[(v, s)].keys())
             for (v, s) in unique_by_venue_symbol}),
        "normalized_funding_hash": N.sha256_of(funding_rows),
        "normalized_mark_price_hash": N.sha256_of(price_rows),
        "cross_venue_intersection_hash": N.sha256_of(coverage_reports),
        "coverage_report_hash": N.sha256_of(coverage_reports),
        "cadence_report_hash": N.sha256_of(cadence_reports),
        "markprice_report_hash": N.sha256_of(markprice_reports),
        "mini_audit_hash": N.sha256_of(mini_audits),
        "primary_panel_report_hash": N.sha256_of(panel_reports),
        "mark_price_bars_used_hash": N.sha256_of(mark_price_bars),
        "analysis_input_hash": N.sha256_of({"eligible_funding_pairs": eligible_rows_sorted,
                                           "mark_price_bars_used": mark_price_bars}),
        "preregistration_hash": hashlib.sha256(
            (ROOT / "research" / "edge_factory" / "basis_dispersion" /
             "stress_gate_dispersion_v2" / "PREREGISTRATION.md").read_bytes()).hexdigest(),
    }

    mismatches = {k: (frozen.get(k), recomputed[k]) for k in recomputed
                 if frozen.get(k) != recomputed[k]}
    if mismatches:
        raise PreflightError(f"hash mismatch vs manifeste gelé : {mismatches}")

    return {"eligible_rows": eligible_rows, "markprice_unique": markprice_unique,
           "hashes": recomputed}


# ── seuil causal groupé (asset x intervalle) ───────────────────────────────

def add_grouped_threshold(rows: List[dict]) -> List[dict]:
    df = pd.DataFrame(rows)
    df = df.sort_values("pair_available_at").reset_index(drop=True)
    df["stress_threshold"] = np.nan
    for (symbol, interval), idx in df.groupby(["symbol", "binance_interval_hours"]).groups.items():
        sub = df.loc[idx].sort_values("pair_available_at")
        thr = sub["raw_dispersion"].shift(1).rolling(window=Z_WIN, min_periods=Z_MIN).quantile(Q)
        df.loc[sub.index, "stress_threshold"] = thr.values
    df["threshold_available"] = df["stress_threshold"].notna()
    df["is_stress"] = df["threshold_available"] & (df["raw_dispersion"] >= df["stress_threshold"])
    return df


# ── ancrage exact de la cible (amendement septies) ─────────────────────────

def add_targets(df: pd.DataFrame, markprice_unique: Dict[str, Dict[int, list]]) -> pd.DataFrame:
    ref_prices, troughs, dds = [], [], []
    for _, row in df.iterrows():
        symbol = row["symbol"]
        decision_ts = int(row["decision_timestamp"])
        mp = markprice_unique[symbol]
        open_ = mp[decision_ts][1] if decision_ts in mp else None
        lows = []
        complete = True
        for bar_ts in range(decision_ts, decision_ts + N.FORWARD_HORIZON_MS, N.BAR_STEP_MS):
            if bar_ts not in mp:
                complete = False
                break
            lows.append(float(mp[bar_ts][3]))
        if open_ is None or not complete:
            ref_prices.append(None); troughs.append(None); dds.append(None)
            continue
        ref = float(open_)
        trough = min(lows)
        ref_prices.append(ref); troughs.append(trough)
        dds.append(trough / ref - 1.0)
    df = df.copy()
    df["reference_price"] = ref_prices
    df["forward_trough"] = troughs
    df["forward_max_drawdown"] = dds
    df["loss_magnitude"] = [(-v if v is not None else None) for v in dds]
    return df


def primary_effect(df: pd.DataFrame) -> Optional[float]:
    d = df[df["threshold_available"] & df["loss_magnitude"].notna()]
    stress = d.loc[d["is_stress"], "loss_magnitude"]
    non_stress = d.loc[~d["is_stress"], "loss_magnitude"]
    if len(stress) == 0 or len(non_stress) == 0:
        return None
    return float(stress.mean() - non_stress.mean())


# ── bootstrap par blocs calendaires mobiles, tous actifs ensemble ──────────

def assign_calendar_blocks(df: pd.DataFrame, block_days: int = BLOCK_DAYS) -> pd.Series:
    t0 = df["pair_available_at"].min()
    block_ms = block_days * 24 * 3600_000
    return ((df["pair_available_at"] - t0) // block_ms).astype(int)


def moving_calendar_block_bootstrap(df: pd.DataFrame, *, block_days: int = BLOCK_DAYS,
                                    resamples: int = N_RESAMPLES, seed: int = SEED) -> np.ndarray:
    """Vectorisé numpy — même séquence de tirage de blocs (rng.choice(blocks,
    size=n_blocks, replace=True), même seed) que l'implémentation d'origine
    à base de pd.concat par resample (trouvée trop lente en pratique : >60min
    pour les appels répétés de Phase 3, cf. commit de correction). Résultat
    numériquement vérifié identique sur le dataset réel gelé (Phase 2)."""
    d = df[df["threshold_available"] & df["loss_magnitude"].notna()].copy()
    d["block"] = assign_calendar_blocks(d, block_days)
    blocks = sorted(d["block"].unique())
    is_stress = d["is_stress"].to_numpy()
    loss = d["loss_magnitude"].to_numpy(dtype=float)
    block_arr = d["block"].to_numpy()
    block_to_indices = {b: np.where(block_arr == b)[0] for b in blocks}
    rng = np.random.RandomState(seed)
    n_blocks = len(blocks)
    deltas = np.full(resamples, np.nan)
    for i in range(resamples):
        chosen = rng.choice(blocks, size=n_blocks, replace=True)
        idx = np.concatenate([block_to_indices[b] for b in chosen])
        s = is_stress[idx]
        l = loss[idx]
        stress_vals = l[s]
        non_stress_vals = l[~s]
        if len(stress_vals) and len(non_stress_vals):
            deltas[i] = stress_vals.mean() - non_stress_vals.mean()
    return deltas[~np.isnan(deltas)]


# ── HAC de soutien (pas seul décisionnaire) ────────────────────────────────

def hac_supporting_test(df: pd.DataFrame) -> dict:
    import statsmodels.api as sm
    d = df[df["threshold_available"] & df["loss_magnitude"].notna()].sort_values("pair_available_at")
    y = d["loss_magnitude"].to_numpy()
    x = sm.add_constant(d["is_stress"].astype(float).to_numpy())
    # lag NW = nb max de lignes (tous actifs poolés) chevauchant une fenêtre de 24h
    ts = d["pair_available_at"].to_numpy()
    max_overlap = 1
    j = 0
    for i in range(len(ts)):
        while j < len(ts) and ts[j] - ts[i] < N.FORWARD_HORIZON_MS:
            j += 1
        max_overlap = max(max_overlap, j - i)
    lag = max(1, max_overlap)
    model = sm.OLS(y, x).fit(cov_type="HAC", cov_kwds={"maxlags": lag})
    coef = float(model.params[1])
    pvalue = float(model.pvalues[1])
    return {"coef": coef, "pvalue": pvalue, "nw_lag": int(lag), "n": int(len(d)),
           "same_sign_as_primary": coef > 0, "supported_p05": pvalue < 0.05}


def sha256_of(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def main() -> None:
    print("PREFLIGHT: recomputing all frozen hashes vs manifest ...")
    try:
        pf = run_preflight()
    except PreflightError as e:
        print(f"PREFLIGHT FAILED: {e}")
        raise SystemExit(2)
    print("PREFLIGHT OK — all hashes match the frozen manifest.")

    import subprocess
    code_commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout.strip()
    receipt = {"experiment_id": "stress_gate_dispersion_v2_reproduction",
              "code_commit": code_commit, "preregistration_commit": "ccb3e63",
              "dataset_manifest_commit": "1ac7da1",
              "analysis_input_hash": pf["hashes"]["analysis_input_hash"],
              "random_seed": SEED, "started_at_utc": now_utc_iso(),
              "economic_outputs_previously_computed": False}
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "unblinding_receipt.yaml").write_text(yaml.safe_dump(receipt, sort_keys=False))
    print("Unblinding receipt written:", RESULT_DIR / "unblinding_receipt.yaml")

    df = add_grouped_threshold(pf["eligible_rows"])
    df = add_targets(df, pf["markprice_unique"])

    delta = primary_effect(df)
    boot = moving_calendar_block_bootstrap(df)
    ci_lo, ci_hi = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))) if len(boot) else (None, None)
    hac = hac_supporting_test(df)

    gate_pass = bool(delta is not None and delta > 0 and ci_lo is not None and ci_lo > 0
                    and hac["same_sign_as_primary"] and hac["supported_p05"])
    if delta is None:
        verdict = "BLOCKED_BY_DATA_SEMANTICS"
    elif gate_pass:
        verdict = "REPRODUCED_CAUSAL_ASSOCIATION"
    else:
        verdict = "REJECTED"

    n_by_group = df.groupby(["symbol", "binance_interval_hours"]).size().to_dict()
    n_threshold_available = int(df["threshold_available"].sum())
    n_stress = int(df["is_stress"].sum())

    result = {
        "verdict": verdict,
        "primary_delta_mean_loss_stress_minus_nonstress": delta,
        "bootstrap": {"resamples": N_RESAMPLES, "block_days": BLOCK_DAYS, "seed": SEED,
                     "n_valid_resamples": int(len(boot)),
                     "ci95_lower": ci_lo, "ci95_upper": ci_hi},
        "hac_supporting": hac,
        "n_rows_total": int(len(df)), "n_threshold_available": n_threshold_available,
        "n_stress_classified": n_stress,
        "n_rows_by_symbol_interval": {str(k): v for k, v in n_by_group.items()},
        "gate_rule": "delta>0 AND bootstrap_ci95_lower>0 AND HAC same sign and p<0.05",
        "completed_at_utc": now_utc_iso(),
    }
    (RESULT_DIR / "PRIMARY_RESULT.json").write_text(json.dumps(result, indent=2, default=str))

    md = [f"# PRIMARY_RESULT — stress_gate_dispersion_v2_reproduction", "",
         f"**Verdict : {verdict}**", "",
         f"- delta primaire (mean loss stress - non_stress) : {delta}",
         f"- bootstrap CI95 (blocs calendaires 7j, {N_RESAMPLES} resamples, seed {SEED}) : "
         f"[{ci_lo}, {ci_hi}]",
         f"- HAC de soutien : coef={hac['coef']:.6g}, p={hac['pvalue']:.4g}, "
         f"lag={hac['nw_lag']}, n={hac['n']}",
         f"- lignes totales : {len(df)}, seuil disponible : {n_threshold_available}, "
         f"classées stress : {n_stress}",
         "", "Gate : `delta>0 AND bootstrap_ci95_lower>0 AND HAC même signe et p<0.05`."]
    (RESULT_DIR / "PRIMARY_RESULT.md").write_text("\n".join(md))

    print(json.dumps(result, indent=2, default=str))
    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    main()
