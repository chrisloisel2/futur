#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
lastfetch.py — PRO version (robuste, OOM-safe, zéro plantage silencieux)

Objectif:
1) Charger Bitstamp BTCUSD 1m (2012-2025) -> proxies microstructure / orderbook (10y+)
2) Optionnel: overlay Binance spot 1m klines (si présent)
3) Optionnel: parser Binance Futures UM bookTicker (2023+) en streaming, downsample (ex: 5min),
   tolérant aux fichiers:
   - vrais ZIP contenant CSV
   - CSV brut
   - GZ
   - faux zip / zip corrompu / download partiel -> skip proprement

Sorties:
- ./out/microstructure_10y_proxy.parquet
- ./out/bbo_real_bookticker_2023plus.parquet (si bookTicker parsé)

Usage:
python3 lastfetch.py

Avec paramètres:
SYMBOL=BTCUSDT \
BINANCE_FUT_BOOKTICKER_DIR=./datasets/data_binance_vision/futures/um/monthly/bookTicker/BTCUSDT \
BOOKTICKER_DOWNSAMPLE=5min \
BOOKTICKER_CHUNKSIZE=50000 \
python3 lastfetch.py

Notes:
- BOOKTICKER_DOWNSAMPLE peut être: 1min, 5min, 15min, 1H, 1D ...
- BOOKTICKER_CHUNKSIZE: 20000–200000 recommandé (selon RAM)
"""

import os
import sys
import io
import re
import glob
import gzip
import zipfile
import traceback
from typing import Optional, Iterator, List, Tuple

import numpy as np
import pandas as pd


# -----------------------------
# Logging
# -----------------------------
def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> None:
    log(f"[FATAL] {msg}")
    sys.exit(code)


# -----------------------------
# Constants
# -----------------------------
EPS = 1e-12

BOOKTICKER_HEADER_CANON = [
    "update_id",
    "best_bid_price",
    "best_bid_qty",
    "best_ask_price",
    "best_ask_qty",
    "transaction_time",
    "event_time",
]

# Expected output columns after normalization
BOOKTICKER_OUT_COLS = ["ts", "bid", "bid_qty", "ask", "ask_qty"]


# -----------------------------
# Helpers
# -----------------------------
def safe_mkdir(path: str) -> None:
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def extract_yyyy_mm_from_filename(path: str) -> Optional[str]:
    base = os.path.basename(path)
    m = re.search(r"(20\d{2})-(\d{2})", base)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def is_zip_file(path: str) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except Exception:
        return False


def file_size(path: str) -> int:
    try:
        return int(os.path.getsize(path))
    except Exception:
        return -1


def list_all_files(in_dir: str) -> List[str]:
    """
    Très robuste: accepte fichiers + symlinks pointant vers fichiers.
    Ne filtre pas sur le nom.
    """
    if not os.path.isdir(in_dir):
        return []

    out: List[str] = []
    for name in sorted(os.listdir(in_dir)):
        fp = os.path.join(in_dir, name)
        try:
            if os.path.isfile(fp):
                out.append(fp)
        except Exception:
            continue
    return out


def pretty_path(p: str) -> str:
    return os.path.basename(p)


# -----------------------------
# Bitstamp 1m loader
# -----------------------------
def safe_read_bitstamp_1m(path_gz_or_csv: str) -> pd.DataFrame:
    if not os.path.isfile(path_gz_or_csv):
        raise FileNotFoundError(path_gz_or_csv)

    log(f"   -> reading {path_gz_or_csv}")
    df = pd.read_csv(path_gz_or_csv, compression="infer")

    cols_lower = {c.lower(): c for c in df.columns}
    ts_col = cols_lower.get("timestamp") or cols_lower.get("date") or df.columns[0]

    rename_map = {ts_col: "ts"}
    for c in ["open", "high", "low", "close", "volume"]:
        if c in cols_lower:
            rename_map[cols_lower[c]] = c

    df = df.rename(columns=rename_map)

    if "ts" not in df.columns:
        raise RuntimeError("Bitstamp timestamp column not found")

    ts = pd.to_numeric(df["ts"], errors="coerce")
    if ts.isna().all():
        raise RuntimeError("Bitstamp timestamp parse failed")

    ts_max = int(ts.dropna().max())
    if ts_max < 10_000_000_000:  # seconds
        df["ts"] = ts.astype("int64") * 1000
    else:
        df["ts"] = ts.astype("int64")

    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.sort_values("datetime").drop_duplicates("datetime").set_index("datetime")

    for c in ["open", "high", "low", "close", "volume"]:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")

    df = df.dropna(subset=["close"])
    return df[["ts", "open", "high", "low", "close", "volume"]].copy()


# -----------------------------
# Binance spot klines loader
# -----------------------------
def parse_binance_klines_zip(zip_path: str) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path, "r") as z:
        csvs = [n for n in z.namelist() if n.endswith(".csv")]
        if not csvs:
            return pd.DataFrame()
        with z.open(csvs[0]) as f:
            df = pd.read_csv(f, header=None)

    if df.empty:
        return df

    df.columns = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ]
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_values("datetime").drop_duplicates("datetime").set_index("datetime")

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")

    df = df.dropna(subset=["close"])
    df = df.rename(columns={"open_time": "ts"})
    return df[["ts", "open", "high", "low", "close", "volume"]].copy()


def load_binance_klines(dir_path: str) -> pd.DataFrame:
    if not os.path.isdir(dir_path):
        return pd.DataFrame()

    zips = sorted(glob.glob(os.path.join(dir_path, "*.zip")))
    if not zips:
        return pd.DataFrame()

    parts: List[pd.DataFrame] = []
    for zp in zips:
        try:
            parts.append(parse_binance_klines_zip(zp))
        except Exception:
            continue

    if not parts:
        return pd.DataFrame()

    df = pd.concat(parts, axis=0).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


# -----------------------------
# Proxies computation
# -----------------------------
def wilder_ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1.0 / n, adjust=False).mean()


def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def compute_proxy_microstructure_from_ohlcv(df_1m: pd.DataFrame) -> pd.DataFrame:
    out = df_1m.copy()

    close = out["close"]
    high = out["high"]
    low = out["low"]
    vol = out["volume"]

    log_ret = np.log((close / (close.shift(1) + EPS)).astype("float64"))

    tr = true_range(high, low, close)
    atr14 = wilder_ema(tr, 14)

    rv60 = log_ret.rolling(60, min_periods=60).std()
    sig_min_60 = rv60 / np.sqrt(60.0)

    spread_proxy = (0.15 * atr14 + 0.50 * (close * log_ret.abs().rolling(5, min_periods=5).mean())).clip(lower=0.0)
    spread_proxy = spread_proxy.ffill().fillna(0.0) + 1e-9

    mid = close
    best_bid = mid - spread_proxy / 2.0
    best_ask = mid + spread_proxy / 2.0
    spread_pct = spread_proxy / (mid + EPS)

    depth_proxy = (vol.rolling(60, min_periods=60).mean() / (sig_min_60 + 1e-6)).replace([np.inf, -np.inf], np.nan)

    candle_range = (high - low)
    clv = (close - low) / (candle_range + EPS)
    mom5 = log_ret.rolling(5, min_periods=5).sum()

    imbalance_proxy = (2.0 * clv - 1.0) * np.tanh(mom5 * 10.0)
    microprice = mid + (imbalance_proxy * spread_proxy * 0.25)

    depth_chg = depth_proxy.pct_change().replace([np.inf, -np.inf], np.nan)
    book_slope_proxy = -(depth_chg.rolling(30, min_periods=30).mean())
    convexity_proxy = depth_chg.diff().rolling(30, min_periods=30).mean()

    out["bbo_best_bid_proxy"] = best_bid
    out["bbo_best_ask_proxy"] = best_ask
    out["bbo_mid_proxy"] = mid
    out["bbo_spread_proxy"] = spread_proxy
    out["bbo_spread_pct_proxy"] = spread_pct

    out["ob_depth_proxy"] = depth_proxy
    out["ob_imbalance_proxy"] = imbalance_proxy
    out["ob_microprice_proxy"] = microprice
    out["ob_book_slope_proxy"] = book_slope_proxy
    out["ob_convexity_proxy"] = convexity_proxy
    return out


# -----------------------------
# Futures bookTicker streaming parser
# -----------------------------
def open_bookticker_text_stream(path: str):
    """
    Retourne un TEXT stream lisible par pandas.read_csv.
    Support:
    - vrai ZIP (csv inside)
    - GZIP
    - CSV brut
    - faux ZIP / partiel: tentative gzip, sinon brut (parfois c'est un binaire -> pd échouera -> skip)
    """
    # 1) ZIP réel
    if is_zip_file(path):
        z = zipfile.ZipFile(path, "r")
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            z.close()
            raise RuntimeError("zip contains no csv")
        bf = z.open(names[0], "r")
        tf = io.TextIOWrapper(bf, encoding="utf-8", errors="replace", newline="")
        tf._zipfile_ref = z
        tf._zipbin_ref = bf
        return tf

    # 2) GZIP possible
    try:
        gf = gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
        # test
        _ = gf.readline()
        gf.seek(0)
        gf._gzip_ref = True
        return gf
    except Exception:
        pass

    # 3) Brut
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def close_stream(s) -> None:
    try:
        if hasattr(s, "_zipbin_ref"):
            try:
                s._zipbin_ref.close()
            except Exception:
                pass
        if hasattr(s, "_zipfile_ref"):
            try:
                s._zipfile_ref.close()
            except Exception:
                pass
        s.close()
    except Exception:
        pass


def detect_header_mode(path: str) -> bool:
    """
    True = header present (columns with names)
    False = headerless (numeric columns)
    """
    s = open_bookticker_text_stream(path)
    try:
        first = s.readline()
        if first == "":
            return True  # doesn't matter
        # bookTicker has explicit named header on some dumps
        if ("best_bid_price" in first) and ("best_ask_price" in first):
            return True
        # could be header but different? we decide based on tokens
        # if line contains letters -> header
        if re.search(r"[A-Za-z_]", first):
            return True
        return False
    finally:
        close_stream(s)


def iter_bookticker_chunks(path: str, chunksize: int) -> Iterator[pd.DataFrame]:
    has_header = detect_header_mode(path)
    s = open_bookticker_text_stream(path)
    try:
        if has_header:
            reader = pd.read_csv(s, chunksize=chunksize, dtype=str)
        else:
            reader = pd.read_csv(s, header=None, chunksize=chunksize, dtype=str)
        for chunk in reader:
            yield chunk
    finally:
        close_stream(s)


def normalize_bookticker_chunk(chunk: pd.DataFrame, has_header: bool) -> pd.DataFrame:
    """
    Sortie: colonnes ts,bid,bid_qty,ask,ask_qty en float/int.
    """
    if chunk is None or chunk.empty:
        return pd.DataFrame(columns=BOOKTICKER_OUT_COLS)

    if has_header:
        cols = list(chunk.columns)
        lower_map = {c.lower(): c for c in cols}

        def pick(*cands: str) -> Optional[str]:
            for cand in cands:
                c = lower_map.get(cand.lower())
                if c is not None:
                    return c
            # substring
            for cand in cands:
                cl = cand.lower()
                for k, v in lower_map.items():
                    if cl in k:
                        return v
            return None

        c_ts = pick("event_time", "eventtime", "time", "timestamp")
        c_bid = pick("best_bid_price", "bestbidprice", "bid_price", "bidprice", "bid")
        c_bidq = pick("best_bid_qty", "bestbidqty", "bid_qty", "bidqty", "bidquantity")
        c_ask = pick("best_ask_price", "bestaskprice", "ask_price", "askprice", "ask")
        c_askq = pick("best_ask_qty", "bestaskqty", "ask_qty", "askqty", "askquantity")

        if not all([c_ts, c_bid, c_bidq, c_ask, c_askq]):
            raise RuntimeError(f"Header present but cannot map columns: {cols}")

        df = chunk[[c_ts, c_bid, c_bidq, c_ask, c_askq]].copy()
        df.columns = BOOKTICKER_OUT_COLS

    else:
        # headerless expected layout:
        # [0]=event_time, [1]=update_id, [2]=best_bid_price, [3]=best_bid_qty, [4]=best_ask_price, [5]=best_ask_qty
        if chunk.shape[1] < 6:
            return pd.DataFrame(columns=BOOKTICKER_OUT_COLS)
        df = chunk.iloc[:, [0, 2, 3, 4, 5]].copy()
        df.columns = BOOKTICKER_OUT_COLS

    # numeric convert
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    df["bid"] = pd.to_numeric(df["bid"], errors="coerce")
    df["bid_qty"] = pd.to_numeric(df["bid_qty"], errors="coerce")
    df["ask"] = pd.to_numeric(df["ask"], errors="coerce")
    df["ask_qty"] = pd.to_numeric(df["ask_qty"], errors="coerce")

    df = df.dropna(subset=["ts", "bid", "ask"])
    return df


def bucket_last(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    Retourne dernière ligne par bucket temporel.
    """
    if df.empty:
        return df

    ts = df["ts"].astype("int64")
    ts_max = int(ts.max())

    # Binance event_time: ms
    if ts_max > 10_000_000_000_000:  # ns
        dt = pd.to_datetime(ts, unit="ns", utc=True)
    else:
        dt = pd.to_datetime(ts, unit="ms", utc=True)

    tmp = df.copy()
    tmp.index = dt
    tmp = tmp.sort_index()

    # last per bucket
    out = tmp.groupby(pd.Grouper(freq=rule)).tail(1)
    out = out[~out.index.duplicated(keep="last")]
    return out


def update_store(store: Optional[pd.DataFrame], bucket_df: pd.DataFrame) -> pd.DataFrame:
    """
    store reste petit (buckets). On update sans concat explosion.
    """
    if store is None or store.empty:
        return bucket_df

    store = store.copy()
    store.update(bucket_df)
    missing = bucket_df.index.difference(store.index)
    if len(missing) > 0:
        store = pd.concat([store, bucket_df.loc[missing]], axis=0)
    return store.sort_index()


def parse_bookticker_file_month_oomsafe(path: str, downsample_rule: str, chunksize: int) -> Optional[pd.DataFrame]:
    """
    Parse un fichier bookTicker (zip/csv/gz) -> downsample -> df buckets.
    """
    sz = file_size(path)
    if sz >= 0 and sz < 1024:
        return None

    # if ".zip" but not real zip: still try (could be gz or csv or corrupted)
    has_header = detect_header_mode(path)

    store: Optional[pd.DataFrame] = None
    chunks = 0

    for chunk in iter_bookticker_chunks(path, chunksize=chunksize):
        chunks += 1
        if chunk is None or chunk.empty:
            continue

        # drop huge useless columns early if header
        if has_header:
            # keep only possible columns to reduce memory
            # (if missing, normalize() will error anyway)
            keep = []
            for c in chunk.columns:
                cl = str(c).lower()
                if ("best_bid" in cl) or ("best_ask" in cl) or ("event_time" in cl) or (cl in ("bid", "ask", "time", "timestamp")):
                    keep.append(c)
            if keep:
                chunk = chunk[keep]

        df_norm = normalize_bookticker_chunk(chunk, has_header=has_header)
        if df_norm.empty:
            continue

        df_b = bucket_last(df_norm, rule=downsample_rule)
        if df_b.empty:
            continue

        store = update_store(store, df_b)

        if chunks % 50 == 0:
            log(f"      -> chunks={chunks} buckets={0 if store is None else store.shape[0]}")

    if store is None or store.empty:
        return None

    store = store[BOOKTICKER_OUT_COLS].copy()
    store = store.dropna(subset=["ts", "bid", "ask"])
    return store.sort_index()


def parse_bookticker_dir_to_parquet_oomsafe(in_dir: str, out_path: str, downsample_rule: str, chunksize: int) -> int:
    """
    Parse tous les fichiers du dossier -> écrit parquet final.
    Très robuste sur discovery.
    """
    log(f"   -> bookTicker dir: {in_dir}")
    log(f"   -> cwd: {os.getcwd()}")
    log(f"   -> dir exists: {os.path.isdir(in_dir)}")

    files = list_all_files(in_dir)
    log(f"   -> file candidates found: {len(files)}")
    if files:
        log("   -> first candidates:")
        for f in files[:30]:
            log(f"      - {pretty_path(f)} size={file_size(f)}")

    if not files:
        log("   -> No bookTicker files found.")
        return 0

    log(f"   -> Found {len(files)} files")
    log(f"   -> Downsample: {downsample_rule}")
    log(f"   -> Chunksize: {chunksize}")

    safe_mkdir(os.path.dirname(out_path))

    part_paths: List[str] = []
    written = 0

    for fp in files:
        ym = extract_yyyy_mm_from_filename(fp) or pretty_path(fp)
        log(f"   -> {pretty_path(fp)} ({ym})")

        try:
            df_month = parse_bookticker_file_month_oomsafe(fp, downsample_rule, chunksize)
            if df_month is None or df_month.empty:
                log("      -> empty/invalid, skip")
                continue

            part_path = out_path.replace(".parquet", f".part_{ym}.parquet")
            df_month.to_parquet(part_path, index=True)
            part_paths.append(part_path)
            written += 1
            log(f"      -> written {pretty_path(part_path)} rows={df_month.shape[0]}")

        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"[WARN] failed {pretty_path(fp)}: {e}")
            continue

    log(f"   -> written parts: {written}/{len(files)}")
    if written == 0:
        return 0

    # merge parts
    dfs: List[pd.DataFrame] = []
    for p in part_paths:
        try:
            dfs.append(pd.read_parquet(p))
        except Exception as e:
            log(f"[WARN] cannot read part {pretty_path(p)}: {e}")

    if not dfs:
        return 0

    all_df = pd.concat(dfs, axis=0).sort_index()
    all_df = all_df[~all_df.index.duplicated(keep="last")]
    all_df.to_parquet(out_path, index=True)

    # cleanup parts
    for p in part_paths:
        try:
            os.remove(p)
        except Exception:
            pass

    return int(all_df.shape[0])


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")
    OUT_DIR = os.environ.get("OUT_DIR", "./out")

    BITSTAMP_CSV_GZ = os.environ.get(
        "BITSTAMP_CSV_GZ",
        "./datasets/data_bitstamp/btcusd_bitstamp_1min_2012-2025.csv.gz",
    )

    BINANCE_KLINES_DIR = os.environ.get(
        "BINANCE_KLINES_DIR",
        f"./datasets/binance_vision_downloads/data/spot/monthly/klines/{SYMBOL}/1m",
    )

    BINANCE_FUT_BOOKTICKER_DIR = os.environ.get(
        "BINANCE_FUT_BOOKTICKER_DIR",
        f"./datasets/data_binance_vision/futures/um/monthly/bookTicker/{SYMBOL}",
    )

    BOOKTICKER_DOWNSAMPLE = os.environ.get("BOOKTICKER_DOWNSAMPLE", "5min")
    BOOKTICKER_CHUNKSIZE = int(os.environ.get("BOOKTICKER_CHUNKSIZE", "50000"))

    safe_mkdir(OUT_DIR)

    # --- 1) Bitstamp 1m proxies
    log("== Load Bitstamp 1m ==")
    bitstamp = safe_read_bitstamp_1m(BITSTAMP_CSV_GZ)

    log("== Compute proxies from Bitstamp OHLCV ==")
    proxy = compute_proxy_microstructure_from_ohlcv(bitstamp)

    # --- 2) Overlay Binance spot klines (OHLCV)
    log("== Overlay Binance spot klines (OHLCV only) ==")
    bin_kl = pd.DataFrame()
    if os.path.isdir(BINANCE_KLINES_DIR):
        bin_kl = load_binance_klines(BINANCE_KLINES_DIR)

    if not bin_kl.empty:
        log("   -> Binance klines found")
        df_bn = compute_proxy_microstructure_from_ohlcv(bin_kl)
        proxy = df_bn.combine_first(proxy)
    else:
        log("   -> Binance klines NOT found or empty (ok)")

    # --- write proxy parquet
    proxy["symbol"] = SYMBOL
    proxy["source_base"] = "bitstamp_1m"
    proxy.loc[proxy.index >= pd.Timestamp("2017-08-01", tz="UTC"), "source_base"] = "binance_1m_if_available"

    cols_keep = [
        "ts", "open", "high", "low", "close", "volume",
        "bbo_best_bid_proxy", "bbo_best_ask_proxy", "bbo_mid_proxy",
        "bbo_spread_proxy", "bbo_spread_pct_proxy",
        "ob_depth_proxy", "ob_imbalance_proxy", "ob_microprice_proxy",
        "ob_book_slope_proxy", "ob_convexity_proxy",
    ]
    for c in cols_keep:
        if c not in proxy.columns:
            proxy[c] = np.nan

    proxy_out = proxy[["symbol", "source_base"] + cols_keep].copy()
    proxy_out = proxy_out.dropna(subset=["close"]).sort_index()

    proxy_path = os.path.join(OUT_DIR, "microstructure_10y_proxy.parquet")
    log(f"== Write {proxy_path} ==")
    proxy_out.to_parquet(proxy_path, index=True)

    # --- 3) Optional: futures bookTicker
    log("== Optional: real futures bookTicker (2023+) ==")
    if not os.path.isdir(BINANCE_FUT_BOOKTICKER_DIR):
        log(f"   -> Missing dir: {BINANCE_FUT_BOOKTICKER_DIR}")
        log("DONE")
        return 0

    out_bt = os.path.join(OUT_DIR, "bbo_real_bookticker_2023plus.parquet")
    nrows = parse_bookticker_dir_to_parquet_oomsafe(
        BINANCE_FUT_BOOKTICKER_DIR,
        out_bt,
        downsample_rule=BOOKTICKER_DOWNSAMPLE,
        chunksize=BOOKTICKER_CHUNKSIZE,
    )

    if nrows <= 0:
        log("   -> No valid futures bookTicker parsed.")
        log("DONE")
        return 0

    log(f"   -> parsed rows: {nrows}")
    df = pd.read_parquet(out_bt)

    # enrich
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread"] = (df["ask"] - df["bid"]).clip(lower=0.0)
    df["spread_pct"] = df["spread"] / (df["mid"] + EPS)
    df["microprice"] = (
        (df["ask"] * df["bid_qty"] + df["bid"] * df["ask_qty"]) /
        (df["bid_qty"] + df["ask_qty"] + EPS)
    )
    df["symbol"] = SYMBOL
    df["source"] = "binance_futures_bookTicker"

    df.to_parquet(out_bt, index=True)
    log(f"   -> enriched & rewritten: {out_bt}")

    log("DONE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        die("Interrupted by user", 130)
    except Exception:
        log("[FATAL] Unhandled exception:")
        traceback.print_exc()
        sys.exit(1)
