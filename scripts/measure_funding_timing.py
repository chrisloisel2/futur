#!/usr/bin/env python3
"""
scripts/measure_funding_timing.py
─────────────────────────────────────────────────────────────────────────────
MESURE niveau-1 (doctrine) du « funding timing » : une position carry
(short perp) gagne-t-elle à SAUTER les fenêtres 8h prédites négatives ?

Prédicteur CAUSAL au début de la fenêtre t : signe du funding réglé en t−1
(persistance). Coût de bascule = entrer/sortir le carry = 2 jambes
(spot+perp) par transition, compté par ALLER-RETOUR complet :
  taker : 28 bps (4 jambes × 7 bps — convention repo) ;
  maker : 8 bps  (4 × 2 bps — l'économie mesurée par la sonde fills).

⚠ Niveau 1 seulement : une amélioration per-fenêtre NE vaut promotion
qu'après test portefeuille (leçon CARRY_GATE_V2 : 3 gates carry morts au
niveau 2 à cause du churn). Sortie : reports/options/FUNDING_TIMING.{json,md}
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FUND = ROOT / "data" / "derivatives_backfill" / "binance" / "funding"
OUT = ROOT / "reports" / "options"
COST_CYCLE = {"taker": 0.0028, "maker": 0.0008}


def analyze(sym: str) -> dict:
    d = pd.read_parquet(FUND / f"{sym}.parquet")
    f = d.set_index(pd.to_datetime(d["timestamp"], utc=True))["funding_rate"].sort_index()
    prev = f.shift(1)
    hold = (prev > 0)                      # règle causale : fenêtre tenue ⟺ f(t-1)>0
    toggles = int((hold != hold.shift(1)).sum())
    yrs = (f.index[-1] - f.index[0]).days / 365.25
    base_ann = float(f.mean() * 3 * 365)
    timed_gross_ann = float(f[hold].sum() / yrs)
    out = {
        "n_windows": int(len(f)), "years": round(yrs, 2),
        "pct_windows_positive": round(float((f > 0).mean()), 3),
        "persistence_pos": round(float((f[prev > 0] > 0).mean()), 3),
        "persistence_neg": round(float((f[prev <= 0] <= 0).mean()), 3),
        "alwayson_ann": round(base_ann, 4),
        "timed_gross_ann": round(timed_gross_ann, 4),
        "participation": round(float(hold.mean()), 3),
        "toggles_per_year": round(toggles / yrs, 1),
    }
    for mode, c in COST_CYCLE.items():
        cost_ann = (toggles / 2) / yrs * c        # 2 toggles = 1 cycle complet
        out[f"timed_net_ann_{mode}"] = round(timed_gross_ann - cost_ann, 4)
        out[f"delta_vs_alwayson_{mode}"] = round(
            timed_gross_ann - cost_ann - base_ann, 4)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res, L = {}, [
        "# Funding timing (niveau 1) — sauter les fenêtres 8h prédites négatives\n",
        "Règle causale : tenir t ⟺ funding(t−1)>0. Coût par cycle complet : "
        "taker 28 bps / maker 8 bps. ⚠ per-fenêtre ≠ portefeuille (niveau 2 requis).\n",
        "| actif | always-on %/an | timé brut | net taker | net maker | Δ maker | particip. | toggles/an |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for sym in ("BTCUSDT", "ETHUSDT"):
        r = analyze(sym)
        res[sym] = r
        L.append(f"| {sym} | {r['alwayson_ann']*100:+.2f}% | "
                 f"{r['timed_gross_ann']*100:+.2f}% | "
                 f"{r['timed_net_ann_taker']*100:+.2f}% | "
                 f"{r['timed_net_ann_maker']*100:+.2f}% | "
                 f"**{r['delta_vs_alwayson_maker']*100:+.2f}%** | "
                 f"{r['participation']*100:.0f}% | {r['toggles_per_year']} |")
        print(f"{sym}: always-on {r['alwayson_ann']*100:+.2f}%/an · timé net maker "
              f"{r['timed_net_ann_maker']*100:+.2f}% (Δ {r['delta_vs_alwayson_maker']*100:+.2f}) "
              f"· net taker {r['timed_net_ann_taker']*100:+.2f}% "
              f"(Δ {r['delta_vs_alwayson_taker']*100:+.2f}) · persistance +{r['persistence_pos']:.2f}/"
              f"−{r['persistence_neg']:.2f} · {r['toggles_per_year']}/an toggles", flush=True)
    L.append("\nPersistance du signe (8h) : BTC "
             f"+{res['BTCUSDT']['persistence_pos']}/−{res['BTCUSDT']['persistence_neg']} · "
             f"ETH +{res['ETHUSDT']['persistence_pos']}/−{res['ETHUSDT']['persistence_neg']}")
    (OUT / "FUNDING_TIMING.json").write_text(json.dumps(res, indent=2))
    (OUT / "FUNDING_TIMING.md").write_text("\n".join(L))
    print(f"→ {OUT}/FUNDING_TIMING.md")


if __name__ == "__main__":
    main()
