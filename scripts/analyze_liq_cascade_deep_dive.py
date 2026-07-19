#!/usr/bin/env python3
"""
scripts/analyze_liq_cascade_deep_dive.py
─────────────────────────────────────────────────────────────────────────────
DEEP DIVE structurel du dataset LIQ_CASCADE — la physique de l'edge, SANS
modèle (diagnostic descriptif ; ne sert PAS à choisir des filtres de trading,
sinon c'est du tuning sur l'historique complet).

Tranches analysées (fwd_4h net de 14 bps) : type d'event, profondeur (oi_drop_z),
ampleur market-wide, funding à l'event, vol, heure UTC, tier de symbole, année.
Cache le dataset dans data/events/liq_cascade_dataset.parquet (réutilisable).
Sortie : reports/liq_cascade/DEEP_DIVE.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.engines.liq_cascade.dataset import build_event_dataset
from src.institutional.engines.liq_cascade.detector import METRICS_DIR, CascadeConfig

CACHE = ROOT / "data" / "events" / "liq_cascade_dataset.parquet"
OUT = ROOT / "reports" / "liq_cascade" / "DEEP_DIVE.md"
COST = 0.0014
MAJORS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}


def slice_table(ev: pd.DataFrame, col: str, bins, labels) -> pd.DataFrame:
    d = ev.copy()
    d["bucket"] = pd.cut(d[col], bins=bins, labels=labels)
    rows = []
    for b, g in d.groupby("bucket", observed=True):
        net = g["fwd_4h"].values - COST
        net = net[np.isfinite(net)]
        if len(net) < 30:
            continue
        pf = net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-12)
        rows.append({"bucket": str(b), "n": len(net), "pf": round(pf, 3),
                     "mean_bps": round(net.mean() * 1e4, 1),
                     "wr": round((net > 0).mean(), 3)})
    return pd.DataFrame(rows)


def main():
    if CACHE.exists():
        ev = pd.read_parquet(CACHE)
        print(f"cache: {len(ev)} events")
    else:
        symbols = sorted(p.stem.replace("_metrics_5m", "")
                         for p in METRICS_DIR.glob("*_metrics_5m.parquet"))
        ev = build_event_dataset(symbols, CascadeConfig())
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        ev.to_parquet(CACHE, index=False)
    ev = ev[ev.label_full].copy()
    ev["year"] = ev["event_time"].dt.year

    L = ["# LIQ_CASCADE — deep dive structurel (fwd_4h net 14 bps)\n",
         f"Events labellisés : {len(ev)} | {ev.symbol.nunique()} symboles | "
         f"{ev.event_time.min().date()} → {ev.event_time.max().date()}\n",
         "⚠ Descriptif SUR TOUT L'HISTORIQUE — ne pas en dériver de filtres "
         "d'exécution (tuning). Les décisions restent au walk-forward.\n"]

    def add(title, df):
        L.append(f"\n## {title}\n")
        L.append(df.to_markdown(index=False) if len(df) else "_(n<30 partout)_")

    net_all = ev["fwd_4h"] - COST
    L.append(f"\nGlobal : n={len(ev)}, mean={net_all.mean()*1e4:+.1f} bps, "
             f"PF={net_all[net_all>0].sum()/abs(net_all[net_all<0].sum()):.3f}\n")

    add("Par type", ev.groupby("kind").apply(
        lambda g: pd.Series({
            "n": len(g),
            "pf": round((g.fwd_4h-COST).clip(lower=0).sum() /
                        max(abs((g.fwd_4h-COST).clip(upper=0).sum()), 1e-12), 3),
            "mean_bps": round((g.fwd_4h-COST).mean()*1e4, 1)})).reset_index())

    add("Par profondeur de cascade (oi_drop_z)",
        slice_table(ev, "oi_drop_z", [-50, -8, -6, -4.5, -3],
                    ["z≤-8 (extrême)", "-8<z≤-6", "-6<z≤-4.5", "-4.5<z≤-3"]))
    add("Par ampleur market-wide (n events 30 min avant)",
        slice_table(ev, "n_events_mktwide_30m", [-1, 0, 2, 5, 1000],
                    ["isolé", "1-2", "3-5", ">5 (cascade globale)"]))
    add("Par funding à l'event (majors avec funding backfillé)",
        slice_table(ev.dropna(subset=["funding_last"]), "funding_last",
                    [-1, -0.0002, 0, 0.0002, 1],
                    ["très négatif", "négatif", "positif", "très positif"]))
    add("Par vol 24h", slice_table(ev, "vol_24h", [0, 0.02, 0.04, 0.08, 10],
                                   ["calme", "normal", "agité", "extrême"]))
    add("Par mouvement prix 30m", slice_table(
        ev, "px_ret_30m", [-1, -0.03, -0.015, -0.004, 0.004, 0.015, 1],
        ["<-3%", "-3..-1.5%", "-1.5..-0.4%", "±0.4% (squeeze size)",
         "+0.4..1.5%", ">+1.5%"]))
    ev["tier"] = np.where(ev.symbol.isin(MAJORS), "major", "alt")
    add("Par tier", ev.groupby("tier").apply(
        lambda g: pd.Series({
            "n": len(g),
            "pf": round((g.fwd_4h-COST).clip(lower=0).sum() /
                        max(abs((g.fwd_4h-COST).clip(upper=0).sum()), 1e-12), 3),
            "mean_bps": round((g.fwd_4h-COST).mean()*1e4, 1)})).reset_index())
    add("Par année", ev.groupby("year").apply(
        lambda g: pd.Series({
            "n": len(g),
            "pf": round((g.fwd_4h-COST).clip(lower=0).sum() /
                        max(abs((g.fwd_4h-COST).clip(upper=0).sum()), 1e-12), 3),
            "mean_bps": round((g.fwd_4h-COST).mean()*1e4, 1)})).reset_index())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
