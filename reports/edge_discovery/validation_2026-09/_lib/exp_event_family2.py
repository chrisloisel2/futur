"""#18 PREMIUM_EXTREME_THEN_CASCADE et #19 CROWD_WASHOUT_NO_CASCADE.

Ces deux datasets portent leurs PROPRES types d'événements (PREM_CAPITULATION /
PREM_FOMO, CROWD_WASHOUT) : le mécanisme réclamé est le type d'événement lui-même,
pas un conditionnement d'une population de cascades. On teste donc :
  (1) la population inconditionnelle du type d'événement (c'est la réclamation) ;
  (2) le conditionnement par extrémité causale de la feature de crowding/premium,
      en bras A − bras B sur cette même population.
Les deux lectures (épisode et événement + SE cluster-robuste) sont produites.
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
from exp_event_family import causal_pctile_flag, evaluate            # noqa: E402

OUT = "/home/qbee/futur/reports/edge_discovery/validation_2026-09/_lib/out"
DATA = "/home/qbee/futur/data/events"


def load(fname: str, kind: str, fwd: str = "fwd_4h", since: str = "2022-01-01") -> pd.DataFrame:
    d = pd.read_parquet(f"{DATA}/{fname}")
    d["event_time"] = pd.to_datetime(d["event_time"], utc=True)
    d = d[(d["kind"] == kind) & d[fwd].notna()
          & (d["event_time"] >= pd.Timestamp(since, tz="UTC"))].copy()
    d = d.sort_values("event_time").reset_index(drop=True)
    d["fwd_4h"] = d[fwd]
    return ev2.add_declustering(d)


def unconditional(df: pd.DataFrame, label: str) -> dict:
    """La réclamation elle-même : le type d'événement porte-t-il un edge ?"""
    ep = ev2.gate_arm(df)
    ev = ew_cluster(df, label)
    print(f"  {label:38s} n={len(df):6d} L3={ep['n_independent_L3']:5d} | "
          f"episode net14={ep['net_bps']:8.2f} t={str(ep['t_stat_declustered']):>7s} | "
          f"event net14={ev['net14_event_weighted']:8.2f} t_cl={str(ev['t_cluster_robust']):>6s} "
          f"| net28={ep['net_bps_stress28']:7.2f} yrs+={ep['n_years_positive']}/{ep['n_years']}",
          flush=True)
    return {"episode_level": ep, "event_weighted": ev}


def main():
    res = {}

    print("[#18 PREMIUM_EXTREME_THEN_CASCADE]", flush=True)
    block = {}
    for kind in ("PREM_CAPITULATION", "PREM_FOMO"):
        d = load("premium_dataset.parquet", kind)
        col = "prem_z_at" if "prem_z_at" in d.columns else "prem_at"
        block[kind] = {
            "_n": int(len(d)), "_L3": int(d.L3.nunique()), "_feature": col,
            "unconditional": unconditional(d, f"{kind} inconditionnel"),
            "extreme_tail": evaluate(f"{kind} {col}<=q10 causal", d,
                                     causal_pctile_flag(d, col, 0.10, low_tail=True),
                                     note="premium extrême (queue basse) vs reste"),
            "high_tail": evaluate(f"{kind} {col}>=q90 causal", d,
                                  causal_pctile_flag(d, col, 0.90),
                                  note="queue haute (contrôle de direction)"),
        }
    res["PREMIUM_EXTREME_THEN_CASCADE"] = block

    print("[#19 CROWD_WASHOUT_NO_CASCADE]", flush=True)
    d = load("crowding_dataset.parquet", "CROWD_WASHOUT")
    col = "toptrader_z_at" if "toptrader_z_at" in d.columns else "toptrader_z"
    res["CROWD_WASHOUT_NO_CASCADE"] = {
        "_n": int(len(d)), "_L3": int(d.L3.nunique()), "_feature": col,
        "unconditional": unconditional(d, "CROWD_WASHOUT inconditionnel"),
        "extreme_tail": evaluate(f"CROWD_WASHOUT {col}<=q10 causal", d,
                                 causal_pctile_flag(d, col, 0.10, low_tail=True, min_prior=100),
                                 note="capitulation la plus extrême vs reste"),
    }

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/event_family2_raw.json", "w") as f:
        json.dump(res, f, indent=2, default=str)
    print("\nécrit:", f"{OUT}/event_family2_raw.json")


if __name__ == "__main__":
    main()
