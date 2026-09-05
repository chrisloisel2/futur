#!/usr/bin/env python
"""T2c — DIAGNOSTIC SUPPLEMENTAIRE (n'est PAS le critere preenregistre).

Le critere preenregistre de la cible 2 porte sur le DELTA ON-OFF (c'est un GATE
qu'on evalue : ajoute-t-il de la valeur ?). Le lecteur demandera neanmoins :
« et le BRAS ON tout seul, en tant qu'alpha, il donne quoi ? »

Ce script applique le gate §2 standard au bras ON de chaque reformulation.
Il est publie pour completude et NE CHANGE PAS le verdict de la cible 2 :
la question posee au registre est celle du gate, pas celle d'un nouvel alpha,
et toute la famille cascade est de toute facon inexecutable (latence 45.5h
pour un horizon 4h, DECISION_LATENCY_AUDIT_2026-09-05.md).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate import gate  # noqa: E402
from t2_vol_gate_reformulations import causal_pctile, TOP_FRAC  # noqa: E402

ROOT = Path("/home/qbee/futur")
OUT = Path(__file__).resolve().parent


def main():
    df = pd.read_parquet(ROOT / "data/events/liq_cascade_dataset.parquet")
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df[df["label_full"] & df["fwd_4h"].notna()].sort_values("event_time").reset_index(drop=True)
    b = df[(df["kind"] == "LONG_CASCADE") & (df["n_events_sym_24h"] >= 2)].copy().reset_index(drop=True)
    b["abs_px_ret_1h"] = b["px_ret_1h"].abs()
    b["abs_oi_drop_z"] = b["oi_drop_z"].abs()
    b["_g"] = "ALL"

    out = {"caveat": "DIAGNOSTIC SUPPLEMENTAIRE — pas le critere preenregistre (qui est le delta ON-OFF). Ne change aucun verdict.",
           "ungated_base": gate(b, direction="LONG", label="LIQ_CASCADE_REPEAT non gate (base)"),
           "on_arms": {}}
    for name, col, by in (("B0_btc_vol_24h_macro", "btc_vol_24h", "_g"),
                          ("R1_symbol_vol_24h", "vol_24h", "symbol"),
                          ("R2_symbol_fast_vol_absret1h", "abs_px_ret_1h", "symbol"),
                          ("R3_event_intensity_oi_drop_z", "abs_oi_drop_z", "symbol")):
        p = pd.Series(causal_pctile(b, col, by=by), index=b.index)
        on = b[(p >= (1 - TOP_FRAC)).fillna(False)]
        out["on_arms"][name] = gate(on, direction="LONG", label="bras ON de %s" % name)
    (OUT / "t2c_on_arm_supplementary.json").write_text(json.dumps(out, indent=2, default=str))
    for k, v in [("UNGATED", out["ungated_base"])] + list(out["on_arms"].items()):
        print("%-32s n=%5s L3=%5s net14=%7s net28=%7s netL3=%7s tL3=%6s exbest=%7s ETA=%s ans"
              % (k, v.get("n_raw"), v.get("n_independent_L3"), v.get("net_bps"),
                 v.get("net_bps_stress28"), v.get("net_bps_L3_declustered"),
                 v.get("t_stat_declustered"), v.get("ex_best_year_net_bps"), v.get("eta_years")))


if __name__ == "__main__":
    main()
