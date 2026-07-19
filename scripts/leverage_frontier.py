#!/usr/bin/env python3
"""
scripts/leverage_frontier.py
─────────────────────────────────────────────────────────────────────────────
« Comment augmenter GRANDEMENT les gains » — la réponse honnête, mesurée.

Le SEUL levier légitime à haut Sharpe = LÉVERAGER le cœur DELTA-NEUTRAL
(carry + basis), car il n'a presque pas de risque directionnel. Mais le levier
multiplie AUSSI le risque de queue (funding-flip / blowup de basis). On mesure
les deux : rendement ET stress, à chaque niveau de levier. Pas de repas gratuit.

Cœur delta-neutral = carry funding (BTC/ETH, historique réel) + basis (equity
backtest). Levier L : ret_L = L×ret_core − (L−1)×borrow. Stress = pire fenêtre
30 j × L (ce qu'un mois de funding-flip coûte au levier choisi).
Sortie : reports/liq_cascade/LEVERAGE_FRONTIER.{json,md}
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "liq_cascade"
BORROW_ANN = 0.08          # coût d'emprunt réaliste sur l'excès levier (~8%/an)
CARRY_SIZING = 0.50        # notional carry / actif
FEE_8H = 0.00002           # maker, amorti


def carry_daily():
    frames = []
    for s in ("BTCUSDT", "ETHUSDT"):
        p = ROOT / "data" / "derivatives_backfill" / "binance" / "funding" / f"{s}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d["ts"] = pd.to_datetime(d["timestamp"], utc=True)
        d = d.set_index("ts")["funding_rate"].resample("D").sum()   # ~3×8h/j
        frames.append(d)
    if not frames:
        return None
    fund = pd.concat(frames, axis=1).mean(axis=1)   # blend BTC/ETH
    # carry Δ-neutre : short perp encaisse le funding (>0), sizing appliqué, − fees
    return (fund * CARRY_SIZING - FEE_8H * 3 * CARRY_SIZING).dropna()


def basis_daily():
    p = OUT / "basis_term_equity_daily.parquet"
    if not p.exists():
        return None
    b = pd.read_parquet(p)
    s = pd.Series(b["equity"].values, index=pd.to_datetime(b["date"], utc=True))
    return s.resample("D").last().ffill().pct_change().dropna()


def stats(ret, lev, borrow_d):
    r = lev * ret - (lev - 1) * borrow_d
    eq = (1 + r).cumprod()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    ann = float(eq.iloc[-1] ** (1 / yrs) - 1)
    roll30 = r.rolling(30).sum()
    worst30 = float(roll30.min())     # pire mois (funding-flip stress) au levier L
    return {"leverage": lev, "roi_ann": round(ann, 4),
            "vol": round(float(r.std() * np.sqrt(365)), 4),
            "maxdd": round(dd, 4),
            "sharpe": round(float(r.mean() / (r.std() + 1e-12) * np.sqrt(365)), 2),
            "worst_month": round(worst30, 4)}


def main():
    carry = carry_daily()
    basis = basis_daily()
    idx = carry.index.union(basis.index)
    core = (0.6 * carry.reindex(idx).fillna(0) + 0.4 * basis.reindex(idx).fillna(0))
    core = core[core.index >= pd.Timestamp("2022-01-01", tz="UTC")]
    borrow_d = BORROW_ANN / 365

    rows = [stats(core, L, borrow_d) for L in (1, 2, 3, 4, 5)]
    # part du temps où funding négatif (risque de queue du carry)
    neg = float((carry < 0).mean())

    L = ["# Frontière de LEVIER — cœur delta-neutral (carry+basis), mesuré 2022→2026\n",
         f"Cœur non-levé : {rows[0]['roi_ann']*100:+.1f}%/an, Sharpe {rows[0]['sharpe']}, "
         f"maxDD {rows[0]['maxdd']*100:.1f}%. Funding négatif {neg*100:.0f}% du temps.\n",
         "| levier | ROI/an | vol | maxDD | Sharpe | PIRE MOIS (stress funding-flip) |",
         "|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        L.append(f"| **{r['leverage']}×** | {r['roi_ann']*100:+.1f}% | {r['vol']*100:.1f}% | "
                 f"{r['maxdd']*100:.1f}% | {r['sharpe']} | **{r['worst_month']*100:+.1f}%** |")
    L.append("\n**Lecture honnête** : le levier multiplie le rendement quasi-linéairement "
             "(Sharpe stable) MAIS la colonne « pire mois » montre le prix — un mois de "
             "funding-flip au levier élevé fait très mal. Le Sharpe stable ne protège pas "
             "d'un blowup ponctuel. Choix de RISQUE, pas d'optimisation gratuite.")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "LEVERAGE_FRONTIER.json").write_text(json.dumps(
        {"rows": rows, "funding_negative_pct": neg}, indent=2))
    (OUT / "LEVERAGE_FRONTIER.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
