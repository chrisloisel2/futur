#!/usr/bin/env python3
"""
scripts/analyze_trade_history.py
─────────────────────────────────────────────────────────────────────────────
Analyse du JOURNAL DE TRANSACTIONS (tapes OOS des moteurs) — ce qui marche,
ce qui casse, et SURTOUT la concentration des pertes : « quelques trades qui
mangent une grosse partie des gains ».

Trades = net de frais (14 bps). Portefeuille = les 3 moteurs du stack live
(cascade + premium + crowding). Les 2 moteurs rejetés (ignition/spillover)
sont analysés à part pour comparaison.
Sortie : reports/liq_cascade/TRADE_HISTORY_ANALYSIS.{json,md}
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "reports" / "liq_cascade"
STACK = {"LIQ_CASCADE": "fwd_4h", "PREMIUM_DISLOCATION": "fwd_4h",
         "CROWDING_REVERSAL": "fwd_24h"}
REJECTED = {"FLOW_IGNITION": "fwd_8h", "BTC_SPILLOVER": "fwd_4h"}


def load(engines):
    frames = []
    for name in engines:
        p = DIR / f"{name}_trades.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d["engine"] = name
        d["event_time"] = pd.to_datetime(d["event_time"], utc=True)
        d = d[np.isfinite(d["net"])]
        frames.append(d[["event_time", "symbol", "engine", "net", "score"]])
    return pd.concat(frames, ignore_index=True).sort_values("event_time") if frames else pd.DataFrame()


def pf(net):
    g = net[net > 0].sum(); l = abs(net[net < 0].sum())
    return float(g / l) if l > 0 else float("inf")


def basic(net):
    return {"n": int(len(net)), "win_rate": round(float((net > 0).mean()), 3),
            "gross_win": round(float(net[net > 0].sum()), 4),
            "gross_loss": round(float(net[net < 0].sum()), 4),
            "net": round(float(net.sum()), 4), "pf": round(pf(net), 3),
            "expectancy_bps": round(float(net.mean()) * 1e4, 2),
            "avg_win_bps": round(float(net[net > 0].mean()) * 1e4, 1),
            "avg_loss_bps": round(float(net[net < 0].mean()) * 1e4, 1)}


def tail_concentration(df):
    """La question de l'utilisateur : combien les pires trades coûtent."""
    net = df["net"].values
    total_net = net.sum()
    gross_win = net[net > 0].sum()
    order = np.argsort(net)          # pires d'abord
    out = {"total_net_pct_sized10": round(float(total_net * 0.10 * 100), 2)}
    rows = []
    for frac in (0.005, 0.01, 0.02, 0.05, 0.10):
        k = max(1, int(len(net) * frac))
        worst = net[order[:k]]
        cost = worst.sum()
        net_without = total_net - cost
        rows.append({
            "worst_pct": frac * 100, "n_trades": k,
            "cost": round(float(cost), 4),
            "pct_of_gross_win_erased": round(float(-cost / gross_win * 100), 1),
            "net_with": round(float(total_net), 4),
            "net_without": round(float(net_without), 4),
            "uplift_x": round(float(net_without / total_net), 2) if total_net > 0 else None,
        })
    out["ladder"] = rows
    # top 15 pires trades individuels
    worst_idx = order[:15]
    out["worst_trades"] = [{
        "date": str(df.iloc[i]["event_time"])[:10], "symbol": df.iloc[i]["symbol"],
        "engine": df.iloc[i]["engine"], "net_bps": round(float(df.iloc[i]["net"]) * 1e4, 0),
        "score": round(float(df.iloc[i]["score"]), 3)} for i in worst_idx]
    return out


def by_group(df, col):
    rows = []
    for key, g in df.groupby(col):
        net = g["net"].values
        rows.append({col: str(key), "n": int(len(g)),
                     "net_bps_sum": round(float(net.sum()) * 1e4, 0),
                     "expectancy_bps": round(float(net.mean()) * 1e4, 1),
                     "pf": round(pf(net), 2),
                     "win_rate": round(float((net > 0).mean()), 2)})
    return sorted(rows, key=lambda r: r["net_bps_sum"])


def main():
    df = load(STACK)
    rej = load(REJECTED)
    res = {"stack_overall": basic(df["net"].values),
           "tail_concentration": tail_concentration(df),
           "by_engine": by_group(df, "engine"),
           "by_symbol_worst": by_group(df, "symbol")[:12],
           "by_symbol_best": by_group(df, "symbol")[-12:][::-1],
           "by_year": by_group(df.assign(year=df["event_time"].dt.year), "year"),
           "rejected_overall": basic(rej["net"].values) if not rej.empty else {}}

    # score band analysis : les gros perdants sont-ils à haute ou basse conviction ?
    df2 = df.copy()
    df2["score_band"] = pd.qcut(df2["score"], 4, labels=["Q1_bas", "Q2", "Q3", "Q4_haut"])
    res["by_score_band"] = by_group(df2, "score_band")

    (DIR / "TRADE_HISTORY_ANALYSIS.json").write_text(json.dumps(res, indent=2, default=str))

    o = res["stack_overall"]; t = res["tail_concentration"]
    L = ["# Analyse du journal de transactions — stack 3 moteurs (OOS, net de frais)\n",
         f"**{o['n']} trades · win rate {o['win_rate']*100:.0f}% · profit factor {o['pf']} · "
         f"espérance {o['expectancy_bps']:+.1f} bps/trade**",
         f"gain moyen +{o['avg_win_bps']:.0f} bps · perte moyenne {o['avg_loss_bps']:.0f} bps "
         f"(ratio {abs(o['avg_win_bps']/o['avg_loss_bps']):.2f})\n",
         "## ⚠ Concentration des pertes — TA question\n",
         "| pires trades | n | coût | % des gains bruts effacé | uplift si retirés |",
         "|---|---:|---:|---:|---:|"]
    for r in t["ladder"]:
        L.append(f"| {r['worst_pct']:.1f}% | {r['n_trades']} | {r['cost']:.3f} | "
                 f"**{r['pct_of_gross_win_erased']:.0f}%** | ×{r['uplift_x']} |")
    L.append("\n**Les 15 pires trades individuels :**\n")
    L.append("| date | actif | moteur | net (bps) | score |")
    L.append("|---|---|---|---:|---:|")
    for w in t["worst_trades"]:
        L.append(f"| {w['date']} | {w['symbol'][:-4]} | {w['engine']} | {w['net_bps']:.0f} | {w['score']} |")
    L.append("\n## Par moteur\n| moteur | n | net (bps cumul) | espérance | PF | WR |\n|---|---:|---:|---:|---:|---:|")
    for r in res["by_engine"]:
        L.append(f"| {r['engine']} | {r['n']} | {r['net_bps_sum']:.0f} | {r['expectancy_bps']:+.1f} | {r['pf']} | {r['win_rate']} |")
    L.append("\n## Actifs qui DÉTRUISENT (net cumul le plus négatif)\n| actif | n | net (bps) | esp | PF |\n|---|---:|---:|---:|---:|")
    for r in res["by_symbol_worst"]:
        L.append(f"| {r['symbol'][:-4]} | {r['n']} | {r['net_bps_sum']:.0f} | {r['expectancy_bps']:+.1f} | {r['pf']} |")
    L.append("\n## Actifs qui MARCHENT (net cumul le plus positif)\n| actif | n | net (bps) | esp | PF |\n|---|---:|---:|---:|---:|")
    for r in res["by_symbol_best"]:
        L.append(f"| {r['symbol'][:-4]} | {r['n']} | {r['net_bps_sum']:.0f} | {r['expectancy_bps']:+.1f} | {r['pf']} |")
    L.append("\n## Par bande de conviction (score du modèle)\n| bande | n | net (bps) | esp | PF | WR |\n|---|---:|---:|---:|---:|---:|")
    for r in res["by_score_band"]:
        L.append(f"| {r['score_band']} | {r['n']} | {r['net_bps_sum']:.0f} | {r['expectancy_bps']:+.1f} | {r['pf']} | {r['win_rate']} |")
    L.append("\n## Par année\n| année | n | net (bps) | esp | PF |\n|---|---:|---:|---:|---:|")
    for r in res["by_year"]:
        L.append(f"| {r['year']} | {r['n']} | {r['net_bps_sum']:.0f} | {r['expectancy_bps']:+.1f} | {r['pf']} |")
    (DIR / "TRADE_HISTORY_ANALYSIS.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
