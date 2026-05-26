#!/usr/bin/env python3
"""Build canonical 1h/1m training parquet datasets from public raw data."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipeline.features import compute_training_features
from data_pipeline.joins import parse_timedelta, point_in_time_join
from data_pipeline.normalization import normalize_symbol, standardize_ohlcv_columns
from data_pipeline.sources import load_source_registry
from data_pipeline.storage import read_partitioned_parquet


def discover_symbols(args_symbols: Sequence[str]) -> List[str]:
    all_tokens = {"all", "all-usdt"}
    if args_symbols and not (len(args_symbols) == 1 and args_symbols[0] in all_tokens):
        return [normalize_symbol(sym) for sym in args_symbols]
    raw_symbols = set()
    for path in (ROOT / "data").glob("*_1h_features.csv"):
        stem = path.name.split("_1h_features.csv")[0]
        raw_symbols.add("BTCUSDT" if stem == "BTCUSD" else normalize_symbol(stem))
    return sorted(raw_symbols or {"BTCUSDT", "ETHUSDT", "SOLUSDT"})


def load_price_frame(raw_root: Path, symbol: str, interval: str) -> pd.DataFrame:
    sources = [
        ("binance_vision_spot_klines", "spot"),
        ("binance_vision_um_futures_klines", "futures_um"),
        ("binance_vision", "spot"),        # compatibility with earlier local raw dumps
        ("binance_vision", "futures_um"),
    ]
    for source, market in sources:
        frame = read_partitioned_parquet(raw_root, source=source, market_type=market, symbol=symbol, interval=interval)
        if not frame.empty:
            return frame

    # Compatibility fallback for the existing repo data files.
    if interval == "1h":
        candidates = [
            ROOT / "data" / ("%s_1h_features.csv" % symbol),
            ROOT / "data" / ("BTCUSD_1h_features.csv" if symbol == "BTCUSDT" else ""),
        ]
        for path in candidates:
            if path.exists():
                return pd.read_csv(path, low_memory=False)
    if interval == "1m":
        parts = sorted((ROOT / "data" / "ohlcv_1m").glob("%s_*.parquet" % symbol))
        if parts:
            return pd.concat([pd.read_parquet(path) for path in parts], ignore_index=True)
    return pd.DataFrame()


def load_context_frames(raw_root: Path) -> Tuple[List[Tuple[str, pd.DataFrame, Optional[pd.Timedelta]]], Dict[str, int]]:
    registry = load_source_registry()
    contexts: List[Tuple[str, pd.DataFrame, Optional[pd.Timedelta]]] = []
    coverage: Dict[str, int] = {}
    skip = {"binance_vision_spot_klines", "binance_vision_um_futures_klines"}
    for name, spec in registry.items():
        if name in skip:
            continue
        frame = read_partitioned_parquet(raw_root, source=name)
        if frame.empty:
            continue
        if name == "gdelt_crypto_articles":
            frame = _gdelt_news_context_frame(frame)
        else:
            frame = _numeric_context_frame(frame)
        if frame.empty:
            continue
        contexts.append((name, frame, parse_timedelta(spec.ffill_limit)))
        coverage[name] = int(len(frame))
    return contexts, coverage


def _numeric_context_frame(frame: pd.DataFrame) -> pd.DataFrame:
    keep = ["timestamp", "symbol"]
    numeric = []
    for col in frame.columns:
        if col in keep:
            continue
        converted = pd.to_numeric(frame[col], errors="coerce")
        if converted.notna().any():
            frame[col] = converted
            numeric.append(col)
    cols = keep + numeric
    return frame[[col for col in cols if col in frame.columns]].dropna(subset=["timestamp"])


def _gdelt_news_context_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce").dt.floor("h")
    if "symbol" not in out.columns:
        out["symbol"] = "GLOBAL"
    out = out.dropna(subset=["timestamp"])
    if out.empty:
        return out
    grouped = (
        out.groupby(["timestamp", "symbol"], as_index=False)
        .size()
        .rename(columns={"size": "news_count"})
    )
    return grouped


def synthesize_public_context_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Create model-facing macro feature names from joined public context columns."""
    out = frame.copy()

    def zscore_from_candidates(target: str, candidates: Sequence[str], window: int, default: float = 0.0) -> None:
        if target in out.columns:
            return
        source = next((col for col in candidates if col in out.columns), None)
        if source is None:
            out[target] = default
            return
        series = pd.to_numeric(out[source], errors="coerce").ffill()
        mean = series.rolling(window, min_periods=max(3, window // 4)).mean()
        std = series.rolling(window, min_periods=max(3, window // 4)).std().replace(0, np.nan)
        out[target] = ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(default)

    zscore_from_candidates("funding_rate_z_24", ["binance_futures_funding_funding_rate", "bybit_funding_funding_rate"], 24)
    zscore_from_candidates("funding_rate_z_72", ["binance_futures_funding_funding_rate", "bybit_funding_funding_rate"], 72)
    zscore_from_candidates("oihist_sumOpenInterest_z_24", ["binance_futures_positioning_oi"], 24)
    zscore_from_candidates("oihist_sumOpenInterest_z_72", ["binance_futures_positioning_oi"], 72)
    zscore_from_candidates("fear_greed_value_z_24", ["alternative_me_fear_greed_fng_value"], 24)
    zscore_from_candidates("fear_greed_value_z_72", ["alternative_me_fear_greed_fng_value"], 72)
    zscore_from_candidates("global_ls_longShortRatio_z_24", ["binance_futures_positioning_ls_ratio_global", "okx_top_long_short_ls_ratio_top_okx"], 24)
    zscore_from_candidates("global_ls_longShortRatio_z_72", ["binance_futures_positioning_ls_ratio_global", "okx_top_long_short_ls_ratio_top_okx"], 72)
    zscore_from_candidates("taker_ls_buySellRatio_z_24", ["binance_futures_positioning_taker_buy_sell_ratio"], 24)
    zscore_from_candidates("global_market_cap_usd_z_24", ["coingecko_global_total_market_cap_usd", "coingecko_global_market_cap_usd"], 24)
    zscore_from_candidates("global_market_cap_usd_z_72", ["coingecko_global_total_market_cap_usd", "coingecko_global_market_cap_usd"], 72)
    zscore_from_candidates("btc_dominance_z_24", ["coingecko_global_btc_dominance"], 24)
    zscore_from_candidates("btc_mempool_fee_fastest_z_24", ["mempool_space_btc_mempool_fee_fastest"], 24)
    zscore_from_candidates("btc_mempool_tx_count_z_24", ["mempool_space_btc_mempool_tx_count"], 24)

    if "taker_ls_imbalance" not in out.columns:
        if "delta_taker_pressure" in out.columns:
            out["taker_ls_imbalance"] = pd.to_numeric(out["delta_taker_pressure"], errors="coerce").fillna(0.0)
        elif "taker_buy_ratio_base" in out.columns:
            out["taker_ls_imbalance"] = pd.to_numeric(out["taker_buy_ratio_base"], errors="coerce").fillna(0.5) - 0.5
        else:
            out["taker_ls_imbalance"] = 0.0

    out["funding_x_global_ls"] = pd.to_numeric(out["funding_rate_z_24"], errors="coerce").fillna(0.0) * pd.to_numeric(out["global_ls_longShortRatio_z_24"], errors="coerce").fillna(0.0)
    out["oi_x_fng"] = pd.to_numeric(out["oihist_sumOpenInterest_z_24"], errors="coerce").fillna(0.0) * pd.to_numeric(out["fear_greed_value_z_24"], errors="coerce").fillna(0.0)

    news_source = "gdelt_crypto_articles_news_count"
    if news_source in out.columns:
        news = pd.to_numeric(out[news_source], errors="coerce").fillna(0.0)
        out["news_count_roll_24"] = news.rolling(24, min_periods=1).sum()
        out["news_count_roll_72"] = news.rolling(72, min_periods=1).sum()
        zscore_from_candidates("news_count_z_24", ["news_count_roll_24"], 24)
        zscore_from_candidates("news_count_z_72", ["news_count_roll_72"], 72)
    else:
        out["news_count_roll_24"] = 0.0
        out["news_count_roll_72"] = 0.0
        out["news_count_z_24"] = 0.0
        out["news_count_z_72"] = 0.0
    if "news_count_roll_240" not in out.columns:
        out["news_count_roll_240"] = out["news_count_roll_24"]
    if "news_count_roll_1440" not in out.columns:
        out["news_count_roll_1440"] = out["news_count_roll_72"]
    return out


def build_symbol_dataset(symbol: str, interval: str, raw_root: Path, output_root: Path, write_csv: bool) -> Optional[Path]:
    price = load_price_frame(raw_root, symbol, interval)
    if price.empty:
        print("no price data for", symbol, interval)
        return None

    contexts, coverage = load_context_frames(raw_root)
    price = standardize_ohlcv_columns(price).reset_index().rename(columns={"index": "timestamp"})
    if contexts:
        price = point_in_time_join(price, contexts, by=None)
    price = synthesize_public_context_features(price)

    features = compute_training_features(price, symbol=symbol, interval=interval, include_labels=True, source_coverage=coverage)
    out_dir = output_root / interval
    out_dir.mkdir(parents=True, exist_ok=True)

    if interval == "1m":
        paths = []
        frame = features.reset_index()
        frame["year"] = pd.to_datetime(frame["timestamp"], utc=True).dt.year
        for year, group in frame.groupby("year"):
            out_path = out_dir / ("%s_%s.parquet" % (symbol, year))
            group.drop(columns=["year"]).to_parquet(out_path, index=False)
            paths.append(out_path)
        print("wrote", [str(path) for path in paths])
        return paths[0] if paths else None

    out_path = out_dir / ("%s.parquet" % symbol)
    features.reset_index().to_parquet(out_path, index=False)
    if write_csv:
        features.to_csv(out_dir / ("%s.csv" % symbol), index_label="timestamp")
    print("wrote", out_path, features.shape)
    return out_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build max-public training datasets")
    parser.add_argument("--profile", default="max-public")
    parser.add_argument("--granularity", choices=["1h", "1m"], default="1h")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    parser.add_argument("--raw-root", default=str(ROOT / "data" / "raw"))
    parser.add_argument("--output-root", default=str(ROOT / "data" / "training"))
    parser.add_argument("--csv", action="store_true", help="Also write CSV compatibility files")
    args = parser.parse_args(argv)

    if args.profile != "max-public":
        raise SystemExit("Only --profile max-public is supported")

    symbols = discover_symbols(args.symbols)
    written = []
    for symbol in symbols:
        path = build_symbol_dataset(symbol, args.granularity, Path(args.raw_root), Path(args.output_root), args.csv)
        if path is not None:
            written.append(str(path))
    print(json.dumps({"written": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
