#!/usr/bin/env python3
"""
scripts/report_maker_fill_probe.py
─────────────────────────────────────────────────────────────────────────────
Dépouille la sonde de fills maker : taux de fill, temps-au-fill, sélection
adverse — l'économie maker RÉELLE par symbole. À lancer après ≥24h de sonde.

Lecture des résultats :
  • fill_rate : part des ordres post-only remplis (règle conservatrice
    « carnet traversé ») avant TTL 600 s ;
  • adv_bps_60s / _300s : où était le mid après le fill (négatif = le prix a
    continué contre nous — sélection adverse à soustraire de l'économie de
    frais) ;
  • verdict par symbole : gain maker net ≈ (frais taker − frais maker)
    + E[adv] (les deux en bps) — s'il reste positif, le levier est réel.
Sortie : reports/options/MAKER_FILL_PROBE.{json,md}
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "execution_probe"
OUT = ROOT / "reports" / "options"
TAKER_BPS, MAKER_BPS = 5.0, 2.0          # barème par side (à ajuster à ton tier)


def main():
    parts = sorted(SRC.glob("date=*/part-*.parquet"))
    if not parts:
        print("aucune donnée sonde — laisser tourner futur-maker-probe")
        return
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    span_h = ((pd.to_datetime(df["ts_place"]).max()
               - pd.to_datetime(df["ts_place"]).min()).total_seconds() / 3600)
    rows = []
    for sym, g in df.groupby("symbol"):
        filled = g[g["filled"]]
        adv60 = filled["adv_bps_60s"].dropna()
        rows.append({
            "symbol": sym, "n": len(g),
            "fill_rate": round(float(g["filled"].mean()), 3),
            "med_ttf_s": (round(float(filled["ttf_s"].median()), 1)
                          if len(filled) else None),
            "adv60_mean_bps": round(float(adv60.mean()), 2) if len(adv60) else None,
            "adv60_p10_bps": (round(float(adv60.quantile(0.10)), 1)
                              if len(adv60) else None),
            "spread_med_bps": round(float(g["spread_bps"].median()), 2),
            # économie nette maker vs taker par side, sélection adverse déduite
            "maker_edge_bps": (round(TAKER_BPS - MAKER_BPS
                                     + float(adv60.mean()), 2)
                               if len(adv60) else None),
        })
    tab = pd.DataFrame(rows).sort_values("n", ascending=False)
    OUT.mkdir(parents=True, exist_ok=True)
    md = [f"# Sonde fills maker — {len(df)} ordres virtuels sur {span_h:.1f} h\n",
          f"Règle de fill conservatrice (carnet traversé). Barème supposé "
          f"taker {TAKER_BPS} / maker {MAKER_BPS} bps par side.\n",
          tab.to_markdown(index=False),
          "\n\n`maker_edge_bps` > 0 ⟹ le levier post-only reste gagnant "
          "APRÈS sélection adverse (à ce stade : par side, hors requeue)."]
    (OUT / "MAKER_FILL_PROBE.md").write_text("\n".join(md))
    (OUT / "MAKER_FILL_PROBE.json").write_text(
        tab.to_json(orient="records", indent=2))
    print(tab.to_string(index=False))
    print(f"\n→ {OUT}/MAKER_FILL_PROBE.md")


if __name__ == "__main__":
    main()
