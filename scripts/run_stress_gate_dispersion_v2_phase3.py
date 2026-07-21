#!/usr/bin/env python3
"""
scripts/run_stress_gate_dispersion_v2_phase3.py
─────────────────────────────────────────────────────────────────────────────
Phase 3 — stabilité + valeur incrémentale (research/edge_factory/
basis_dispersion/stress_gate_dispersion_v2/PHASE3_STABILITY_PROTOCOL.md).

Les labels stress (is_stress) sont GELÉS depuis le résultat primaire Phase 2
— jamais recalculés par sous-échantillon (un leave-one-out qui recalculerait
le seuil sur un historique tronqué changerait la définition même du stress
et invaliderait la comparaison). Tous les tests ci-dessous ne font que
FILTRER le dataframe déjà labellisé de Phase 2 et recalculer l'effet/le
bootstrap sur le sous-ensemble.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.normalize_stress_gate_dispersion_v2 as N
import scripts.run_stress_gate_dispersion_v2_primary_test as P2

RESULT_DIR = (ROOT / "research" / "edge_factory" / "basis_dispersion" /
             "stress_gate_dispersion_v2" / "results")

PERIOD_SPLIT_ISO = "2024-09-08T00:00:00Z"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def labeled_full_panel() -> pd.DataFrame:
    """Reproduit EXACTEMENT le df labellisé de Phase 2 (mêmes fonctions,
    même préflight) — is_stress/loss_magnitude gelés une fois pour toutes."""
    pf = P2.run_preflight()
    df = P2.add_grouped_threshold(pf["eligible_rows"])
    df = P2.add_targets(df, pf["markprice_unique"])
    return df, pf["markprice_unique"]


def effect_with_ci(df: pd.DataFrame) -> dict:
    delta = P2.primary_effect(df)
    if delta is None:
        return {"delta": None, "ci95_lower": None, "ci95_upper": None, "n": len(df)}
    boot = P2.moving_calendar_block_bootstrap(df)
    lo, hi = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))) if len(boot) else (None, None)
    return {"delta": delta, "ci95_lower": lo, "ci95_upper": hi, "n_valid_resamples": int(len(boot)),
           "n": int((df["threshold_available"] & df["loss_magnitude"].notna()).sum())}


# ── Test 1 : stabilité temporelle ──────────────────────────────────────────

def split_by_period(df: pd.DataFrame, cutoff_iso: str = PERIOD_SPLIT_ISO
                    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    cutoff_ms = int(datetime.fromisoformat(cutoff_iso.replace("Z", "+00:00")).timestamp() * 1000)
    return df[df["pair_available_at"] < cutoff_ms], df[df["pair_available_at"] >= cutoff_ms]


# ── Test 2 : leave-one-asset-out ───────────────────────────────────────────

def leave_one_asset_out(df: pd.DataFrame, asset: str) -> pd.DataFrame:
    return df[df["symbol"] != asset]


# ── Test 3 : leave-one-calendar-year-out ───────────────────────────────────

def leave_one_year_out(df: pd.DataFrame, year: int) -> pd.DataFrame:
    years = df["pair_available_at"].apply(
        lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year)
    return df[years != year]


# ── Test 4 : leave-one-stress-episode-out ──────────────────────────────────

def identify_stress_episodes(df: pd.DataFrame, gap_ms: int = 24 * 3600_000) -> List[dict]:
    stress = df[df["is_stress"]].sort_values("pair_available_at")
    ts = stress["pair_available_at"].to_numpy()
    idx = stress.index.to_numpy()
    episodes = []
    if len(ts) == 0:
        return episodes
    cur_start, cur_idx = ts[0], [idx[0]]
    for i in range(1, len(ts)):
        if ts[i] - ts[i - 1] <= gap_ms:
            cur_idx.append(idx[i])
        else:
            episodes.append({"start_ts": int(cur_start), "end_ts": int(ts[i - 1]), "indices": cur_idx})
            cur_start, cur_idx = ts[i], [idx[i]]
    episodes.append({"start_ts": int(cur_start), "end_ts": int(ts[-1]), "indices": cur_idx})
    return episodes


def leave_one_episode_out(df: pd.DataFrame, episode: dict) -> pd.DataFrame:
    return df.drop(index=episode["indices"])


# ── Test 5 : sensibilité panel exact-ms ─────────────────────────────────────

def exact_ms_subpanel(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["timestamp_offset_ms"] == 0]


# ── Test 6 : valeur incrémentale (controls causaux, avant decision_timestamp) ──

def trailing_24h_controls(symbol: str, decision_ts: int, mp: dict) -> Tuple[float, float]:
    start = decision_ts - N.FORWARD_HORIZON_MS
    bars = []
    t = start
    while t < decision_ts:
        if t not in mp:
            return None, None
        bars.append(mp[t])
        t += N.BAR_STEP_MS
    closes = [float(b[4]) for b in bars]
    lows = [float(b[3]) for b in bars]
    ref = closes[0]
    trough = min(lows)
    trailing_dd = trough / ref - 1.0
    log_rets = np.diff(np.log(closes))
    trailing_rv = float(np.std(log_rets)) if len(log_rets) > 1 else None
    return trailing_dd, trailing_rv


def add_incremental_controls(df: pd.DataFrame, markprice_unique: dict) -> pd.DataFrame:
    dds, rvs = [], []
    for _, row in df.iterrows():
        dd, rv = trailing_24h_controls(row["symbol"], int(row["decision_timestamp"]), markprice_unique[row["symbol"]])
        dds.append(dd); rvs.append(rv)
    df = df.copy()
    df["trailing_24h_drawdown"] = dds
    df["trailing_24h_realized_volatility"] = rvs
    df["calendar_year"] = df["pair_available_at"].apply(
        lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year)
    return df


def incremental_value_test(df: pd.DataFrame, markprice_unique: dict) -> dict:
    import statsmodels.api as sm
    d = df[df["threshold_available"] & df["loss_magnitude"].notna()].copy()
    d = add_incremental_controls(d, markprice_unique)
    d = d[d["trailing_24h_drawdown"].notna() & d["trailing_24h_realized_volatility"].notna()]
    d = d.sort_values("pair_available_at")

    fe_asset = pd.get_dummies(d["symbol"], prefix="asset", drop_first=True)
    fe_year = pd.get_dummies(d["calendar_year"], prefix="year", drop_first=True)
    fe_interval = pd.get_dummies(d["binance_interval_hours"], prefix="interval", drop_first=True)
    X = pd.concat([fe_asset, fe_year, fe_interval,
                  d[["is_stress", "trailing_24h_drawdown", "trailing_24h_realized_volatility"]]
                  .astype(float)], axis=1)
    X = sm.add_constant(X.astype(float))
    y = d["loss_magnitude"].astype(float).to_numpy()

    ts = d["pair_available_at"].to_numpy()
    max_overlap, j = 1, 0
    for i in range(len(ts)):
        while j < len(ts) and ts[j] - ts[i] < N.FORWARD_HORIZON_MS:
            j += 1
        max_overlap = max(max_overlap, j - i)
    lag = max(1, max_overlap)

    model = sm.OLS(y, X.to_numpy()).fit(cov_type="HAC", cov_kwds={"maxlags": lag})
    stress_col = list(X.columns).index("is_stress")
    beta_stress = float(model.params[stress_col])
    pvalue = float(model.pvalues[stress_col])

    # bootstrap calendaire conjoint sur beta_stress
    d2 = d.reset_index(drop=True)
    d2["block"] = P2.assign_calendar_blocks(d2)
    blocks = sorted(d2["block"].unique())
    by_block = {b: d2[d2["block"] == b] for b in blocks}
    rng = np.random.RandomState(P2.SEED)
    betas = []
    for _ in range(2000):   # 2000 resamples pour la régression (coût par resample plus élevé qu'une moyenne)
        chosen = rng.choice(blocks, size=len(blocks), replace=True)
        sample = pd.concat([by_block[b] for b in chosen], ignore_index=True)
        if sample["is_stress"].nunique() < 2:
            continue
        fe_a = pd.get_dummies(sample["symbol"], prefix="asset", drop_first=True)
        fe_y = pd.get_dummies(sample["calendar_year"], prefix="year", drop_first=True)
        fe_i = pd.get_dummies(sample["binance_interval_hours"], prefix="interval", drop_first=True)
        Xs = pd.concat([fe_a, fe_y, fe_i,
                       sample[["is_stress", "trailing_24h_drawdown",
                              "trailing_24h_realized_volatility"]].astype(float)], axis=1)
        Xs = sm.add_constant(Xs.reindex(columns=X.columns, fill_value=0.0).astype(float))
        ys = sample["loss_magnitude"].astype(float).to_numpy()
        try:
            m = sm.OLS(ys, Xs.to_numpy()).fit()
            betas.append(float(m.params[stress_col]))
        except Exception:
            continue
    betas = np.array(betas)
    ci_lo = float(np.percentile(betas, 2.5)) if len(betas) else None

    return {"beta_stress": beta_stress, "pvalue": pvalue, "nw_lag": int(lag), "n": int(len(d)),
           "bootstrap_ci95_lower": ci_lo, "n_valid_resamples": int(len(betas)),
           "gate_pass": bool(beta_stress > 0 and ci_lo is not None and ci_lo > 0)}


def main() -> None:
    print("Rebuilding Phase 2 labeled panel (frozen stress labels) ...")
    df, markprice_unique = labeled_full_panel()
    print(f"Panel rows: {len(df)}, stress-classified: {int(df['is_stress'].sum())}")

    report = {"started_at_utc": now_utc_iso(), "frozen_from_phase2_commit": "c34cc9d"}

    # Test 1
    p1, p2 = split_by_period(df)
    e1, e2 = effect_with_ci(p1), effect_with_ci(p2)
    report["test1_temporal_stability"] = {"period_1": e1, "period_2": e2,
                                          "gate_pass": bool(e1["delta"] and e1["delta"] > 0
                                                            and e2["delta"] and e2["delta"] > 0)}

    # Test 2
    t2 = {}
    for asset in N.SYMBOLS:
        t2[asset] = effect_with_ci(leave_one_asset_out(df, asset))
    report["test2_leave_one_asset_out"] = {
        "results": t2, "gate_pass": all(v["delta"] is not None and v["delta"] > 0 for v in t2.values())}

    # Test 3
    years = sorted(df["pair_available_at"].apply(
        lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year).unique())
    t3 = {int(y): effect_with_ci(leave_one_year_out(df, y)) for y in years}
    report["test3_leave_one_year_out"] = {
        "results": t3, "gate_pass": all(v["delta"] is not None and v["delta"] > 0 for v in t3.values())}

    # Test 4
    episodes = identify_stress_episodes(df)
    t4 = []
    for ep in episodes:
        eff = effect_with_ci(leave_one_episode_out(df, ep))
        t4.append({"start_ts": ep["start_ts"], "end_ts": ep["end_ts"],
                  "n_events": len(ep["indices"]), "delta_after_removal": eff["delta"]})
    min_delta = min((r["delta_after_removal"] for r in t4 if r["delta_after_removal"] is not None), default=None)
    worst = min(t4, key=lambda r: (r["delta_after_removal"] if r["delta_after_removal"] is not None else float("inf")))
    report["test4_leave_one_stress_episode_out"] = {
        "n_episodes": len(episodes),
        "median_events_per_episode": float(np.median([len(e["indices"]) for e in episodes])) if episodes else None,
        "min_leave_one_episode_out_delta": min_delta,
        "episode_with_largest_effect_reduction": {"start_iso": N.iso(worst["start_ts"]),
                                                   "end_iso": N.iso(worst["end_ts"]),
                                                   "n_events": worst["n_events"]} if t4 else None,
        "gate_pass": bool(min_delta is not None and min_delta > 0)}

    # Test 5
    e5 = effect_with_ci(exact_ms_subpanel(df))
    report["test5_exact_ms_sensitivity"] = {**e5, "gate_pass": bool(e5["delta"] is not None and e5["delta"] > 0)}

    # Test 6
    print("Running incremental-value regression (this can take a moment) ...")
    t6 = incremental_value_test(df, markprice_unique)
    report["test6_incremental_value"] = t6

    stability_pass = all([report["test1_temporal_stability"]["gate_pass"],
                          report["test2_leave_one_asset_out"]["gate_pass"],
                          report["test3_leave_one_year_out"]["gate_pass"],
                          report["test4_leave_one_stress_episode_out"]["gate_pass"]])
    incremental_pass = report["test6_incremental_value"]["gate_pass"]

    if not stability_pass:
        verdict = "REPRODUCED_BUT_UNSTABLE_ASSOCIATION"
    elif not incremental_pass:
        verdict = "REPRODUCED_NON_INCREMENTAL_RISK_ASSOCIATION"
    else:
        verdict = "VALIDATED_RISK_FEATURE_CANDIDATE"

    report["verdict"] = verdict
    report["stability_gates_all_pass"] = stability_pass
    report["incremental_gate_pass"] = incremental_pass
    report["completed_at_utc"] = now_utc_iso()

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "PHASE3_RESULT.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "test4_leave_one_stress_episode_out"},
                     indent=2, default=str))
    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    main()
