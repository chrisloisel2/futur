#!/usr/bin/env python3
"""
scripts/archive_binance_positioning.py
─────────────────────────────────────────────────────────────────────────────
Runner de l'archiveur positioning (voir src/institutional/data/
positioning_archiver.py). Conçu pour tourner sous timer systemd toutes les
6 h (deploy/systemd/futur-positioning.timer) — les endpoints fapi ne
gardent que 30 jours, un appel 5m×500 couvre 41,6 h.

Usage :
  python3 scripts/archive_binance_positioning.py                # univers 50
  python3 scripts/archive_binance_positioning.py --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.institutional.data.positioning_archiver import (  # noqa: E402
    DEFAULT_OUT_DIR, UNIVERSE_50, archive_symbol)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(UNIVERSE_50))
    ap.add_argument("--period", default="5m")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out)
    t0 = time.time()
    print(f"[{pd.Timestamp.utcnow().isoformat()}] archive positioning : "
          f"{len(syms)} symboles, period={args.period}, limit={args.limit}",
          flush=True)

    registry: dict = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(archive_symbol, s, out_dir, args.period,
                          args.limit): s for s in syms}
        for fut in as_completed(futs):
            sym = futs[fut]
            registry[sym] = fut.result()

    n_ok = sum(1 for st in registry.values()
               for ep in st.values() if ep["status"] == "ok")
    n_err = sum(1 for st in registry.values()
                for ep in st.values() if ep["status"].startswith("err"))
    (out_dir / "registry.json").write_text(json.dumps(
        {"generated_at": pd.Timestamp.utcnow().isoformat(),
         "period": args.period, "limit": args.limit,
         "endpoints_ok": n_ok, "endpoints_err": n_err,
         "symbols": registry}, indent=2))
    print(f"  ok={n_ok} err={n_err} en {time.time()-t0:.0f}s → {out_dir}",
          flush=True)

    # échec systemd seulement si TOUT a échoué (erreurs partielles :
    # rattrapées au prochain run, rétention API 30 j)
    return 1 if n_ok == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
