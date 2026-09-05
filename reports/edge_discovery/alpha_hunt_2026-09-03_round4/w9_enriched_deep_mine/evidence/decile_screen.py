#!/usr/bin/env python3
"""W9 Phase 2 — test de PARCIMONIE : H1a_rev / H3a_rev / H4a / H4b_rev sont-ils quatre
mecanismes, ou un seul effet (« position du close dans le range recent ») vu sous 4 angles ?

Test : balayage par DECILE de `minmax_norm_close_20` (colonne USABLE, causale : rolling
high/low strictement trailing) du rendement forward 24 h demeane par jour calendaire.
Si la relation est monotone, il n'y a qu'UN effet et le livrable est un screen continu.
Sortie : evidence/DECILE_SCREEN.json + tableaux imprimes.
"""
import sys, os, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate2 import load, evaluate, decluster, block_boot, _period_mask, COST, COST_STRESS
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")

df = load()
res = {}
for period in ("discovery", "oos"):
    per = _period_mask(df, period)
    for H in (8, 24):
        ok = df[f"fwd{H}"].notna() & per & df["minmax_norm_close_20"].notna()
        pop = df[ok].copy()
        daymean = pop.groupby("date")[f"fwd{H}"].mean()
        pop["ret_dm"] = pop[f"fwd{H}"] - pop["date"].map(daymean).values
        pop["dec"] = np.clip((pop["minmax_norm_close_20"] * 10).astype(int), 0, 9)
        L1 = decluster(pop)
        tab = []
        for dcl, g in L1.groupby("dec"):
            L2 = g.groupby("date")["ret_dm"].mean()
            L2r = g.groupby("date")[f"fwd{H}"].mean()
            wk = g.groupby("date")["week"].first().reindex(L2.index).values
            n2 = len(L2); mu = float(L2.mean()); sd = float(L2.std(ddof=1))
            t = mu / (sd / np.sqrt(n2)) if sd > 0 and n2 > 1 else 0.0
            ci = block_boot(L2.values, wk) if n2 > 20 else [np.nan, np.nan]
            tab.append(dict(decile=int(dcl), n_raw=int(len(g)), n_L2=n2,
                            edge_dm_bps=round(mu, 2), t=round(float(t), 2),
                            ci95=[round(float(ci[0]), 2), round(float(ci[1]), 2)],
                            gross_long_bps=round(float(L2r.mean()), 2)))
        res[f"{period}_H{H}"] = tab
        print(f"\n=== {period} H={H} : rendement {H}h demeane par decile de minmax_norm_close_20 ===")
        print(pd.DataFrame(tab).to_string(index=False))

# --- le screen retenu : decile 9 (haut de range) en SHORT / GATE, vs decile 0 en LONG
sc = {}
for period in ("discovery", "oos"):
    for H in (8, 24):
        top = df["minmax_norm_close_20"] >= 0.90
        bot = df["minmax_norm_close_20"] <= 0.10
        sc[f"SCREEN_top_decile_short_{period}_H{H}"] = evaluate(
            "SCREEN_range_top_decile_short", df, top, -1, H, period=period, family="SCREEN",
            note="decile haut de minmax_norm_close_20 — livrable en SCREEN/GATE, pas en short standalone")
        sc[f"SCREEN_bottom_decile_long_{period}_H{H}"] = evaluate(
            "SCREEN_range_bottom_decile_long", df, bot, +1, H, period=period, family="SCREEN",
            note="decile bas de minmax_norm_close_20 — jambe LONG rapportee separement (politique SHORT)")

# --- robustesse : hors DOGE/XRP (sources SPOT) et hors 2017-2019 (BTC/ETH seuls)
rob = {}
noswap = ~df["symbol"].isin(["DOGEUSDT", "XRPUSDT"])
post2020 = df["datetime"] >= pd.Timestamp("2020-01-01", tz="UTC")
for tag, extra in (("hors_DOGE_XRP_spot", noswap), ("depuis_2020", post2020),
                   ("hors_spot_et_depuis_2020", noswap & post2020)):
    rob[tag] = evaluate("SCREEN_range_top_decile_short", df,
                        (df["minmax_norm_close_20"] >= 0.90) & extra, -1, 24,
                        period="discovery", family="SCREEN", note=tag)

json.dump(dict(deciles=res, screen=sc, robustness=rob), open(OUT + "/decile_screen.json", "w"), indent=1, default=str)
print("\n=== SCREEN decile extreme ===")
k = ["mechanism", "period", "horizon_h", "n_raw", "n_independent_L2", "gross_bps", "net_bps",
     "net_bps_stress28", "edge_vs_control_bps", "t_stat_declustered", "ex_best_year",
     "event_rate_per_week", "eta_forward_years", "verdict"]
print(pd.DataFrame(list(sc.values()))[k].to_string(index=False))
print("\n=== robustesse (short decile haut, H=24, decouverte) ===")
print(pd.DataFrame(list(rob.values()))[k].to_string(index=False))
print("\n=== annee par annee du SCREEN (decouverte, H=24) ===")
print(pd.DataFrame(sc["SCREEN_top_decile_short_discovery_H24"]["year_by_year"]).T.to_string())
print("\n=== annee par annee de la jambe LONG decile bas (decouverte, H=24) ===")
print(pd.DataFrame(sc["SCREEN_bottom_decile_long_discovery_H24"]["year_by_year"]).T.to_string())
