#!/usr/bin/env python
"""T2b — Les "episodes independants" gagnes par la reformulation par symbole
sont-ils VRAIMENT independants, ou juste l'etat macro BTC porte par 49 symboles ?

C'est le piege du declustering redecouvert 4 fois dans ce projet. Trois mesures :
  1. recouvrement entre l'etat ON local (R1/R2/R3) et l'etat ON macro (B0)
  2. concordance cross-symbole intra-jour de l'etat local (si tous les symboles
     ont le meme etat le meme jour, ce ne sont pas des episodes independants)
  3. ETA recalcule sur l'unite JOUR CALENDAIRE (L2), la plus conservatrice des
     trois unites imposees par le briefing
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate import decluster, Z_ALPHA, Z_POWER, HAIRCUT  # noqa: E402
from t2_vol_gate_reformulations import causal_pctile, TOP_FRAC  # noqa: E402

ROOT = Path("/home/qbee/futur")
OUT = Path(__file__).resolve().parent
COST = 14.0


def main():
    df = pd.read_parquet(ROOT / "data/events/liq_cascade_dataset.parquet")
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df[df["label_full"] & df["fwd_4h"].notna()].copy()
    df = df.sort_values("event_time").reset_index(drop=True)
    b = df[(df["kind"] == "LONG_CASCADE") & (df["n_events_sym_24h"] >= 2)].copy().reset_index(drop=True)
    b["abs_px_ret_1h"] = b["px_ret_1h"].abs()
    b["abs_oi_drop_z"] = b["oi_drop_z"].abs()
    b["_g"] = "ALL"

    states = {}
    states["B0"] = pd.Series(causal_pctile(b, "btc_vol_24h", by="_g"), index=b.index) >= (1 - TOP_FRAC)
    for name, col in [("R1", "vol_24h"), ("R2", "abs_px_ret_1h"), ("R3", "abs_oi_drop_z")]:
        p = pd.Series(causal_pctile(b, col, by="symbol"), index=b.index)
        states[name] = p >= (1 - TOP_FRAC)
        states[name][p.isna()] = False

    b["day"] = b["event_time"].dt.floor("D")
    out = {"n_base": int(len(b)), "top_frac": TOP_FRAC, "overlap_with_macro_B0": {},
           "intra_day_cross_symbol_concordance": {}, "eta_on_calendar_day_unit": {}}

    B0 = states["B0"]
    for name in ("R1", "R2", "R3"):
        S = states[name]
        inter = int((S & B0).sum()); union = int((S | B0).sum())
        out["overlap_with_macro_B0"][name] = {
            "jaccard": round(inter / union, 3) if union else None,
            "P(local_ON | macro_ON)": round(float(S[B0].mean()), 3),
            "P(local_ON | macro_OFF)": round(float(S[~B0].mean()), 3),
            "lift": round(float(S[B0].mean() / max(S[~B0].mean(), 1e-9)), 2)}

        # concordance cross-symbole : sur les jours avec >=3 symboles distincts
        d = b.copy(); d["on"] = S.values
        g = d.groupby("day").agg(nsym=("symbol", "nunique"), n=("on", "size"), on=("on", "mean"))
        g = g[g["nsym"] >= 3]
        # part des jours ou TOUS les events du jour partagent le meme etat
        out["intra_day_cross_symbol_concordance"][name] = {
            "n_days_ge3_symbols": int(len(g)),
            "pct_days_unanimous_state": round(float(((g["on"] == 0) | (g["on"] == 1)).mean()), 3),
            "mean_within_day_state_share": round(float(np.maximum(g["on"], 1 - g["on"]).mean()), 3),
            "expected_if_independent": round(float(np.maximum(S.mean(), 1 - S.mean())), 3)}

    # ETA sur l'unite JOUR (L2) : la plus conservatrice
    for name in ("B0", "R1", "R2", "R3"):
        d = b.copy()
        d["on"] = states[name].values
        d["bps"] = d["fwd_4h"] * 1e4 - COST
        d = decluster(d)
        ea = d[d["on"]].groupby("L2")["bps"].mean()
        eb = d[~d["on"]].groupby("L2")["bps"].mean()
        delta = float(ea.mean() - eb.mean())
        var = ea.var(ddof=1) + eb.var(ddof=1)
        eff = HAIRCUT * abs(delta)
        nreq = (Z_ALPHA + Z_POWER) ** 2 * var / eff ** 2 if eff > 0 else np.inf
        tmax = d["event_time"].max(); cut = tmax - pd.DateOffset(months=6)
        weeks = (tmax - cut).total_seconds() / (7 * 86400)
        rate = d[(d["event_time"] >= cut) & d["on"]]["L2"].nunique() / weeks
        eta_w = nreq / rate if rate > 0 else np.inf
        se = np.sqrt(ea.var(ddof=1) / len(ea) + eb.var(ddof=1) / len(eb))
        out["eta_on_calendar_day_unit"][name] = {
            "n_on_days": int(len(ea)), "n_off_days": int(len(eb)),
            "delta_bps": round(delta, 2), "welch_t": round(float(delta / se), 2),
            "n_required_on_days": int(nreq) if np.isfinite(nreq) else None,
            "on_day_rate_per_week_last6m": round(float(rate), 3),
            "eta_years": round(float(eta_w / 52.18), 2) if np.isfinite(eta_w) else None}

    (OUT / "t2b_gate_independence_check.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
