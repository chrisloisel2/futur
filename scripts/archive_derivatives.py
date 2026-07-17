#!/usr/bin/env python3
"""
scripts/archive_derivatives.py — Archivage positioning dérivés Binance
======================================================================

Archive en continu les données futures/data de Binance (~30 j de rétention) :
ratios top-trader (comptes + positions), ratio global, taker buy/sell,
open interest historique — plus un snapshot quotidien d'univers perpétuels.

Incrémental : chaque exécution ne récupère que ce qui manque depuis le
dernier timestamp archivé (fenêtre max 30 j). Relançable sans doublons.

Usage:
  python scripts/archive_derivatives.py                    # top 40 + univers top 80
  python scripts/archive_derivatives.py --top 20
  python scripts/archive_derivatives.py --symbols BTCUSDT ETHUSDT SOLUSDT
  python scripts/archive_derivatives.py --period 5m --root data/raw
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipeline.derivatives_positioning import archive_positioning  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=40, help="Nb de symboles par volume 24h (défaut 40)")
    parser.add_argument("--symbols", nargs="*", default=[], help="Symboles additionnels à archiver")
    parser.add_argument("--period", default="5m", help="Granularité futures/data (défaut 5m)")
    parser.add_argument("--root", default=str(ROOT / "data" / "raw"), help="Racine du lake parquet")
    parser.add_argument("--universe-top", type=int, default=80, help="Taille du snapshot d'univers (défaut 80)")
    args = parser.parse_args()

    started = time.time()
    print(f"=== archive_derivatives === {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"root={args.root} top={args.top} period={args.period}")

    written = archive_positioning(
        Path(args.root),
        top_n=args.top,
        extra_symbols=args.symbols,
        period=args.period,
        universe_top_n=args.universe_top,
    )

    ok = {k: v for k, v in written.items() if v >= 0}
    failed = [k for k, v in written.items() if v < 0]
    print(f"\nTerminé en {time.time() - started:.0f}s : {len(ok)} symboles, "
          f"{sum(ok.values())} lignes écrites, {len(failed)} échecs"
          + (f" ({', '.join(failed)})" if failed else ""))
    return 1 if len(failed) > len(ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
