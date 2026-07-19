#!/usr/bin/env python3
"""
scripts/build_derivatives_store.py
─────────────────────────────────────────────────────────────────────────────
Derivatives store (Phase 4) — depuis les données RÉELLES locales uniquement.

Honnêteté : pas de liquidations historiques disponibles (nulle part) ; OI/taker
quasi BTC-only. On construit donc ce qui EXISTE (BTC : OI réel 2021-2025 +
funding + taker partiel) et on documente les manques. La gate
DERIVATIVES_STORE_PASS échoue volontairement tant que la couverture multi-actifs
n'est pas acquise → pas de faux moteur Liquidation multi-actifs.

Sortie : data/derivatives/{ASSET}_1h.parquet (atomique) + registry hashé.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.data.atomic_parquet import atomic_write_parquet, validate_parquet_readable
from src.institutional.engines.legacy_bridge import load_enriched

OUT_DIR = ROOT / "data" / "derivatives"
REG = ROOT / "artifacts" / "data_registry" / "derivatives_store.yaml"
REQUIRED = ["oi_sum", "funding_rate", "taker_buy_sell_ratio"]


def _taker_btc() -> pd.Series:
    """taker_buy_sell_ratio BTC depuis data_out metrics (5min → 1h), 2020-2022 only."""
    frames = []
    for f in sorted(glob.glob(str(ROOT / "data_out" / "*" / "raw" / "binance_metrics.parquet"))):
        df = pd.read_parquet(f, columns=["timestamp", "taker_buy_sell_ratio"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        frames.append(df.set_index("timestamp")["taker_buy_sell_ratio"])
    if not frames:
        return pd.Series(dtype=float)
    return pd.concat(frames).sort_index().resample("1h").mean()


def build_btc() -> dict:
    enr = load_enriched("BTCUSDT", required_cols=["oi_sum", "funding_rate", "close"])
    if enr is None:
        return {"asset": "BTCUSDT", "status": "NO_ENRICHED"}
    df = enr.set_index("datetime")[["close", "oi_sum", "funding_rate"]].sort_index()
    df["oi_pct_change_1h"] = df["oi_sum"].pct_change()
    df["oi_zscore_24h"] = (df["oi_pct_change_1h"] - df["oi_pct_change_1h"].rolling(24).mean()) \
        / (df["oi_pct_change_1h"].rolling(24).std() + 1e-9)
    taker = _taker_btc()
    df["taker_buy_sell_ratio"] = taker.reindex(df.index)
    df = df.reset_index()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "BTCUSDT_1h.parquet"
    atomic_write_parquet(df, out)
    validate_parquet_readable(out)

    oi_ok = df["oi_sum"].notna()
    taker_ok = df["taker_buy_sell_ratio"].notna()
    return {
        "asset": "BTCUSDT", "path": str(out.relative_to(ROOT)),
        "rows": int(len(df)),
        "oi_coverage": [str(df.loc[oi_ok, "datetime"].min()), str(df.loc[oi_ok, "datetime"].max())] if oi_ok.any() else None,
        "taker_coverage": [str(df.loc[taker_ok, "datetime"].min()), str(df.loc[taker_ok, "datetime"].max())] if taker_ok.any() else None,
        "has_liquidations": False,
        "status": "PARTIAL_BTC_ONLY",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default="BTCUSDT")
    args = ap.parse_args()

    registry = {}
    for a in [x.strip() for x in args.assets.split(",")]:
        if a == "BTCUSDT":
            registry[a] = build_btc()
        else:
            # alts : pas d'OI/taker historiques → non constructible honnêtement
            registry[a] = {"asset": a, "status": "NO_DERIVATIVES_DATA",
                           "reason": "pas d'OI/taker/liquidations historiques pour cet actif"}

    REG.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    REG.write_text(yaml.safe_dump(registry, sort_keys=True, allow_unicode=True))

    # GATE honnête : PASS seulement si multi-actifs OI+taker+liquidations couverts
    partial = [a for a, v in registry.items() if v.get("status") != "FULL"]
    print(f"\nDERIVATIVES STORE → {REG.relative_to(ROOT)}")
    for a, v in registry.items():
        print(f"  {a}: {v.get('status')}  rows={v.get('rows','-')}  liq={v.get('has_liquidations', False)}")
    print(f"\nDERIVATIVES_STORE_GATE : FAIL")
    print("  raisons : liquidations historiques absentes ; OI/taker BTC-only ; pas de 2026 OI.")
    print("  → moteur Liquidation Event-First crédible = BTC-only OI-deleveraging proxy, 2022-2025.")
    print("  → 40-80K/an exige une ACQUISITION de données dérivées multi-actifs (OI+liquidations live).")


if __name__ == "__main__":
    main()
