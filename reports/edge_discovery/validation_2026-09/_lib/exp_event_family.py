"""Famille ÉVÉNEMENTIELLE — validations #12, #13, #18, #19 de la liste de mission.

  #12 OI_COLLAPSE_BOUNCE            — effondrement d'OI extrême -> rebond
  #13 CVD_SHOCK_DOWN_MEMORY         — choc de flux taker baissier -> mémoire/reprise
  #18 PREMIUM_EXTREME_THEN_CASCADE  — premium index extrême puis cascade
  #19 CROWD_WASHOUT_NO_CASCADE      — capitulation de la foule sans cascade

Toutes partagent le gate de la famille cascade (exp_v2_cascade) :
  - conditionnement par règle de centile CAUSALE (jamais in-sample) ;
  - test bras A − bras B sur la MÊME population (jamais « A > 0 ») ;
  - L3 = épisode cross-symbole chaîné (gap < 4 h) ;
  - double lecture : moyenne par épisode ET moyenne par événement avec SE
    cluster-robuste — un candidat n'est retenu que si les deux tiennent.

Chaque règle est fixée ici AVANT exécution (aucune n'est ajustée après résultat).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl                  # noqa: E402
import exp_v2_cascade as ev2                 # noqa: E402
from exp_event_weighted_cluster import ew_cluster, diff_ew_cluster   # noqa: E402

OUT = "/home/qbee/futur/reports/edge_discovery/validation_2026-09/_lib/out"
DATA = "/home/qbee/futur/data/events"
COST = 14.0


def load_events(fname: str, *, kind: str = "LONG_CASCADE", fwd: str = "fwd_4h",
                since: str = "2022-01-01") -> pd.DataFrame:
    d = pd.read_parquet(f"{DATA}/{fname}")
    d["event_time"] = pd.to_datetime(d["event_time"], utc=True)
    m = (d["event_time"] >= pd.Timestamp(since, tz="UTC")) & d[fwd].notna()
    if kind and "kind" in d.columns:
        m &= d["kind"] == kind
    if "label_full" in d.columns:
        m &= d["label_full"] == True            # noqa: E712
    d = d[m].sort_values("event_time").reset_index(drop=True)
    if fwd != "fwd_4h":
        d["fwd_4h"] = d[fwd]                    # le gate lit fwd_4h
    return ev2.add_declustering(d)


def causal_pctile_flag(df: pd.DataFrame, col: str, q: float, *, lookback_days: int = 365,
                       min_prior: int = 200, low_tail: bool = False) -> pd.Series:
    """`flag(t)` = col(t) au-delà du q-ième centile CAUSAL sur [t−365j, t).
    `low_tail=True` -> queue basse (col <= q-ième centile)."""
    t = df["event_time"].to_numpy()
    v = df[col].to_numpy(dtype=float)
    lb = pd.Timedelta(days=lookback_days).to_timedelta64()
    out = np.full(len(df), np.nan)
    lo = 0
    for i in range(len(df)):
        while t[lo] < t[i] - lb:
            lo += 1
        prior = v[lo:i]
        prior = prior[~np.isnan(prior)]
        if len(prior) < min_prior or np.isnan(v[i]):
            continue
        thr = np.quantile(prior, q)
        out[i] = (v[i] <= thr) if low_tail else (v[i] >= thr)
    return pd.Series(out, index=df.index)


def evaluate(name: str, df: pd.DataFrame, flag: pd.Series, *, note: str) -> dict:
    """Gate complet d'un conditionnement binaire sur une population d'événements."""
    u = df[flag.notna()].copy()
    A, B = u[flag[flag.notna()] == 1], u[flag[flag.notna()] == 0]
    if len(A) < 50:
        return {"name": name, "note": note, "error": f"bras A trop mince (n={len(A)})"}
    out = {
        "name": name, "note": note,
        "n_population": int(len(u)), "n_arm_A": int(len(A)), "n_arm_B": int(len(B)),
        "episode_level_A": ev2.gate_arm(A),
        "episode_level_B": ev2.gate_arm(B),
        "episode_level_A_minus_B": ev2.arm_difference(A, B),
        "event_weighted_A": ew_cluster(A, f"{name} bras A"),
        "event_weighted_B": ew_cluster(B, f"{name} bras B"),
        "event_weighted_A_minus_B": diff_ew_cluster(A, B, f"{name} A−B"),
    }
    ea, ew, d1, d2 = (out["episode_level_A"], out["event_weighted_A"],
                      out["episode_level_A_minus_B"], out["event_weighted_A_minus_B"])
    print(f"  {name:34s} nA={len(A):6d} L3={ea['n_independent_L3']:5d} | "
          f"episode net14={ea['net_bps']:8.2f} t={str(ea['t_stat_declustered']):>7s} | "
          f"event net14={ew['net14_event_weighted']:8.2f} t_cl={str(ew['t_cluster_robust']):>6s} | "
          f"A−B ep={d1.get('difference_bps')} t_ev={d2.get('t_cluster_robust')}", flush=True)
    return out


def main():
    res = {}
    casc = ev2.add_declustering(ev2.population_A())

    # ── #12 OI_COLLAPSE_BOUNCE ────────────────────────────────────────────
    # Règle : effondrement d'OI 24 h dans la queue BASSE (décile) de son historique
    # causal -> le déleveraging est terminé, le rebond suit. Bras B = le reste.
    print("[#12 OI_COLLAPSE_BOUNCE]", flush=True)
    res["OI_COLLAPSE_BOUNCE"] = {
        "primary": evaluate("oi_ret_24h <= q10 causal", casc,
                            causal_pctile_flag(casc, "oi_ret_24h", 0.10, low_tail=True),
                            note="effondrement d'OI 24h extrême -> rebond"),
        "P1_q05": evaluate("oi_ret_24h <= q05 causal", casc,
                           causal_pctile_flag(casc, "oi_ret_24h", 0.05, low_tail=True),
                           note="queue plus extrême"),
        "P2_oi_pctile_30d": evaluate("oi_pctile_30d <= q10 causal", casc,
                                     causal_pctile_flag(casc, "oi_pctile_30d", 0.10, low_tail=True),
                                     note="variante : OI bas vs 30j"),
    }

    # ── #13 CVD_SHOCK_DOWN_MEMORY ─────────────────────────────────────────
    # Proxy CVD : taker_z (déséquilibre de flux taker). Choc BAISSIER = queue basse.
    print("[#13 CVD_SHOCK_DOWN_MEMORY]", flush=True)
    res["CVD_SHOCK_DOWN_MEMORY"] = {
        "primary": evaluate("taker_z <= q10 causal", casc,
                            causal_pctile_flag(casc, "taker_z", 0.10, low_tail=True),
                            note="choc de flux taker baissier -> mémoire/reprise"),
        "P1_delta_1h": evaluate("taker_delta_1h <= q10 causal", casc,
                                causal_pctile_flag(casc, "taker_delta_1h", 0.10, low_tail=True),
                                note="variante : variation 1h du flux taker"),
    }

    # ── #18 PREMIUM_EXTREME_THEN_CASCADE ──────────────────────────────────
    print("[#18 PREMIUM_EXTREME_THEN_CASCADE]", flush=True)
    try:
        prem = load_events("premium_dataset.parquet")
        col = "prem_z_at" if "prem_z_at" in prem.columns else "prem_at"
        res["PREMIUM_EXTREME_THEN_CASCADE"] = {
            "_population": {"n": int(len(prem)), "L3": int(prem.L3.nunique()),
                            "feature": col},
            "primary": evaluate(f"{col} <= q10 causal", prem,
                                causal_pctile_flag(prem, col, 0.10, low_tail=True),
                                note="premium extrêmement négatif puis cascade -> fade"),
            "P1_high_tail": evaluate(f"{col} >= q90 causal", prem,
                                     causal_pctile_flag(prem, col, 0.90),
                                     note="queue haute (contrôle de direction)"),
        }
    except Exception as e:                                   # noqa: BLE001
        res["PREMIUM_EXTREME_THEN_CASCADE"] = {"error": str(e)}
        print("   ERREUR:", e, flush=True)

    # ── #19 CROWD_WASHOUT_NO_CASCADE ──────────────────────────────────────
    print("[#19 CROWD_WASHOUT_NO_CASCADE]", flush=True)
    try:
        crowd = load_events("crowding_dataset.parquet",
                            fwd="fwd_4h" if "fwd_4h" in pd.read_parquet(
                                f"{DATA}/crowding_dataset.parquet").columns else "fwd_1h")
        col = "toptrader_z_at" if "toptrader_z_at" in crowd.columns else "toptrader_z"
        res["CROWD_WASHOUT_NO_CASCADE"] = {
            "_population": {"n": int(len(crowd)), "L3": int(crowd.L3.nunique()),
                            "feature": col},
            "primary": evaluate(f"{col} <= q10 causal", crowd,
                                causal_pctile_flag(crowd, col, 0.10, low_tail=True,
                                                   min_prior=100),
                                note="capitulation des top traders sans cascade"),
        }
    except Exception as e:                                   # noqa: BLE001
        res["CROWD_WASHOUT_NO_CASCADE"] = {"error": str(e)}
        print("   ERREUR:", e, flush=True)

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/event_family_raw.json", "w") as f:
        json.dump(res, f, indent=2, default=str)
    print("\nécrit:", f"{OUT}/event_family_raw.json")


if __name__ == "__main__":
    main()
