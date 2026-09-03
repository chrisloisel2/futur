"""V2 — famille CASCADE : BTC_LEAD_ALT_CASCADE + LIQ_CASCADE_FAR_FROM_LOW,
et V1 — SHORT_COVERING_CONTINUATION (contrôle de chevauchement uniquement ici).

Spec exécutée : ../BTC_LEAD_ALT_CASCADE/PREREGISTRATION.md (population A, règle de
choc causale q90 sur 365 j, PRIMARY BLA-P0, perturbations BLA-P1..P8, critères S1..S5)
et la règle de seuil PROPRE à ce validateur pour FAR_FROM_LOW (le 0,05 de la spec live
est RECONSTRUIT/data-snoopé — il devient une perturbation, pas la primaire).

Déclustering (leçon wave 1) : un choc BTC est un événement MARKET-WIDE, donc L3 =
épisode cross-symbole chaîné (gap < 4 h), jamais le déclustering same-symbol de la
découverte qui laisse N surestimé d'un ordre de grandeur.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl  # noqa: E402

OUT = "/home/qbee/futur/reports/edge_discovery/validation_2026-09"
CASCADE = "/home/qbee/futur/data/events/cascade_dataset.parquet"
LEGACY = "/home/qbee/futur/data/events/liq_cascade_dataset.parquet"
LEDGER = "/home/qbee/futur/reports/live_alpha_lab/LIQ_CASCADE_REPEAT_V1/decisions.parquet"

HORIZON = pd.Timedelta(hours=4)
EPISODE_GAP = pd.Timedelta(hours=4)     # L3 : chaîne cross-symbole
COST_NOMINAL, COST_STRESS = 14.0, 28.0  # une jambe, convention projet
MIN_CALENDAR_DAYS = 60                  # plancher événementiel (briefing §3.4)


# ═══════════════════════════════════════════════════════════════════════════
# Population et déclustering
# ═══════════════════════════════════════════════════════════════════════════

def population_A(path: str = CASCADE, since: str = "2022-01-01") -> pd.DataFrame:
    d = pd.read_parquet(path)
    d["event_time"] = pd.to_datetime(d["event_time"], utc=True)
    a = d[
        (d["kind"] == "LONG_CASCADE")
        & (d["symbol"] != "BTCUSDT")
        & (d["label_full"] == True)          # noqa: E712
        & d["fwd_4h"].notna()
        & (d["event_time"] >= pd.Timestamp(since, tz="UTC"))
        & d["btc_ret_30m"].notna()
    ].copy()
    return a.sort_values("event_time").reset_index(drop=True)


def add_declustering(a: pd.DataFrame) -> pd.DataFrame:
    """L1 = même symbole, chaîne < 24 h. L2 = jour calendaire UTC.
    L3 = épisode cross-symbole chaîné (gap < 4 h) — l'unité d'inférence."""
    a = a.sort_values("event_time").reset_index(drop=True)
    a["L3"] = vl.chain_episodes(a["event_time"], EPISODE_GAP)
    a["L2"] = a["event_time"].dt.floor("D")
    l1 = []
    for sym, g in a.groupby("symbol", sort=False):
        ep = vl.chain_episodes(g["event_time"], pd.Timedelta(hours=24))
        l1.append(pd.Series([f"{sym}_{e}" for e in ep], index=g.index))
    a["L1"] = pd.concat(l1).reindex(a.index)
    return a


def causal_shock_flag(a: pd.DataFrame, *, q: float = 0.90, lookback_days: int = 365,
                      min_prior: int = 200, signed: str | None = None) -> pd.Series:
    """`shock(t)` = |btc_ret_30m| >= q-ième centile des événements de la population A
    dans [t − lookback, t) — STRICTEMENT causal, jamais le centile in-sample.

    `signed='down'` -> btc_ret_30m <= q(1−q) signé ; `signed='up'` -> >= q signé.
    Renvoie une Series booléenne, NaN quand moins de `min_prior` événements antérieurs.
    """
    t = a["event_time"].to_numpy()
    v = (a["btc_ret_30m"] if signed else a["btc_ret_30m"].abs()).to_numpy()
    lb = pd.Timedelta(days=lookback_days).to_timedelta64()
    out = np.full(len(a), np.nan)
    lo = 0
    for i in range(len(a)):
        while t[lo] < t[i] - lb:
            lo += 1
        prior = v[lo:i]
        if len(prior) < min_prior:
            continue
        if signed == "down":
            out[i] = v[i] <= np.quantile(prior, 1.0 - q)
        elif signed == "up":
            out[i] = v[i] >= np.quantile(prior, q)
        else:
            out[i] = v[i] >= np.quantile(prior, q)
    return pd.Series(out, index=a.index)


# ═══════════════════════════════════════════════════════════════════════════
# Gate événementiel
# ═══════════════════════════════════════════════════════════════════════════

def episode_returns(arm: pd.DataFrame) -> tuple[pd.Series, pd.Series, np.ndarray]:
    """Agrège au niveau L3 : la valeur d'un épisode = moyenne des bps bruts de ses
    jambes (un épisode = UNE observation indépendante, pas N jambes corrélées)."""
    g = arm.groupby("L3")
    gross = g.apply(lambda x: float(x["fwd_4h"].mean()) * 1e4)
    dates = g["event_time"].min()
    return gross, dates, gross.index.to_numpy()


def gate_arm(arm: pd.DataFrame, *, exclude_years=None, cost=COST_NOMINAL,
             stress=COST_STRESS) -> dict:
    if exclude_years:
        arm = arm[~arm["event_time"].dt.year.isin(exclude_years)]
    if arm.empty:
        return {"error": "empty arm"}
    gross, dates, l3 = episode_returns(arm)
    out = vl.full_gate(
        gross, dates=dates, l3=l3,
        cost_nominal=cost, cost_stress=stress,
        l3_definition="épisode cross-symbole chaîné (gap < 4h)",
        minimum_calendar_days=MIN_CALENDAR_DAYS,
        n_raw=int(len(arm)),
        n_l1=int(arm["L1"].nunique()),
        n_l2=int(arm["L2"].nunique()),
    )
    out["n_events_raw"] = int(len(arm))
    return out


def arm_difference(a_arm: pd.DataFrame, b_arm: pd.DataFrame) -> dict:
    """Test « bras A − bras B » sur la même population, déclusterisé par épisode.
    Welch sur les moyennes d'épisode + bootstrap par bloc de la différence."""
    ga, _, _ = episode_returns(a_arm)
    gb, _, _ = episode_returns(b_arm)
    if len(ga) < 2 or len(gb) < 2:
        return {"error": "not enough episodes"}
    from scipy import stats as sps
    w = sps.ttest_ind(ga.to_numpy(), gb.to_numpy(), equal_var=False)
    rng = np.random.default_rng(vl.BOOTSTRAP_SEED)
    A, B = ga.to_numpy(), gb.to_numpy()
    diffs = np.array([
        A[rng.integers(0, len(A), len(A))].mean() - B[rng.integers(0, len(B), len(B))].mean()
        for _ in range(5000)
    ])
    return {
        "arm_A_gross_bps": round(float(A.mean()), 2),
        "arm_B_gross_bps": round(float(B.mean()), 2),
        "difference_bps": round(float(A.mean() - B.mean()), 2),
        "welch_t": round(float(w.statistic), 3),
        "welch_p_one_sided": round(float(w.pvalue / 2 if w.statistic > 0 else 1 - w.pvalue / 2), 4),
        "n_episodes_A": int(len(A)), "n_episodes_B": int(len(B)),
        "bootstrap_ci95": [round(float(np.percentile(diffs, 2.5)), 2),
                           round(float(np.percentile(diffs, 97.5)), 2)],
        "bootstrap_P_diff_le_0": round(float((diffs <= 0).mean()), 4),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Chevauchement avec le ledger live
# ═══════════════════════════════════════════════════════════════════════════

def ledger_overlap(arm: pd.DataFrame, ledger_path: str = LEDGER,
                   tol: pd.Timedelta = pd.Timedelta(minutes=5)) -> dict:
    if not os.path.exists(ledger_path):
        return {"error": "ledger absent", "path": ledger_path}
    led = pd.read_parquet(ledger_path)
    tcol = next((c for c in ("event_time", "decision_ts", "ts", "timestamp")
                 if c in led.columns), None)
    if tcol is None or "symbol" not in led.columns:
        return {"error": f"colonnes ledger inattendues: {list(led.columns)[:12]}"}
    led[tcol] = pd.to_datetime(led[tcol], utc=True)
    hits = 0
    for sym, g in arm.groupby("symbol"):
        lg = led.loc[led["symbol"] == sym, tcol].sort_values()
        if lg.empty:
            continue
        idx = np.searchsorted(lg.to_numpy(), g["event_time"].to_numpy())
        for k, et in zip(idx, g["event_time"]):
            for j in (k - 1, k):
                if 0 <= j < len(lg) and abs(lg.iloc[j] - et) <= tol:
                    hits += 1
                    break
    return {
        "n_arm_events": int(len(arm)),
        "n_ledger_rows": int(len(led)),
        "n_matched_within_5min": int(hits),
        "share_of_arm_in_ledger": round(hits / max(1, len(arm)), 4),
        "ledger_window": [str(led[tcol].min()), str(led[tcol].max())],
    }


# ═══════════════════════════════════════════════════════════════════════════

def main():
    res = {}
    a = add_declustering(population_A())
    print(f"population A: n={len(a)} symbols={a.symbol.nunique()} "
          f"L1={a.L1.nunique()} L2={a.L2.nunique()} L3={a.L3.nunique()}", flush=True)
    res["_population"] = {
        "n_raw": int(len(a)), "n_symbols": int(a.symbol.nunique()),
        "n_L1": int(a.L1.nunique()), "n_L2": int(a.L2.nunique()), "n_L3": int(a.L3.nunique()),
        "window": [str(a.event_time.min()), str(a.event_time.max())],
        "baseline_gross_bps": round(float(a.fwd_4h.mean()) * 1e4, 2),
    }

    # ── BTC_LEAD_ALT_CASCADE ──────────────────────────────────────────────
    print("[BLA] BTC_LEAD_ALT_CASCADE", flush=True)
    flag = causal_shock_flag(a)
    a["shock"] = flag
    usable = a[flag.notna()]
    shock, no_shock = usable[usable["shock"] == 1], usable[usable["shock"] == 0]
    print(f"  usable={len(usable)} shock={len(shock)} no_shock={len(no_shock)} "
          f"(dropped for <200 prior: {int(flag.isna().sum())})", flush=True)

    BLA = {
        "_arms": {"n_usable": int(len(usable)), "n_shock": int(len(shock)),
                  "n_no_shock": int(len(no_shock)),
                  "n_dropped_insufficient_prior": int(flag.isna().sum())},
        "T1_shock_alone": gate_arm(shock),
        "T1_no_shock_arm_B": gate_arm(no_shock),
        "T2_shock_minus_noshock": arm_difference(shock, no_shock),
    }

    # BLA-P1 : décile in-sample (construction littérale de la découverte)
    thr = a["btc_ret_30m"].abs().quantile(0.90)
    BLA["P1_insample_decile"] = gate_arm(a[a["btc_ret_30m"].abs() >= thr])
    # BLA-P2 : split signé
    for s in ("down", "up"):
        f = causal_shock_flag(a, signed=s)
        BLA[f"P2_{s}_shock"] = gate_arm(a[f == 1])
    # BLA-P4 : quintile
    f80 = causal_shock_flag(a, q=0.80)
    BLA["P4_quintile"] = gate_arm(a[f80 == 1])
    # BLA-P6 : dataset de découverte historique
    try:
        legacy = add_declustering(population_A(LEGACY))
        fl = causal_shock_flag(legacy)
        BLA["P6_legacy_dataset"] = gate_arm(legacy[fl == 1])
    except Exception as e:                                   # noqa: BLE001
        BLA["P6_legacy_dataset"] = {"error": str(e)}
    # BLA-P7 : hors meilleure année
    best = BLA["T1_shock_alone"].get("best_year")
    if best:
        BLA["P7_ex_best_year"] = gate_arm(shock, exclude_years=[best])
    # chevauchement + résidu BLA-P8
    ov = ledger_overlap(shock)
    BLA["overlap_LIQ_CASCADE_REPEAT_V1"] = ov
    res["BTC_LEAD_ALT_CASCADE"] = BLA

    # ── LIQ_CASCADE_FAR_FROM_LOW ──────────────────────────────────────────
    print("[FFL] LIQ_CASCADE_FAR_FROM_LOW", flush=True)
    # Règle PROPRE (préenregistrée ici) : centile 75 CAUSAL sur 365 j de dist_low_24h
    # parmi la population A — le 0,05 de la spec live est reconstruit -> perturbation.
    t = a["event_time"].to_numpy()
    v = a["dist_low_24h"].to_numpy()
    lb = pd.Timedelta(days=365).to_timedelta64()
    far = np.full(len(a), np.nan)
    lo = 0
    for i in range(len(a)):
        while t[lo] < t[i] - lb:
            lo += 1
        prior = v[lo:i]
        if len(prior) >= 200:
            far[i] = v[i] >= np.quantile(prior, 0.75)
    far = pd.Series(far, index=a.index)
    FFL = {
        "_rule": "dist_low_24h >= q75 causal 365j (>=200 événements antérieurs)",
        "_arms": {"n_far": int((far == 1).sum()), "n_near": int((far == 0).sum())},
        "T1_far_causal_q75": gate_arm(a[far == 1]),
        "T1_near_arm_B": gate_arm(a[far == 0]),
        "T2_far_minus_near": arm_difference(a[far == 1], a[far == 0]),
        "P1_live_threshold_0p05": gate_arm(a[a["dist_low_24h"] >= 0.05]),
        "P2_dist_low_7d_q75": gate_arm(a[a["dist_low_7d"] >= a["dist_low_7d"].quantile(0.75)]),
        "overlap_LIQ_CASCADE_REPEAT_V1": ledger_overlap(a[far == 1]),
    }
    bf = FFL["T1_far_causal_q75"].get("best_year")
    if bf:
        FFL["P3_ex_best_year"] = gate_arm(a[far == 1], exclude_years=[bf])
    res["LIQ_CASCADE_FAR_FROM_LOW"] = FFL

    os.makedirs(f"{OUT}/_lib/out", exist_ok=True)
    with open(f"{OUT}/_lib/out/v2_raw.json", "w") as f:
        json.dump(res, f, indent=2, default=str)

    print("\n=== SYNTHÈSE V2 ===")
    for cand in ("BTC_LEAD_ALT_CASCADE", "LIQ_CASCADE_FAR_FROM_LOW"):
        print(f"\n{cand}")
        for k, v in res[cand].items():
            if isinstance(v, dict) and "net_bps" in v:
                print(f"  {k:28s} net={v['net_bps']:8.2f} net28={v['net_bps_stress28']:8.2f} "
                      f"t_L3={str(v['t_stat_declustered']):>7s} L3={v['n_independent_L3']:5d} "
                      f"raw={v.get('n_events_raw',0):6d} p05={v['bootstrap_p05']:8.2f} "
                      f"yrs+={v['n_years_positive']}/{v['n_years']}")
            elif isinstance(v, dict) and "difference_bps" in v:
                print(f"  {k:28s} A-B={v['difference_bps']:8.2f} welch_t={v['welch_t']:6.2f} "
                      f"P(diff<=0)={v['bootstrap_P_diff_le_0']}")
        print("  overlap:", json.dumps(res[cand].get("overlap_LIQ_CASCADE_REPEAT_V1", {}), default=str)[:200])


if __name__ == "__main__":
    main()
