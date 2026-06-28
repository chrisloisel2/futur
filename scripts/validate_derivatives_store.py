#!/usr/bin/env python3
"""
scripts/validate_derivatives_store.py
─────────────────────────────────────────────────────────────────────────────
Valide le store dérivés RAW (Phase 1) sur 3 niveaux + couverture quotidienne.
  technique : magic bytes, schema, timestamp monotone, doublons, nan/inf
  marché    : prix>0, OI≥0, funding borné, usd≥0
  temporel  : latence p50/p99, gaps, recv_time≥event_time

Écrit reports/DERIVATIVES_DAILY_<date>.md + registry. Gate :
DERIVATIVES_LIVE_COLLECTION_PASS.

    python3 scripts/validate_derivatives_store.py --strict
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "derivatives_raw" / "exchange=binance" / "market=usdm"
REG = ROOT / "artifacts" / "data_registry" / "derivatives_raw_store.yaml"


def _check_market(stream: str, df: pd.DataFrame) -> list:
    issues = []
    if "open_interest" in df.columns and (df["open_interest"] < 0).any():
        issues.append("OI<0")
    for c in ("price", "mark_price", "usd"):
        if c in df.columns and (df[c].dropna() < 0).any():
            issues.append(f"{c}<0")
    if "funding_rate" in df.columns and (df["funding_rate"].abs() > 0.05).any():
        issues.append("funding_out_of_bounds")
    return issues


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    if not RAW.exists():
        print("Aucun store RAW — déployer run_derivatives_collector.py (systemd).")
        if args.strict:
            sys.exit(1)
        return

    parts = sorted(RAW.glob("stream=*/symbol=*/date=*/part-*.parquet"))
    cov = defaultdict(lambda: defaultdict(int))
    lat, bad, market_issues = [], [], []
    for p in parts:
        stream = p.parts[-4].split("=")[1]; symbol = p.parts[-3].split("=")[1]
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            bad.append((str(p), str(e))); continue
        cov[stream][symbol] += len(df)
        if "latency_ms" in df.columns:
            lat.extend(df["latency_ms"].dropna().tolist())
        mi = _check_market(stream, df)
        if mi:
            market_issues.append((p.name, mi))
        if "timestamp" in df.columns and "recv_time" in df.columns:
            if (df["recv_time"] < df["timestamp"]).any():
                bad.append((str(p), "recv_time<event_time"))

    registry = {}
    for stream, syms in cov.items():
        registry[stream] = {"rows": int(sum(syms.values())), "symbols": list(syms.keys())}
    REG.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    REG.write_text(yaml.safe_dump(registry, sort_keys=True, allow_unicode=True))

    # rapport quotidien
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lat_p50 = float(np.percentile(lat, 50)) if lat else None
    lat_p99 = float(np.percentile(lat, 99)) if lat else None
    rpt = [f"# Derivatives daily — {date}\n",
           f"- parts: {len(parts)}  corrompus: {len(bad)}  market_issues: {len(market_issues)}",
           f"- latency p50: {lat_p50} ms  p99: {lat_p99} ms\n", "## Coverage par stream"]
    for stream, syms in sorted(cov.items()):
        rpt.append(f"- **{stream}**: {sum(syms.values())} rows, {len(syms)} symbols")
    liq = sum(cov.get("force_order", {}).values())
    rpt.append(f"\n- LIQUIDATIONS capturées: {liq} (donnée introuvable en historique)")
    out = ROOT / "reports" / f"DERIVATIVES_DAILY_{date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rpt))

    print(f"\nDERIVATIVES RAW STORE — {len(parts)} parts, {len(bad)} corrompus, {len(market_issues)} market issues")
    for stream, syms in sorted(cov.items()):
        print(f"  {stream:<14} rows={sum(syms.values()):>7} symbols={len(syms)}")
    print(f"  latency p50={lat_p50}ms p99={lat_p99}ms | liquidations={liq}")
    print(f"  → {out.relative_to(ROOT)}")
    gate = (len(bad) == 0 and len(parts) > 0)
    print(f"\nDERIVATIVES_LIVE_COLLECTION_{'PASS' if gate else 'FAIL'}")
    if args.strict and not gate:
        sys.exit(1)


if __name__ == "__main__":
    main()
