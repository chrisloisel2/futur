#!/usr/bin/env python3
"""
scripts/test_listing_age_22_30.py
─────────────────────────────────────────────────────────────────────────────
Test ciblé J+22 → J+30 post-listing (décision utilisateur 2026-07-18).

L'event study (reports/LISTING_EVENT_STUDY.md) démontre un drift négatif
jusqu'à J+21. Avant de brancher un filtre "aucun long < 30 j" dans le
ranker/edge-gate, mesurer EXPLICITEMENT la fenêtre restante J+22→J+30 pour
ne pas extrapoler. Règle : médiane nette < 0 ⇒ le filtre 30 j est justifié
de bout en bout ; sinon, filtre 21 j seulement.

Entrée = close à t0+528 h (J+22), sortie = close à t0+720 h (J+30),
couverture exigée (dernier bar ≤ 2 h de la cible). Net = 40 bps (coûts ×2).

    .venv/bin/python scripts/test_listing_age_22_30.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_perp_listing_event_study import (DATA, BTC_ENRICHED, COST_RT_BPS,
                                                   load_price_series, price_at)

ENTRY_H, EXIT_H = 528, 720          # J+22 → J+30


def main() -> None:
    btc = pd.read_parquet(BTC_ENRICHED, columns=["datetime", "close"])
    btc_s = btc.set_index(pd.DatetimeIndex(btc["datetime"]))["close"].sort_index()

    rows = []
    for p in sorted((DATA / "klines_1h").glob("*.parquet")):
        s = load_price_series(p.stem)
        if s is None or len(s) < 24:
            continue
        t0 = s.index[0]
        t_in, t_out = t0 + pd.Timedelta(hours=ENTRY_H), t0 + pd.Timedelta(hours=EXIT_H)
        p_in, p_out = price_at(s, t_in), price_at(s, t_out)
        if not (np.isfinite(p_in) and np.isfinite(p_out)) or p_in <= 0:
            continue
        # couverture réelle aux deux bornes
        ok = True
        for t in (t_in, t_out):
            idx = s.index.searchsorted(t, side="right") - 1
            if idx < 0 or t - s.index[idx] > pd.Timedelta(hours=2):
                ok = False
        if not ok:
            continue
        ret = p_out / p_in - 1
        b_in, b_out = price_at(btc_s, t_in), price_at(btc_s, t_out)
        ret_adj = ret - (b_out / b_in - 1) if np.isfinite(b_in) and np.isfinite(b_out) else np.nan
        rows.append({"symbol": p.stem, "year": t0.year, "ret": ret, "ret_adj": ret_adj})

    df = pd.DataFrame(rows)
    net = df["ret"] - COST_RT_BPS / 1e4
    print(f"J+22 → J+30 : n={len(df)} listings avec couverture complète")
    print(f"  brut   : mean {df['ret'].mean()*1e4:+.0f} bps, med {df['ret'].median()*1e4:+.0f}, "
          f"hit {(df['ret']>0).mean()*100:.1f}%")
    print(f"  net ×2 : mean {net.mean()*1e4:+.0f} bps, med {net.median()*1e4:+.0f}")
    print(f"  adjBTC : med {df['ret_adj'].median()*1e4:+.0f} bps")
    print("\nPar cohorte :")
    for y, g in df.groupby("year"):
        gn = g["ret"] - COST_RT_BPS / 1e4
        print(f"  {y} : n={len(g)}, med net {gn.median()*1e4:+.0f} bps, "
              f"hit {(g['ret']>0).mean()*100:.0f}%")
    verdict = "NÉGATIF → filtre 30 j justifié de bout en bout" \
        if net.median() < 0 else "POSITIF/NEUTRE → limiter le filtre à 21 j"
    print(f"\nVERDICT J+22→J+30 : médiane nette {net.median()*1e4:+.0f} bps — {verdict}")


if __name__ == "__main__":
    main()
