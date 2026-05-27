#!/usr/bin/env python3
"""
Backfill public/free data sources into partitioned parquet.

Examples:
  python scripts/backfill_public_data.py --profile max-public --granularity 1h 1m --symbols all-usdt
  python scripts/backfill_public_data.py --symbols BTCUSDT ETHUSDT --sources binance_vision_spot_klines binance_futures_funding
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipeline.http import CheckpointStore, PublicHTTPClient
from data_pipeline.normalization import binance_klines_to_frame, normalize_symbol, read_binance_vision_zip
from data_pipeline.sources import SourceSpec, load_source_registry
from data_pipeline.storage import write_mongo_snapshots, write_partitioned_parquet


BINANCE_SPOT_API = "https://api.binance.com/api/v3"
BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"
BINANCE_FDATA = "https://fapi.binance.com/futures/data"
BINANCE_VISION_S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BINANCE_VISION_CDN = "https://data.binance.vision"


def discover_symbols(symbol_args: Sequence[str]) -> List[str]:
    if symbol_args and not (len(symbol_args) == 1 and symbol_args[0] == "all-usdt"):
        return [normalize_symbol(sym) for sym in symbol_args]
    symbols = []
    for path in sorted((ROOT / "data").glob("*_1h_features.csv")):
        raw = path.name.split("_1h_features.csv")[0]
        symbols.append("BTCUSDT" if raw == "BTCUSD" else normalize_symbol(raw))
    if not symbols:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]
    return sorted(set(symbols))


def month_range(start: str, end: Optional[str]) -> List[Tuple[int, int]]:
    start_ts = pd.Timestamp(start, tz="UTC").replace(day=1)
    end_ts = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")
    end_ts = end_ts.replace(day=1)
    months = []
    cur = start_ts
    while cur <= end_ts:
        months.append((int(cur.year), int(cur.month)))
        cur = cur + pd.DateOffset(months=1)
    return months


def _ts(value: Optional[str]) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC") if value else pd.Timestamp.now(tz="UTC")


def _binance_market_path(spec: SourceSpec) -> str:
    return "futures/um" if "futures" in spec.market_type else "spot"


def _s3_list_url(prefix: str, continuation_token: Optional[str] = None) -> str:
    params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
    if continuation_token:
        params["continuation-token"] = continuation_token
    return BINANCE_VISION_S3 + "?" + urlencode(params)


def _parse_s3_list(payload: bytes) -> Tuple[List[str], Optional[str]]:
    root = ET.fromstring(payload)
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    keys = [
        node.text or ""
        for node in root.findall(".//s3:Contents/s3:Key", ns)
        if node.text
    ]
    token_node = root.find("s3:NextContinuationToken", ns)
    token = token_node.text if token_node is not None else None
    return keys, token


def list_s3_keys(client: PublicHTTPClient, prefix: str) -> List[str]:
    keys: List[str] = []
    token: Optional[str] = None
    while True:
        payload = client.get_bytes(_s3_list_url(prefix, token))
        page_keys, token = _parse_s3_list(payload)
        keys.extend(page_keys)
        if not token:
            break
    return sorted(set(keys))


def _monthly_prefix(spec: SourceSpec, symbol: str, interval: str) -> str:
    return "data/%s/monthly/klines/%s/%s/" % (_binance_market_path(spec), symbol, interval)


def _daily_prefix(spec: SourceSpec, symbol: str, interval: str, year: int, month: int) -> str:
    return "data/%s/daily/klines/%s/%s/%s-%s-%04d-%02d" % (
        _binance_market_path(spec),
        symbol,
        interval,
        symbol,
        interval,
        year,
        month,
    )


def _key_month(key: str) -> Optional[Tuple[int, int]]:
    match = re.search(r"-(\d{4})-(\d{2})(?:-\d{2})?\.zip$", key)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _key_day(key: str) -> Optional[pd.Timestamp]:
    match = re.search(r"-(\d{4})-(\d{2})-(\d{2})\.zip$", key)
    if not match:
        return None
    return pd.Timestamp(
        year=int(match.group(1)),
        month=int(match.group(2)),
        day=int(match.group(3)),
        tz="UTC",
    )


def _month_allowed(key: str, wanted: set) -> bool:
    key_month = _key_month(key)
    return key_month in wanted if key_month else False


def _day_allowed(key: str, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    day = _key_day(key)
    return bool(day is not None and start.normalize() <= day <= end.normalize())


def _zip_keys(keys: Iterable[str]) -> List[str]:
    return sorted(key for key in keys if key.endswith(".zip") and not key.endswith(".CHECKSUM"))


def _tail_months(end: Optional[str], months: int) -> List[Tuple[int, int]]:
    if months <= 0:
        return []
    end_ts = _ts(end).replace(day=1)
    start_ts = end_ts - pd.DateOffset(months=months - 1)
    out = []
    cur = start_ts
    while cur <= end_ts:
        out.append((int(cur.year), int(cur.month)))
        cur = cur + pd.DateOffset(months=1)
    return out


def fetch_binance_vision(
    *,
    spec: SourceSpec,
    symbols: Sequence[str],
    intervals: Sequence[str],
    start: str,
    end: Optional[str],
    raw_root: Path,
    checkpoint: CheckpointStore,
    dry_run: bool,
    max_months: int = 0,
    daily_tail_months: int = 2,
) -> Dict[str, int]:
    market = "futures_um" if "futures" in spec.market_type else "spot"
    client = PublicHTTPClient(rate_limit_per_minute=spec.rate_limit_per_minute)
    counts: Dict[str, int] = {}
    wanted_months = set(month_range(start, end))
    start_ts = _ts(start)
    end_ts = _ts(end)

    for symbol in symbols:
        for interval in intervals:
            monthly_prefix = _monthly_prefix(spec, symbol, interval)
            try:
                monthly_keys = _zip_keys(list_s3_keys(client, monthly_prefix))
            except Exception as exc:
                print("  skip listing %s %s %s (%s)" % (spec.name, symbol, interval, exc))
                monthly_keys = []
            monthly_keys = [key for key in monthly_keys if _month_allowed(key, wanted_months)]
            if max_months > 0:
                monthly_keys = monthly_keys[:max_months]

            monthly_months = {m for m in (_key_month(key) for key in monthly_keys) if m is not None}
            if monthly_keys:
                print("listed %s %s %s monthly=%s first=%s last=%s" % (
                    spec.name, symbol, interval, len(monthly_keys), Path(monthly_keys[0]).name, Path(monthly_keys[-1]).name,
                ))
            else:
                print("listed %s %s %s monthly=0" % (spec.name, symbol, interval))
            for s3_key in monthly_keys:
                _fetch_binance_vision_key(
                    s3_key=s3_key,
                    spec=spec,
                    market=market,
                    symbol=symbol,
                    interval=interval,
                    raw_root=raw_root,
                    checkpoint=checkpoint,
                    dry_run=dry_run,
                    counts=counts,
                )

            daily_keys: List[str] = []
            for year, month in _tail_months(end, daily_tail_months):
                if (year, month) in monthly_months:
                    continue
                daily_prefix = _daily_prefix(spec, symbol, interval, year, month)
                try:
                    keys = _zip_keys(list_s3_keys(client, daily_prefix))
                except Exception as exc:
                    print("  skip daily listing %s %s %s %04d-%02d (%s)" % (spec.name, symbol, interval, year, month, exc))
                    continue
                daily_keys.extend(key for key in keys if _day_allowed(key, start_ts, end_ts))
            if daily_keys:
                print("listed %s %s %s daily_tail=%s first=%s last=%s" % (
                    spec.name, symbol, interval, len(daily_keys), Path(daily_keys[0]).name, Path(daily_keys[-1]).name,
                ))
            for s3_key in daily_keys:
                _fetch_binance_vision_key(
                    s3_key=s3_key,
                    spec=spec,
                    market=market,
                    symbol=symbol,
                    interval=interval,
                    raw_root=raw_root,
                    checkpoint=checkpoint,
                    dry_run=dry_run,
                    counts=counts,
                )
    return counts


def _fetch_binance_vision_key(
    *,
    s3_key: str,
    spec: SourceSpec,
    market: str,
    symbol: str,
    interval: str,
    raw_root: Path,
    checkpoint: CheckpointStore,
    dry_run: bool,
    counts: Dict[str, int],
) -> None:
    checkpoint_key = "%s:%s:%s:%s" % (spec.name, symbol, interval, Path(s3_key).name)
    if checkpoint.get(checkpoint_key) == "done":
        return
    url = BINANCE_VISION_CDN + "/" + s3_key
    checksum_url = url + ".CHECKSUM"
    print("fetch", url)
    if dry_run:
        return
    try:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / Path(url).name
            try:
                client = PublicHTTPClient(rate_limit_per_minute=spec.rate_limit_per_minute)
                client.download(url, zip_path, checksum_url=checksum_url)
            except Exception:
                client = PublicHTTPClient(rate_limit_per_minute=spec.rate_limit_per_minute)
                client.download(url, zip_path)
            frame = read_binance_vision_zip(
                zip_path,
                symbol=symbol,
                interval=interval,
                market_type=market,
                source=spec.name,
            )
        written = write_partitioned_parquet(
            frame,
            root=raw_root,
            source=spec.name,
            market_type=market,
            symbol=symbol,
            interval=interval,
        )
        counts[checkpoint_key] = int(len(frame))
        checkpoint.set(checkpoint_key, "done")
        print("  wrote %s rows -> %s partitions" % (len(frame), len(written)))
    except Exception as exc:
        print("  skip %s (%s)" % (checkpoint_key, exc))


def fetch_binance_rest_klines(symbol: str, interval: str, start: str, end: Optional[str]) -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=120)
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int((pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")).timestamp() * 1000)
    rows = []
    while start_ms < end_ms:
        data = client.get_json(
            BINANCE_SPOT_API + "/klines",
            params={"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
        )
        if not data:
            break
        rows.extend(data)
        last = int(data[-1][0])
        start_ms = last + 1
        if len(data) < 1000:
            break
    return binance_klines_to_frame(rows, symbol=symbol, interval=interval, market_type="spot", source="binance_spot_api")


def collect_binance_funding(symbol: str, start: str, end: Optional[str]) -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=120)
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int((pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")).timestamp() * 1000)
    rows = []
    while start_ms < end_ms:
        data = client.get_json(BINANCE_FAPI + "/fundingRate", params={"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000})
        if not data:
            break
        rows.extend(data)
        last = int(data[-1]["fundingTime"])
        start_ms = last + 1
        if len(data) < 1000:
            break
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["fundingTime"], unit="ms", utc=True)
    frame["funding_rate"] = pd.to_numeric(frame["fundingRate"], errors="coerce")
    frame["mark_price"] = pd.to_numeric(frame.get("markPrice", 0), errors="coerce")
    frame["symbol"] = symbol
    return frame[["timestamp", "symbol", "funding_rate", "mark_price"]]


def _collect_binance_fdata(symbol: str, endpoint: str, value_map: Dict[str, str], period: str = "1h") -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=120)
    data = client.get_json(BINANCE_FDATA + "/" + endpoint, params={"symbol": symbol, "period": period, "limit": 500})
    frame = pd.DataFrame(data or [])
    if frame.empty:
        return frame
    ts_col = next((col for col in frame.columns if "time" in col.lower()), None)
    if ts_col is None:
        return pd.DataFrame()
    frame["timestamp"] = pd.to_datetime(frame[ts_col].astype(float), unit="ms", utc=True)
    frame["symbol"] = symbol
    for src, dst in value_map.items():
        if src in frame.columns:
            frame[dst] = pd.to_numeric(frame[src], errors="coerce")
    keep = ["timestamp", "symbol"] + list(value_map.values())
    return frame[[col for col in keep if col in frame.columns]]


def collect_binance_positioning(symbol: str) -> pd.DataFrame:
    frames = [
        _collect_binance_fdata(symbol, "openInterestHist", {"sumOpenInterest": "oi", "sumOpenInterestValue": "oi_value"}),
        _collect_binance_fdata(symbol, "globalLongShortAccountRatio", {"longShortRatio": "ls_ratio_global"}),
        _collect_binance_fdata(symbol, "topLongShortPositionRatio", {"longShortRatio": "ls_ratio_top_binance"}),
        _collect_binance_fdata(symbol, "takerlongshortRatio", {"buySellRatio": "taker_buy_sell_ratio"}),
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    out = frames[0]
    for frame in frames[1:]:
        out = pd.merge_asof(
            out.sort_values("timestamp"),
            frame.sort_values("timestamp"),
            on="timestamp",
            by="symbol",
            direction="nearest",
            tolerance=pd.Timedelta("5min"),
        )
    return out


def collect_bybit_funding(symbol: str) -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=60)
    payload = client.get_json(
        "https://api.bybit.com/v5/market/funding/history",
        params={"category": "linear", "symbol": symbol, "limit": 200},
    )
    rows = (payload or {}).get("result", {}).get("list", [])
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["fundingRateTimestamp"].astype(float), unit="ms", utc=True)
    frame["funding_rate"] = pd.to_numeric(frame["fundingRate"], errors="coerce")
    frame["symbol"] = symbol
    return frame[["timestamp", "symbol", "funding_rate"]]


def collect_okx_top_long_short(symbol: str) -> pd.DataFrame:
    inst = symbol.replace("USDT", "-USDT-SWAP")
    client = PublicHTTPClient(rate_limit_per_minute=60)
    payload = client.get_json(
        "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader",
        params={"instId": inst, "period": "1H", "limit": "100"},
    )
    rows = (payload or {}).get("data", [])
    frame = pd.DataFrame(rows, columns=["timestamp_ms", "ls_ratio_top_okx"])
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp_ms"].astype(float), unit="ms", utc=True)
    frame["ls_ratio_top_okx"] = pd.to_numeric(frame["ls_ratio_top_okx"], errors="coerce")
    frame["symbol"] = symbol
    return frame[["timestamp", "symbol", "ls_ratio_top_okx"]]


def collect_deribit_options() -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=60)
    payload = client.get_json(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
        params={"currency": "BTC", "kind": "option"},
    )
    rows = (payload or {}).get("result", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["mark_iv"] = pd.to_numeric(df.get("mark_iv", np.nan), errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
    df["open_interest"] = pd.to_numeric(df.get("open_interest", 0), errors="coerce").fillna(0)
    df["underlying_price"] = pd.to_numeric(df.get("underlying_price", np.nan), errors="coerce")
    parts = df["instrument_name"].astype(str).str.split("-", expand=True)
    if parts.shape[1] >= 4:
        df["strike"] = pd.to_numeric(parts[2], errors="coerce")
        df["option_type"] = parts[3]
    else:
        return pd.DataFrame()
    spot = float(df["underlying_price"].dropna().mean()) if df["underlying_price"].notna().any() else np.nan
    calls = df[df["option_type"] == "C"]
    puts = df[df["option_type"] == "P"]
    call_vol = float(calls["volume"].sum())
    put_vol = float(puts["volume"].sum())
    call_oi = float(calls["open_interest"].sum())
    put_oi = float(puts["open_interest"].sum())
    atm_iv = float(df.assign(dist=(df["strike"] - spot).abs()).nsmallest(5, "dist")["mark_iv"].mean())
    skew = float((puts["mark_iv"] * puts["open_interest"]).sum() / (put_oi + 1e-9) - (calls["mark_iv"] * calls["open_interest"]).sum() / (call_oi + 1e-9))
    return pd.DataFrame([{
        "timestamp": pd.Timestamp.now(tz="UTC"),
        "symbol": "BTCUSDT",
        "atm_iv": atm_iv,
        "put_call_vol_ratio": put_vol / (call_vol + 1e-9),
        "put_call_oi_ratio": put_oi / (call_oi + 1e-9),
        "skew_25d_approx": skew,
        "spot_price": spot,
    }])


def collect_fear_greed() -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=30)
    payload = client.get_json("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"})
    frame = pd.DataFrame((payload or {}).get("data", []))
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp"].astype(int), unit="s", utc=True)
    frame["fng_value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["fng_class"] = frame["value_classification"]
    frame["symbol"] = "GLOBAL"
    return frame[["timestamp", "symbol", "fng_value", "fng_class"]]


def collect_coingecko_global() -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=25)
    payload = client.get_json("https://api.coingecko.com/api/v3/global")
    data = (payload or {}).get("data", {})
    return pd.DataFrame([{
        "timestamp": pd.Timestamp.now(tz="UTC"),
        "symbol": "GLOBAL",
        "total_market_cap_usd": data.get("total_market_cap", {}).get("usd"),
        "total_volume_24h_usd": data.get("total_volume", {}).get("usd"),
        "btc_dominance": data.get("market_cap_percentage", {}).get("btc"),
        "eth_dominance": data.get("market_cap_percentage", {}).get("eth"),
        "market_cap_change_24h": data.get("market_cap_change_percentage_24h_usd"),
    }])


def collect_mempool_space() -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=30)
    fees = client.get_json("https://mempool.space/api/v1/fees/recommended")
    stats = client.get_json("https://mempool.space/api/mempool")
    height = client.get_json("https://mempool.space/api/blocks/tip/height")
    return pd.DataFrame([{
        "timestamp": pd.Timestamp.now(tz="UTC"),
        "symbol": "GLOBAL",
        "mempool_fee_fastest": fees.get("fastestFee") if isinstance(fees, dict) else None,
        "mempool_fee_halfhour": fees.get("halfHourFee") if isinstance(fees, dict) else None,
        "mempool_tx_count": stats.get("count") if isinstance(stats, dict) else None,
        "mempool_vsize": stats.get("vsize") if isinstance(stats, dict) else None,
        "btc_block_height": int(height) if str(height).isdigit() else None,
    }])


def collect_gdelt_crypto_articles() -> pd.DataFrame:
    client = PublicHTTPClient(rate_limit_per_minute=15)
    payload = client.get_json(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": '(bitcoin OR ethereum OR cryptocurrency OR crypto OR solana)',
            "mode": "artlist",
            "format": "json",
            "maxrecords": 250,
            "timespan": "1day",
            "sort": "datedesc",
        },
    )
    articles = (payload or {}).get("articles", [])
    rows = []
    for article in articles:
        rows.append({
            "timestamp": pd.to_datetime(article.get("seendate"), utc=True, errors="coerce"),
            "symbol": "GLOBAL",
            "title": article.get("title"),
            "url": article.get("url"),
            "domain": article.get("domain"),
            "source_country": article.get("sourcecountry"),
            "language": article.get("language"),
        })
    return pd.DataFrame(rows).dropna(subset=["timestamp"]) if rows else pd.DataFrame()


def collect_rest_sources(
    *,
    source_names: Sequence[str],
    symbols: Sequence[str],
    start: str,
    end: Optional[str],
    raw_root: Path,
    dry_run: bool,
    mongo_uri: Optional[str] = None,
    mongo_db: str = "futur_market_context",
) -> Dict[str, int]:
    counts: Dict[str, int] = {}

    def safe(name: str, symbol: str, fn) -> pd.DataFrame:
        try:
            return fn()
        except Exception as exc:
            print("  skip %s %s (%s)" % (name, symbol, exc))
            return pd.DataFrame()

    def write(name: str, frame: pd.DataFrame, market_type: str, symbol: str, interval: str) -> None:
        if frame.empty:
            print("  empty", name, symbol)
            return
        print("  %s %s rows=%s" % (name, symbol, len(frame)))
        counts[name + ":" + symbol] = int(len(frame))
        if not dry_run:
            write_partitioned_parquet(frame, root=raw_root, source=name, market_type=market_type, symbol=symbol, interval=interval)
            if mongo_uri:
                write_mongo_snapshots(
                    frame,
                    mongo_uri=mongo_uri,
                    database=mongo_db,
                    collection=name,
                    source=name,
                    market_type=market_type,
                    symbol=symbol,
                    interval=interval,
                )

    for name in source_names:
        if name == "binance_futures_funding":
            for sym in symbols:
                write(name, safe(name, sym, lambda sym=sym: collect_binance_funding(sym, start, end)), "futures_um", sym, "8h")
        elif name == "binance_futures_positioning":
            for sym in symbols:
                write(name, safe(name, sym, lambda sym=sym: collect_binance_positioning(sym)), "futures_um", sym, "1h")
        elif name == "bybit_funding":
            for sym in symbols:
                write(name, safe(name, sym, lambda sym=sym: collect_bybit_funding(sym)), "futures_linear", sym, "8h")
        elif name == "okx_top_long_short":
            for sym in symbols:
                write(name, safe(name, sym, lambda sym=sym: collect_okx_top_long_short(sym)), "swap", sym, "1h")
        elif name == "deribit_options_summary":
            write(name, safe(name, "BTCUSDT", collect_deribit_options), "options", "BTCUSDT", "4h")
        elif name == "alternative_me_fear_greed":
            write(name, safe(name, "GLOBAL", collect_fear_greed), "global", "GLOBAL", "1d")
        elif name == "coingecko_global":
            write(name, safe(name, "GLOBAL", collect_coingecko_global), "global", "GLOBAL", "1h")
        elif name == "mempool_space_btc":
            write(name, safe(name, "GLOBAL", collect_mempool_space), "onchain", "GLOBAL", "1h")
        elif name == "gdelt_crypto_articles":
            write(name, safe(name, "GLOBAL", collect_gdelt_crypto_articles), "global_news", "GLOBAL", "1h")
    return counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill public/free data into data/raw")
    parser.add_argument("--profile", default="max-public")
    parser.add_argument("--granularity", nargs="+", default=["1h"], help="Intervals for Binance Vision, e.g. 1h 1m")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    parser.add_argument("--sources", nargs="+", default=["all"], help="Registry source names or all")
    parser.add_argument("--start", default="2017-08-01")
    parser.add_argument("--end")
    parser.add_argument("--raw-root", default=str(ROOT / "data" / "raw"))
    parser.add_argument("--checkpoint", default=str(ROOT / "data" / "raw" / "_checkpoints" / "backfill_public_data.json"))
    parser.add_argument("--mongo-uri", help="Optional MongoDB URI for real-time snapshot upserts")
    parser.add_argument("--mongo-db", default="futur_market_context")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-months", type=int, default=0, help="Debug cap for Binance Vision monthly files")
    parser.add_argument("--daily-tail-months", type=int, default=2, help="Also list/download daily Binance Vision files for the most recent N months")
    args = parser.parse_args(argv)

    if args.profile != "max-public":
        raise SystemExit("Only --profile max-public is supported")

    raw_root = Path(args.raw_root)
    registry = load_source_registry()
    requested = list(registry) if args.sources == ["all"] else args.sources
    symbols = discover_symbols(args.symbols)
    checkpoint = CheckpointStore(Path(args.checkpoint))

    print(json.dumps({"symbols": symbols, "sources": requested, "granularity": args.granularity}, indent=2))

    counts: Dict[str, int] = {}
    for source_name in requested:
        spec = registry.get(source_name)
        if spec is None:
            print("unknown source", source_name)
            continue
        if source_name in ("binance_vision_spot_klines", "binance_vision_um_futures_klines"):
            counts.update(fetch_binance_vision(
                spec=spec,
                symbols=symbols,
                intervals=args.granularity,
                start=args.start,
                end=args.end,
                raw_root=raw_root,
                checkpoint=checkpoint,
                dry_run=args.dry_run,
                max_months=args.max_months,
                daily_tail_months=args.daily_tail_months,
            ))
    rest = [name for name in requested if not name.startswith("binance_vision")]
    counts.update(collect_rest_sources(
        source_names=rest,
        symbols=symbols,
        start=args.start,
        end=args.end,
        raw_root=raw_root,
        dry_run=args.dry_run,
        mongo_uri=args.mongo_uri,
        mongo_db=args.mongo_db,
    ))
    print(json.dumps({"rows": counts}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
