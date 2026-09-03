#!/usr/bin/env python
"""T2 — LIQ_REPEAT_VOL_GATE : reformulations R1/R2/R3 preenregistrees.

Question : le meme mecanisme economique ("n'activer le repeat-cascade que sous
stress") existe-t-il sous une forme dont les EPISODES INDEPENDANTS sont beaucoup
plus nombreux que l'etat macro lent "vol BTC 24h elevee" (268 episodes -> ETA 28-38 ans) ?

Gates testes (tous decides AVANT de voir un resultat) :
  B0  vol BTC 24h macro (reproduction du gate original)
  R1  vol realisee 24h DU SYMBOLE (etat local, 49 symboles en parallele)
  R2  vol rapide : |px_ret_1h| du symbole (change plusieurs fois/semaine)
  R3  intensite de l'event lui-meme : |oi_drop_z| (change a chaque event)

Etat ON = decile superieur 30% (preenregistre), percentile CAUSAL trailing par
symbole (rang parmi les 200 events PRECEDENTS du meme symbole, min 30, shift(1)).

Unite de declustering supplementaire L4 = EPISODE DE REGIME DU GATE : run maximal
d'events consecutifs (par symbole pour R1/R3, global pour B0) partageant le meme
etat de gate, coupe des qu'un ecart > 24h apparait. C'est l'unite qui avait
detruit la preuve du gate original (949 trades -> 268 episodes).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate import decluster, _block_boot_ci, Z_ALPHA, Z_POWER, HAIRCUT  # noqa: E402

ROOT = Path("/home/qbee/futur")
OUT = Path(__file__).resolve().parent
COST = 14.0
TOP_FRAC = 0.30          # preenregistre
TRAIL_N = 200            # events precedents du meme symbole
MIN_TRAIL = 30


def causal_pctile(df, col, by="symbol"):
    """Rang du point courant parmi les TRAIL_N points PRECEDENTS du meme symbole."""
    out = np.full(len(df), np.nan)
    for _, g in df.groupby(by, sort=False):
        v = g[col].values.astype(float)
        idx = df.index.get_indexer(g.index)
        for i in range(len(v)):
            lo = max(0, i - TRAIL_N)
            past = v[lo:i]
            past = past[np.isfinite(past)]
            if len(past) >= MIN_TRAIL and np.isfinite(v[i]):
                out[idx[i]] = (past < v[i]).mean()
    return out


def gate_episodes(df, state_col, by_symbol):
    """L4 : runs maximaux de meme etat de gate (par symbole ou global), coupes a 24h."""
    d = df.sort_values("event_time")
    ids = np.zeros(len(d), dtype=np.int64)
    k = 0
    groups = d.groupby("symbol", sort=False) if by_symbol else [("_all", d)]
    for _, g in groups:
        t = g["event_time"].values
        s = g[state_col].values
        newep = np.r_[True, (s[1:] != s[:-1]) | (np.diff(t) > pd.Timedelta("24h").to_timedelta64())]
        cur = np.cumsum(newep) + k
        k = cur.max()
        ids[d.index.get_indexer(g.index)] = cur
    return pd.Series(ids, index=d.index).reindex(df.index).values


def _t(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan


def eval_gate(base, state, name, by_symbol, recent_months=6):
    d = base.copy()
    d["_on"] = state
    d = d[d["_on"].notna()].copy()
    d["_on"] = d["_on"].astype(bool)
    d["bps"] = d["fwd_4h"].astype(float) * 1e4 - COST
    d = d[np.isfinite(d["bps"])]
    d = decluster(d)
    d["L4"] = gate_episodes(d, "_on", by_symbol)

    on, off = d[d["_on"]], d[~d["_on"]]
    res = {"gate": name, "n_on_raw": int(len(on)), "n_off_raw": int(len(off)),
           "on_share": round(float(d["_on"].mean()), 3),
           "mean_on_bps_raw": round(float(on["bps"].mean()), 2),
           "mean_off_bps_raw": round(float(off["bps"].mean()), 2),
           "delta_bps_raw": round(float(on["bps"].mean() - off["bps"].mean()), 2)}

    for L in ("L1", "L2", "L3", "L4"):
        ea = on.groupby(L)["bps"].mean()
        eb = off.groupby(L)["bps"].mean()
        if len(ea) < 5 or len(eb) < 5:
            res[L] = {"status": "TOO_FEW"}
            continue
        delta = float(ea.mean() - eb.mean())
        se = np.sqrt(ea.var(ddof=1) / len(ea) + eb.var(ddof=1) / len(eb))
        res[L] = {"n_ep_on": int(len(ea)), "n_ep_off": int(len(eb)),
                  "on_bps": round(float(ea.mean()), 2), "off_bps": round(float(eb.mean()), 2),
                  "delta_bps": round(delta, 2),
                  "welch_t": round(float(delta / se), 2) if se > 0 else None,
                  "on_t_vs_zero": round(_t(ea.values), 2),
                  "on_ci95": [round(v, 2) for v in _block_boot_ci(ea.values)]}

    # ── ETA sur l'unite L4 (episodes de regime du gate = l'unite qui a tue l'original)
    ea = on.groupby("L4")["bps"].mean()
    eb = off.groupby("L4")["bps"].mean()
    if len(ea) >= 5 and len(eb) >= 5:
        delta = float(ea.mean() - eb.mean())
        var = ea.var(ddof=1) + eb.var(ddof=1)
        eff = HAIRCUT * abs(delta)
        nreq = (Z_ALPHA + Z_POWER) ** 2 * var / eff ** 2 if eff > 0 else np.inf
        tmax = d["event_time"].max()
        cut = tmax - pd.DateOffset(months=recent_months)
        weeks = max((tmax - cut).total_seconds() / (7 * 86400), 1e-9)
        rate = on[on["event_time"] >= cut]["L4"].nunique() / weeks
        eta_w = nreq / rate if rate > 0 and np.isfinite(nreq) else np.inf
        res["ETA"] = {"unit": "L4 gate-regime episode",
                      "delta_bps": round(delta, 2),
                      "n_required_on_episodes": int(nreq) if np.isfinite(nreq) else None,
                      "on_episode_rate_per_week_last6m": round(float(rate), 3),
                      "eta_days": round(float(eta_w * 7), 1) if np.isfinite(eta_w) else None,
                      "eta_years": round(float(eta_w / 52.18), 2) if np.isfinite(eta_w) else None}
        # sanity : combien d'episodes de regime independants dans TOUT l'historique
        res["ETA"]["total_on_episodes_history"] = int(on["L4"].nunique())

    # stabilite annuelle du delta (sur episodes L4)
    yrs = {}
    for y, g in d.groupby(d["event_time"].dt.year):
        a = g[g["_on"]].groupby("L4")["bps"].mean()
        b = g[~g["_on"]].groupby("L4")["bps"].mean()
        if len(a) >= 3 and len(b) >= 3:
            yrs[int(y)] = round(float(a.mean() - b.mean()), 2)
    res["delta_by_year"] = yrs
    res["years_positive"] = f"{sum(v > 0 for v in yrs.values())}/{len(yrs)}"
    # stress 28bps : le delta est invariant au cout (meme cout des deux cotes) ->
    # ce qui compte est que le bras ON reste net-positif a 28bps
    res["on_net_bps_stress28_L4"] = round(float(ea.mean() - 14.0), 2) if len(ea) >= 5 else None
    return res


def main():
    thr = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    df = pd.read_parquet(ROOT / "data/events/liq_cascade_dataset.parquet")
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df[df["label_full"] & df["fwd_4h"].notna()].copy()
    df = df.sort_values("event_time").reset_index(drop=True)

    base = df[(df["kind"] == "LONG_CASCADE") & (df["n_events_sym_24h"] >= thr)].copy()
    base = base.reset_index(drop=True)
    print(f"base population (LIQ_CASCADE_REPEAT, thr>={thr}): n={len(base)}")

    base["abs_px_ret_1h"] = base["px_ret_1h"].abs()
    base["abs_oi_drop_z"] = base["oi_drop_z"].abs()

    out = {"base": {"spec": f"LONG_CASCADE & n_events_sym_24h>={thr} (repeat_variant.py frozen spec)",
                    "n": int(len(base)),
                    "period": [str(base["event_time"].min()), str(base["event_time"].max())],
                    "mean_net_bps_raw": round(float(base["fwd_4h"].mean() * 1e4 - COST), 2)},
           "top_frac_preregistered": TOP_FRAC, "results": {}}

    specs = [
        # (nom, colonne, par-symbole pour le percentile causal, par-symbole pour L4)
        ("B0_btc_vol_24h_macro", "btc_vol_24h", False, False),
        ("R1_symbol_vol_24h", "vol_24h", True, True),
        ("R2_symbol_fast_vol_absret1h", "abs_px_ret_1h", True, True),
        ("R3_event_intensity_oi_drop_z", "abs_oi_drop_z", True, True),
    ]
    for name, col, pct_by_sym, l4_by_sym in specs:
        b = base.copy()
        if pct_by_sym:
            p = causal_pctile(b, col, by="symbol")
        else:
            # macro : rang causal global parmi les 200 events precedents (tous symboles)
            b["_g"] = "ALL"
            p = causal_pctile(b, col, by="_g")
        state = pd.Series(p, index=b.index)
        state = state.where(state.notna(), np.nan)
        on = (state >= (1 - TOP_FRAC))
        on[state.isna()] = np.nan
        out["results"][name] = eval_gate(b, on, name, by_symbol=l4_by_sym)
        print(name, json.dumps(out["results"][name].get("ETA", {}), default=str),
              "L4delta", out["results"][name].get("L4", {}).get("delta_bps"),
              "t", out["results"][name].get("L4", {}).get("welch_t"))

    (OUT / f"t2_vol_gate_reformulations_thr{thr}.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"-> t2_vol_gate_reformulations_thr{thr}.json")


if __name__ == "__main__":
    main()
