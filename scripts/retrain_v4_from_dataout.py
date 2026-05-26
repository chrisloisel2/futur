"""
scripts/retrain_v4_from_dataout.py — Relance pipeline v4 depuis /hedge_fund/data_out/
=======================================================================================

Pipeline :
  1. Charge les parquets annuels depuis /home/qbee/hedge_fund/data_out/result/
  2. Concatène et rééchantillonne 1m → 1h par symbole
  3. Conserve les features macro (funding_rate, OI, L/S ratios, spot divergence)
  4. Sauvegarde en bundle 1h dans data_hedge_fund/{SYMBOL}_dataout_1h.parquet
  5. Lance build_hedge_fund_features.py pour feature engineering complet
  6. Lance train_hedge_fund.py pour entraînement et backtest

Usage:
  python scripts/retrain_v4_from_dataout.py
  python scripts/retrain_v4_from_dataout.py --symbols BTCUSDT ETHUSDT
  python scripts/retrain_v4_from_dataout.py --symbols BTCUSDT --skip-prep
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = Path("/home/qbee/hedge_fund/data_out/result")
DATA_HF  = ROOT / "data_hedge_fund"
DATA_HF.mkdir(exist_ok=True)

# Symboles disponibles dans data_out (avec au moins 3 ans de data)
SYMBOLS_AVAILABLE = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "ADAUSDT", "LINKUSDT", "XRPUSDT", "DOGEUSDT",
]

# Colonnes macro à forward-fill (mise à jour < 1h, valeur constante sur 1h)
MACRO_FFILL = [
    "funding_rate", "funding_mark_price", "spot_close",
    "oi_sum", "oi_value_sum", "top_trader_lsr", "top_trader_lsr_sum",
    "global_long_short_ratio", "taker_buy_sell_ratio",
    "funding_z_7d", "funding_z_30d", "funding_accel", "funding_sign", "funding_extreme",
    "oi_chg_60m", "oi_chg_240m", "oi_chg_1440m",
    "oi_z_1d", "oi_price_div_1h",
    "oi_price_div_4h", "oi_price_div_24h",
    "lsr_z_1d", "lsr_z_7d", "lsr_accel",
    "funding_x_oi", "funding_x_lsr", "oi_x_lsr",
    "fear_greed_value",
]

# Colonnes de labels 4h (horizon cible de la pipeline)
LABEL_COLS_4H = [
    "label_ret_240m", "label_sign_240m", "label_sharpe_240m",
    "label_ret_60m", "label_sign_60m",
    "label_tb_trend", "label_tb_trend_time", "label_tb_trend_tradeable",
]


COLS_TO_KEEP = (
    ["open", "high", "low", "close", "volume"]
    + MACRO_FFILL
    + LABEL_COLS_4H
)


def resample_year_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Rééchantillonne un chunk annuel 1m → 1h, sélectionne les colonnes utiles."""
    cols = [c for c in COLS_TO_KEEP if c in df.columns]
    df = df[cols].copy()

    ohlcv_agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ohlcv_agg = {k: v for k, v in ohlcv_agg.items() if k in df.columns}
    ohlcv = df[[c for c in ohlcv_agg]].resample("1h").agg(ohlcv_agg)

    macro_available = [c for c in MACRO_FFILL if c in df.columns]
    if macro_available:
        macro = df[macro_available].resample("1h").last().ffill()
        result = pd.concat([ohlcv, macro], axis=1)
    else:
        result = ohlcv

    label_available = [c for c in LABEL_COLS_4H if c in df.columns]
    if label_available:
        labels = df[label_available].resample("1h").first()
        result = pd.concat([result, labels], axis=1)

    return result.dropna(subset=["open", "close"])


def load_symbol_yearly(symbol: str) -> pd.DataFrame:
    """Charge année par année et concatène les 1h rééchantillonnés (évite l'OOM)."""
    files = sorted(DATA_OUT.glob(f"*_{symbol}_features.parquet"))
    if not files:
        print(f"  ⚠ Aucun fichier trouvé pour {symbol}")
        return pd.DataFrame()

    chunks_1h = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp")
            chunk_1h = resample_year_chunk(df)
            chunks_1h.append(chunk_1h)
            print(f"    {f.stem[:4]}: {len(df):,} barres 1m → {len(chunk_1h)} barres 1h")
        except Exception as e:
            print(f"  ⚠ Erreur {f.name}: {e}")

    if not chunks_1h:
        return pd.DataFrame()

    combined = pd.concat(chunks_1h).sort_index().drop_duplicates()
    print(f"  {symbol}: {len(combined):,} barres 1h  [{combined.index[0].date()} → {combined.index[-1].date()}]")
    return combined


def prepare_bundles(symbols: list[str]) -> dict[str, Path]:
    """Prépare les bundles 1h depuis data_out pour chaque symbole."""
    print("\n" + "=" * 65)
    print("  ÉTAPE 1 : PRÉPARATION DES BUNDLES 1h DEPUIS data_out")
    print("=" * 65)

    bundles = {}
    for symbol in symbols:
        print(f"\n[{symbol}]")
        df_1m = load_symbol_yearly(symbol)
        if df_1m.empty:
            continue

        df_1h = resample_to_1h(df_1m)
        print(f"  → Rééchantillonné : {len(df_1h):,} barres 1h")
        print(f"     Features macro incluses : {sum(1 for c in MACRO_FFILL if c in df_1h.columns)}")
        print(f"     Labels 4h inclus        : {sum(1 for c in LABEL_COLS_4H if c in df_1h.columns)}")

        out_path = DATA_HF / f"{symbol}_dataout_1h.parquet"
        df_1h.to_parquet(out_path)
        bundles[symbol] = out_path
        print(f"  ✅ Sauvegardé : {out_path}")

    return bundles


def run_feature_engineering(symbols: list[str]) -> bool:
    """Lance build_hedge_fund_features.py pour les symboles préparés."""
    print("\n" + "=" * 65)
    print("  ÉTAPE 2 : FEATURE ENGINEERING (enrichi)")
    print("=" * 65)

    script = ROOT / "scripts" / "build_hedge_fund_features.py"
    if not script.exists():
        print(f"  ⚠ Script non trouvé : {script}")
        return False

    cmd = [sys.executable, str(script)] + ["--symbols"] + symbols + ["--source", "dataout"]
    print(f"  Commande : {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def run_training(symbols: list[str], version_suffix: str = "v8") -> bool:
    """Lance train_hedge_fund.py."""
    print("\n" + "=" * 65)
    print(f"  ÉTAPE 3 : ENTRAÎNEMENT & BACKTEST ({version_suffix})")
    print("=" * 65)

    script = ROOT / "scripts" / "train_hedge_fund.py"
    if not script.exists():
        print(f"  ⚠ Script non trouvé : {script}")
        return False

    cmd = [
        sys.executable, str(script),
        "--symbols", *symbols,
        "--version", version_suffix,
    ]
    print(f"  Commande : {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--symbols", nargs="+", default=SYMBOLS_AVAILABLE,
        help="Symboles à traiter (défaut : tous)",
    )
    parser.add_argument("--skip-prep", action="store_true", help="Skip la préparation des bundles")
    parser.add_argument("--skip-features", action="store_true", help="Skip le feature engineering")
    parser.add_argument("--skip-train", action="store_true", help="Skip l'entraînement")
    parser.add_argument("--version", default="v8", help="Suffixe de version pour les runs")
    args = parser.parse_args()

    symbols = [s.upper() if not s.endswith("USDT") else s for s in args.symbols]
    # Filtrer aux symboles disponibles
    symbols = [s for s in symbols if s in SYMBOLS_AVAILABLE or s + "USDT" in SYMBOLS_AVAILABLE]
    if not symbols:
        symbols = SYMBOLS_AVAILABLE

    print(f"\n{'='*65}")
    print(f"  RELANCE PIPELINE v4 — SOURCE: data_out")
    print(f"  Symboles: {', '.join(symbols)}")
    print(f"  Version:  hedge_fund_{args.version}")
    print(f"{'='*65}")

    t0 = time.time()

    if not args.skip_prep:
        bundles = prepare_bundles(symbols)
        if not bundles:
            print("❌ Aucun bundle préparé — vérifier data_out/result/")
            sys.exit(1)

    if not args.skip_features:
        ok = run_feature_engineering(symbols)
        if not ok:
            print("⚠ Feature engineering terminé avec erreurs (continuation...)")

    if not args.skip_train:
        ok = run_training(symbols, args.version)
        if not ok:
            print("⚠ Entraînement terminé avec erreurs")

    elapsed = time.time() - t0
    print(f"\n✅ Pipeline terminée en {elapsed/60:.1f} min")
    print(f"\nRésultats dans : runs/hedge_fund_{args.version}/")
    print(f"Analyse PropFirm : python scripts/propfirm_analysis.py --version {args.version}")


if __name__ == "__main__":
    main()
