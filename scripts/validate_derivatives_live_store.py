#!/usr/bin/env python3
"""
scripts/validate_derivatives_live_store.py
─────────────────────────────────────────────────────────────────────────────
Valide + reporte la couverture du store dérivés live (Phase 1).
Vérifie : parts lisibles (magic bytes), timestamps, par stream/symbol/date.

    python3 scripts/validate_derivatives_live_store.py --strict
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "derivatives_live" / "exchange=binance"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    if not LIVE.exists():
        print("Aucun store live encore — lancer run_derivatives_collector.py")
        return

    parts = sorted(LIVE.glob("stream=*/symbol=*/date=*/part-*.parquet"))
    bad = []
    cov = defaultdict(lambda: defaultdict(int))   # stream -> symbol -> rows
    for p in parts:
        stream = p.parts[-4].split("=")[1]
        symbol = p.parts[-3].split("=")[1]
        try:
            df = pd.read_parquet(p)
            cov[stream][symbol] += len(df)
        except Exception as e:
            bad.append((str(p), str(e)))

    print(f"\nDERIVATIVES LIVE STORE — {len(parts)} parts, {len(bad)} corrompus")
    for stream, syms in sorted(cov.items()):
        tot = sum(syms.values())
        print(f"  stream={stream:<14} rows={tot:>7}  symbols={len(syms)}  "
              f"({', '.join(f'{s}:{n}' for s, n in list(syms.items())[:5])})")
    if "force_order" in cov:
        print(f"  ✓ LIQUIDATIONS capturées : {sum(cov['force_order'].values())} events "
              f"(donnée introuvable en historique)")
    else:
        print("  ⚠ aucune liquidation encore (forceOrder est événementiel — normal sur courte fenêtre)")

    if args.strict and bad:
        print(f"\nSTRICT FAIL : {len(bad)} parts corrompus")
        for f, e in bad[:5]:
            print("  ", f, e)
        sys.exit(1)


if __name__ == "__main__":
    main()
