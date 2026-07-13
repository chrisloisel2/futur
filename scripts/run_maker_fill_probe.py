#!/usr/bin/env python3
"""
scripts/run_maker_fill_probe.py — lance la sonde de fills maker (voir module).
Usage : run_maker_fill_probe.py [--symbols A,B,...]
Service : futur-maker-probe.service
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.institutional.execution.maker_fill_probe import MakerFillProbe, SYMBOLS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    args = ap.parse_args()
    syms = args.symbols.split(",") if args.symbols else SYMBOLS
    probe = MakerFillProbe(symbols=syms)
    print(f"[probe] démarrage — {len(syms)} symboles, ordres virtuels "
          f"post-only bilatéraux toutes les 30 s, TTL 600 s", flush=True)
    asyncio.run(probe.run())


if __name__ == "__main__":
    main()
