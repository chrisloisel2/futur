#!/usr/bin/env python3
"""
scripts/measure_vol_risk_premium.py
─────────────────────────────────────────────────────────────────────────────
MESURE (pas un moteur) du variance risk premium crypto sur données réelles :
DVOL Deribit (vol implicite 30j) vs vol réalisée des 30 jours SUIVANTS.

Littérature : le VRP (IV > RV subséquente) est la base économique du short-vol
systématique (Quantpedia VRP effect ; Anchorage 2026 covered calls BTC).
Question posée ici, niveau 1 doctrine : le premium existe-t-il, de combien,
avec quelle queue ? (Niveau 2 = conversion portefeuille — PAS cet objet.)

Méthode :
  • quotidien (chevauchant, pour la stat d'existence) : prem(t) = IV(t) − RV(t+1→t+30) ;
  • mensuel NON-chevauchant (pour le P&L) : vendeur d'un variance swap 30j,
    P&L par unité de vega ≈ (IV² − RV²)/(2·IV) en points de vol ;
  • sizing illustratif : vega tel que 1 pt de vol = 0,10 % d'equity.

⚠ CAVEATS (verdict = MESURE, pas déployable) :
  – approximation variance swap (réel = straddles delta-hedgés, hedge discret) ;
  – AUCUN coût inclus (spreads options Deribit ≈ 1-2 pts de vol = matériel) ;
  – DVOL = indice, pas un strike exécutable ; marge/liquidation non modélisées.
Sortie : reports/options/VOL_RISK_PREMIUM.{json,md}
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DVOL = ROOT / "data" / "options_backfill" / "deribit"
OUT = ROOT / "reports" / "options"
VEGA_PCT_PER_VOLPT = 0.001            # 1 pt de vol = 0,10 % d'equity


def realized_vol_next30(sym: str) -> pd.Series:
    px = pd.read_parquet(ROOT / "data" / "enriched" / f"{sym}_1h_enriched.parquet",
                         columns=["datetime", "close"])
    px["datetime"] = pd.to_datetime(px["datetime"], utc=True)
    c = px.set_index("datetime")["close"].resample("1D").last().dropna()
    lr = np.log(c / c.shift(1))
    # RV annualisée (%) des 30 jours SUIVANTS (fenêtre décalée de -30, future)
    fwd = lr.rolling(30).std().shift(-30) * np.sqrt(365) * 100
    return fwd


def analyze(ccy: str, sym: str) -> dict:
    dv = pd.read_parquet(DVOL / f"DVOL_{ccy}_1d.parquet")
    iv = dv.set_index(pd.to_datetime(dv["ts"], utc=True))["close"]
    iv.index = iv.index.normalize()
    rv = realized_vol_next30(sym)
    df = pd.DataFrame({"iv": iv, "rv": rv}).dropna()
    df["prem"] = df["iv"] - df["rv"]
    daily = {
        "n_days": int(len(df)),
        "mean_iv": round(float(df["iv"].mean()), 1),
        "mean_rv_next30": round(float(df["rv"].mean()), 1),
        "mean_prem_volpts": round(float(df["prem"].mean()), 2),
        "pct_days_positive": round(float((df["prem"] > 0).mean()), 3),
        "p05_prem": round(float(df["prem"].quantile(0.05)), 1),
        "worst_prem": round(float(df["prem"].min()), 1),
    }
    # mensuel non-chevauchant : entrée = 1er jour dispo du mois
    m = df.groupby(df.index.to_period("M")).head(1).copy()
    m["pnl_volpts"] = (m["iv"] ** 2 - m["rv"] ** 2) / (2 * m["iv"])
    m["pnl_equity"] = m["pnl_volpts"] * VEGA_PCT_PER_VOLPT
    eq = (1 + m["pnl_equity"]).cumprod()
    yrs = (m.index[-1] - m.index[0]).days / 365.25
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    monthly = {
        "n_months": int(len(m)),
        "hit_rate": round(float((m["pnl_volpts"] > 0).mean()), 3),
        "mean_pnl_volpts": round(float(m["pnl_volpts"].mean()), 2),
        "worst_month_volpts": round(float(m["pnl_volpts"].min()), 1),
        "worst_month_date": str(m["pnl_volpts"].idxmin()),
        "sized_ann_return": round(float(eq.iloc[-1] ** (1 / yrs) - 1), 4),
        "sized_maxdd": round(dd, 4),
        "by_year": {int(y): {"n": int(len(g)),
                             "mean_volpts": round(float(g["pnl_volpts"].mean()), 1),
                             "hit": round(float((g["pnl_volpts"] > 0).mean()), 2)}
                    for y, g in m.groupby(m.index.year)},
    }
    return {"daily": daily, "monthly_short_var": monthly}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res, L = {}, ["# Variance Risk Premium crypto — MESURE (DVOL vs RV+30j)\n",
                  "Vendeur de variance 30j, mensuel non-chevauchant, vega "
                  "0,10 %/pt. ⚠ SANS coûts (spreads ≈1-2 pts), approximation "
                  "variance swap — mesure d'existence, PAS un backtest déployable.\n"]
    for ccy, sym in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        r = analyze(ccy, sym)
        res[ccy] = r
        d, mo = r["daily"], r["monthly_short_var"]
        L.append(f"\n## {ccy}\n"
                 f"- IV moyenne {d['mean_iv']}% vs RV+30j {d['mean_rv_next30']}% → "
                 f"**premium moyen {d['mean_prem_volpts']} pts, positif "
                 f"{d['pct_days_positive']*100:.0f}% des jours** "
                 f"(p05 {d['p05_prem']}, pire {d['worst_prem']})\n"
                 f"- Vendeur mensuel : hit {mo['hit_rate']*100:.0f}%, moyenne "
                 f"{mo['mean_pnl_volpts']} pts/mois, **pire mois "
                 f"{mo['worst_month_volpts']} pts ({mo['worst_month_date'][:10]})**\n"
                 f"- Sizé (1 pt = 0,10% eq.) : {mo['sized_ann_return']*100:+.1f}%/an, "
                 f"maxDD {mo['sized_maxdd']*100:.1f}%\n")
        L.append("| année | n | moy pts | hit |")
        L.append("|---|---:|---:|---:|")
        for y, g in sorted(mo["by_year"].items()):
            L.append(f"| {y} | {g['n']} | {g['mean_volpts']} | {g['hit']} |")
        print(f"{ccy}: prem moyen {d['mean_prem_volpts']} pts "
              f"({d['pct_days_positive']*100:.0f}% jours >0) · vendeur mensuel "
              f"hit {mo['hit_rate']*100:.0f}% · pire mois {mo['worst_month_volpts']} pts "
              f"· sizé {mo['sized_ann_return']*100:+.1f}%/an DD {mo['sized_maxdd']*100:.1f}%",
              flush=True)
    (OUT / "VOL_RISK_PREMIUM.json").write_text(json.dumps(res, indent=2))
    (OUT / "VOL_RISK_PREMIUM.md").write_text("\n".join(L))
    print(f"→ {OUT}/VOL_RISK_PREMIUM.md")


if __name__ == "__main__":
    main()
