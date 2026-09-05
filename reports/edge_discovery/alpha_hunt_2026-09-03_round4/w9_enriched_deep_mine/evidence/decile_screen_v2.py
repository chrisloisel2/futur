#!/usr/bin/env python3
"""W9 Phase 2 — balayage par decile CORRIGE (declustering L1 applique DANS chaque decile).

La v1 (decile_screen.py) declusterisait la population entiere PUIS repartissait en deciles :
chaque decile ne recevait alors que les barres qui se trouvaient etre la premiere de la
journee pour leur symbole (~1/10 de l'echantillon, et un conditionnement sur l'heure).
Cette v2 declusterise a l'interieur de chaque decile, ce qui la rend comparable au SCREEN.
Sortie : evidence/DECILE_SCREEN_V2.json
"""
import sys, os, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate2 import load, evaluate, _period_mask
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")
df = load()
res = {}
for period in ("discovery", "oos"):
    for H in (8, 24):
        tab = []
        for dcl in range(10):
            lo, hi = dcl/10.0, (dcl+1)/10.0
            m = (df["minmax_norm_close_20"] >= lo) & (df["minmax_norm_close_20"] < hi if dcl < 9 else df["minmax_norm_close_20"] <= 1.0)
            r = evaluate(f"decile_{dcl}", df, m, +1, H, period=period, family="DECILE",
                         note=f"minmax_norm_close_20 dans [{lo:.1f},{hi:.1f}) — jambe LONG")
            tab.append({k: r.get(k) for k in ("mechanism","n_raw","n_independent_L1","n_independent_L2",
                        "gross_bps","net_bps","edge_vs_control_bps","t_stat_declustered","bootstrap_ci95","verdict")})
        res[f"{period}_H{H}"] = tab
        print(f"\n=== {period} H={H} — declustering DANS le decile (jambe LONG) ===")
        print(pd.DataFrame(tab).to_string(index=False), flush=True)
json.dump(res, open(OUT+"/decile_screen_v2.json","w"), indent=1, default=str)
