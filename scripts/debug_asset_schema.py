#!/usr/bin/env python3
"""
scripts/debug_asset_schema.py
─────────────────────────────────────────────────────────────────────────────
Outil de diagnostic des sources de données par actif.

Affiche pour chaque actif :
  - fichiers trouvés
  - colonnes brutes
  - mapping OHLCV détecté
  - colonnes manquantes
  - nombre de lignes
  - période couverte
  - source sélectionnée (enriched / data_out / absent)

Usage :
    python3 scripts/debug_asset_schema.py --assets ETHUSDT,SOLUSDT,BNBUSDT

    # Toutes les sources
    python3 scripts/debug_asset_schema.py --assets BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,AVAXUSDT,LINKUSDT

    # Filtrer une source spécifique
    python3 scripts/debug_asset_schema.py --assets ETHUSDT --sources enriched,data_out
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

DATA_ROOT    = Path("data_out")
ENRICHED_ROOT = Path("data/enriched")

# Fichiers data_out connus pour les alts (séries de clôture uniquement, PAS OHLCV)
CLOSE_ONLY_FILES = {
    "ETHUSDT": "binance_eth.parquet",
    "BNBUSDT": "binance_bnb.parquet",
    "SOLUSDT": "binance_sol.parquet",
    "BTCUSDT": "binance_spot.parquet",   # spot close uniquement
}
OHLCV_FILES = {
    "BTCUSDT": "binance_futures_klines.parquet",
}


def check_enriched(asset: str) -> dict:
    path = ENRICHED_ROOT / f"{asset}_1h_enriched.parquet"
    if not path.exists():
        return {"found": False, "path": str(path)}

    df = pd.read_parquet(path)
    ohlcv_present = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    ts_col = "datetime" if "datetime" in df.columns else (
        df.index.name if isinstance(df.index, pd.DatetimeIndex) else "absent"
    )

    # Période couverte
    if "datetime" in df.columns:
        t_min = pd.to_datetime(df["datetime"]).min()
        t_max = pd.to_datetime(df["datetime"]).max()
    elif isinstance(df.index, pd.DatetimeIndex):
        t_min, t_max = df.index.min(), df.index.max()
    else:
        t_min = t_max = None

    return {
        "found":        True,
        "path":         str(path),
        "rows":         len(df),
        "columns":      list(df.columns[:8]),
        "ohlcv_ok":     len(ohlcv_present) == 5,
        "ohlcv_found":  ohlcv_present,
        "missing_ohlcv": [c for c in ["open","high","low","close","volume"] if c not in df.columns],
        "timestamp_col": ts_col,
        "period":       f"{t_min} → {t_max}" if t_min else "unknown",
        "size_mb":      round(path.stat().st_size / 1e6, 1),
    }


def check_data_out(asset: str) -> dict:
    results = {}

    # OHLCV file (si disponible)
    ohlcv_file = OHLCV_FILES.get(asset)
    if ohlcv_file:
        frames = []
        for year in range(2019, 2027):
            p = DATA_ROOT / str(year) / "raw" / ohlcv_file
            if p.exists():
                try:
                    df = pd.read_parquet(p)
                    frames.append({"year": year, "rows": len(df), "cols": list(df.columns[:6])})
                except Exception as e:
                    frames.append({"year": year, "error": str(e)})
        results["ohlcv_file"] = {"name": ohlcv_file, "years": frames}

    # Close-only file (attention : PAS OHLCV)
    close_file = CLOSE_ONLY_FILES.get(asset)
    if close_file:
        frames = []
        for year in range(2019, 2027):
            p = DATA_ROOT / str(year) / "raw" / close_file
            if p.exists():
                try:
                    df = pd.read_parquet(p)
                    frames.append({"year": year, "rows": len(df), "cols": list(df.columns)})
                except Exception as e:
                    frames.append({"year": year, "error": str(e)})
        results["close_only_file"] = {
            "name": close_file,
            "WARNING": "Séries de clôture uniquement — PAS OHLCV, inutilisable pour features",
            "years": frames,
        }

    return results


def print_report(asset: str, sources: list) -> bool:
    """Affiche le diagnostic pour un actif. Retourne True si la source primaire est OK."""
    print(f"\n{'═'*65}")
    print(f" {asset}")
    print(f"{'═'*65}")

    ok = False

    if "enriched" in sources:
        info = check_enriched(asset)
        if info["found"]:
            status = "✓ UTILISABLE" if info["ohlcv_ok"] else "⚠ OHLCV INCOMPLET"
            print(f"\n  [ENRICHED] {status}")
            print(f"    Path   : {info['path']}")
            print(f"    Rows   : {info['rows']:,}")
            print(f"    Size   : {info['size_mb']} MB")
            print(f"    Period : {info['period']}")
            print(f"    Cols   : {info['columns']}")
            print(f"    OHLCV  : {info['ohlcv_found']}")
            if info["missing_ohlcv"]:
                print(f"    MISSING: {info['missing_ohlcv']}")
            if info["ohlcv_ok"]:
                ok = True
        else:
            print(f"\n  [ENRICHED] ✗ ABSENT")
            print(f"    Expected: {info['path']}")

    if "data_out" in sources:
        info = check_data_out(asset)
        if info:
            print(f"\n  [DATA_OUT]")
            if "ohlcv_file" in info:
                ohlcv_info = info["ohlcv_file"]
                years_ok = [y for y in ohlcv_info["years"] if "error" not in y]
                print(f"    OHLCV file : {ohlcv_info['name']} ({len(years_ok)} années)")
                if years_ok:
                    y = years_ok[-1]
                    print(f"    Sample     : year={y['year']} rows={y['rows']:,} cols={y['cols']}")
            if "close_only_file" in info:
                co_info = info["close_only_file"]
                years_ok = [y for y in co_info["years"] if "error" not in y]
                print(f"    CLOSE-ONLY : {co_info['name']} ({len(years_ok)} années)")
                print(f"    ⚠  {co_info['WARNING']}")
                if years_ok:
                    y = years_ok[-1]
                    print(f"    Sample     : year={y['year']} rows={y['rows']:,} cols={y['cols']}")

    if not ok:
        print(f"\n  ✗ AUCUNE SOURCE OHLCV COMPLÈTE DISPONIBLE")
        print(f"    → Placer {asset}_1h_enriched.parquet dans data/enriched/")

    return ok


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Debug asset data sources")
    p.add_argument("--assets", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT",
                   help="Comma-separated assets (ex: BTCUSDT,ETHUSDT)")
    p.add_argument("--sources", default="enriched,data_out",
                   help="Sources à inspecter: enriched,data_out")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    assets  = [a.strip().upper() for a in args.assets.split(",")]
    sources = [s.strip() for s in args.sources.split(",")]

    print(f"{'─'*65}")
    print(f" DEBUG ASSET SCHEMA")
    print(f" Assets  : {assets}")
    print(f" Sources : {sources}")
    print(f"{'─'*65}")

    all_ok = True
    for asset in assets:
        ok = print_report(asset, sources)
        if not ok:
            all_ok = False

    print(f"\n{'═'*65}")
    if all_ok:
        print(f" TOUS LES ASSETS DISPONIBLES ✓")
    else:
        failed = []
        for asset in assets:
            info = check_enriched(asset)
            if not info["found"] or not info.get("ohlcv_ok"):
                failed.append(asset)
        print(f" ASSETS SANS SOURCE OHLCV : {failed}")
        print(f" ACTION : placer les fichiers *_1h_enriched.parquet dans data/enriched/")
        sys.exit(1)


if __name__ == "__main__":
    main()
