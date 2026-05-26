#!/usr/bin/env python3
"""
scripts/data_daemon.py
=======================
Démon de collecte de données multi-source, entièrement automatisé.

Sources (100% gratuites, aucune clé API requise) :
  ├── Binance Spot   : OHLCV 1m / 5m / 15m / 1h / 4h / 1d (BTC, ETH, SOL, BNB)
  ├── Binance Futures: Funding, OI historique, L/S ratio, Taker flow, Liquidations proxy
  ├── Bybit Futures  : Funding rate (comparaison cross-exchange)
  ├── OKX            : L/S ratio top traders (cross-exchange)
  ├── Deribit        : Options BTC (IV ATM, skew 25d, put/call ratio)
  ├── Mempool.space  : On-chain BTC (fees, mempool, hashrate, blocks)
  ├── CoinGecko      : Dominance BTC, total market cap, stablecoin caps
  ├── Alternative.me : Fear & Greed Index
  └── Yahoo Finance  : Macro (DXY, SPX, Gold, VIX, 10Y yield, Oil)

Features calculées automatiquement (requises par le modèle ML) :
  - funding_rate_z_24/72, oihist_sumOpenInterest_z_24/72
  - fear_greed_value_z_24/72, global_ls_longShortRatio_z_24/72
  - liq_short_spike_12, liq_long_spike_12, liq_imbalance
  - taker_ls_imbalance, taker_ls_buySellRatio_z_24
  - oi_x_fng, funding_x_global_ls, vol_imbalance, trade_intensity

Planning :
  ├── Toutes les 1 min  : Binance klines 1m + liquidations proxy
  ├── Toutes les 5 min  : Binance klines 5m/15m + CoinGecko snapshot
  ├── Toutes les 1 heure: Funding all exchanges + OI + L/S + Options + On-chain
  ├── Toutes les 4 heures: Klines 4h/1d + Macro Yahoo + OHLCV feature recompute
  └── Au démarrage      : Bootstrap historique toutes sources

Usage :
  python scripts/data_daemon.py            # Démarrage complet
  python scripts/data_daemon.py --dry-run  # Test sans écriture
  python scripts/data_daemon.py --once     # Un seul cycle complet puis exit
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import json

import numpy as np
import pandas as pd
import requests
import schedule
from pymongo import MongoClient, UpdateOne, ASCENDING, DESCENDING

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(name)s]  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("data_daemon")

# ─── Config ──────────────────────────────────────────────────────────────────
MONGO_URI = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
DB_MAIN   = os.getenv("FUTUR_MONGO_DB", os.getenv("MONGODB_DB", "trader"))
DB_INTEL  = "market_intel"
FEATURE_COLLECTION = os.getenv(
    "FUTUR_MONGO_FEATURE_COLLECTION",
    os.getenv(
        "MONGODB_FEATURE_COLLECTION",
        os.getenv("FUTUR_MONGO_OHLCV_COLLECTION", os.getenv("MONGODB_HIST_COLLECTION", "historical_ohlcv_enriched")),
    ),
)

OHLCV_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
PERP_SYMBOLS  = ["BTCUSDT", "ETHUSDT"]  # perpetuals Binance
PRIMARY_SYMBOL = "BTCUSDT"

DRY_RUN = False

# ─── HTTP ─────────────────────────────────────────────────────────────────────
_session = requests.Session()
_session.headers["User-Agent"] = "futur-daemon/2.0"

def _get(url: str, params: dict = None, timeout: int = 12, ok_404: bool = False) -> Any:
    for attempt in range(4):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if ok_404 and r.status_code == 404:
                return []
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if ok_404 and e.response and e.response.status_code in (404, 400):
                return []
            if attempt == 3:
                log.warning(f"HTTP {e} — {url[:80]}")
                return []
            time.sleep(2 ** attempt)
        except Exception as e:
            if attempt == 3:
                log.warning(f"Request error {e} — {url[:80]}")
                return []
            time.sleep(2 ** attempt)
    return []

# ─── MongoDB ─────────────────────────────────────────────────────────────────
_mongo_client: Optional[MongoClient] = None

def db(name: str = DB_MAIN):
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _mongo_client[name]

def _native(v: Any) -> Any:
    if isinstance(v, (np.integer,)):   return int(v)
    if isinstance(v, (np.floating,)):  return None if np.isnan(v) else float(v)
    if isinstance(v, (np.bool_,)):     return bool(v)
    if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
        return None
    if hasattr(v, "to_pydatetime"):    return v.to_pydatetime()
    return v

def _clean_doc(doc: dict) -> dict:
    return {k: _native(v) for k, v in doc.items()}

def upsert_df(df: pd.DataFrame, coll_name: str, ts_col: str = "timestamp",
              extra_key: str = None, db_name: str = DB_MAIN) -> int:
    """Upsert un DataFrame dans MongoDB."""
    if df.empty or DRY_RUN:
        return 0
    coll = db(db_name)[coll_name]
    ops  = []
    for row in df.to_dict("records"):
        ts  = row[ts_col]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        flt = {ts_col: ts}
        if extra_key and extra_key in row:
            flt[extra_key] = row[extra_key]
        ops.append(UpdateOne(flt, {"$set": _clean_doc(row)}, upsert=True))
    if not ops:
        return 0
    n = 0
    for i in range(0, len(ops), 500):
        r = coll.bulk_write(ops[i:i+500], ordered=False)
        n += r.upserted_count + r.modified_count
    return n

def store_signal(source: str, data: dict, db_name: str = DB_INTEL) -> None:
    """Stocke un signal ponctuel dans market_intel.signals."""
    if DRY_RUN:
        return
    import hashlib, json as _json
    ts  = datetime.now(timezone.utc)
    doc = {"source": source, "timestamp": ts, **data}
    # fingerprint unique = source + minute arrondie (évite les doublons)
    fp_key = f"{source}:{ts.strftime('%Y%m%d%H%M')}"
    doc["fingerprint"] = hashlib.md5(fp_key.encode()).hexdigest()
    coll = db(db_name)["signals"]
    coll.update_one({"fingerprint": doc["fingerprint"]}, {"$set": _clean_doc(doc)}, upsert=True)

# ─────────────────────────────────────────────────────────────────────────────
# COLLECTEURS
# ─────────────────────────────────────────────────────────────────────────────

# ── 1. Binance Spot OHLCV multi-TF ───────────────────────────────────────────

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

def _klines_to_df(rows: list, symbol: str, interval: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_volume","trades",
            "taker_buy_base","taker_buy_quote","_ignore"]
    df = pd.DataFrame(rows, columns=cols)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").astype("Int64")
    df["symbol"]   = symbol
    df["interval"] = interval
    df["source"]   = "binance_spot"
    df["taker_buy_ratio"] = df["taker_buy_base"] / (df["volume"] + 1e-12)
    return df[["timestamp","symbol","interval","open","high","low","close",
               "volume","quote_volume","trades","taker_buy_base","taker_buy_quote",
               "taker_buy_ratio","source"]]

def fetch_klines_recent(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    rows = _get(BINANCE_KLINES, {"symbol": symbol, "interval": interval, "limit": limit})
    return _klines_to_df(rows, symbol, interval)

def fetch_klines_since(symbol: str, interval: str, since: str) -> pd.DataFrame:
    """Télécharge tous les klines depuis une date donnée."""
    start_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)
    now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    all_rows = []
    while start_ms < now_ms:
        rows = _get(BINANCE_KLINES,
                    {"symbol": symbol, "interval": interval,
                     "startTime": start_ms, "limit": 1000})
        if not rows:
            break
        all_rows.extend(rows)
        last = int(rows[-1][0])
        if len(rows) < 1000:
            break
        start_ms = last + 1
        time.sleep(0.05)
    return _klines_to_df(all_rows, symbol, interval)

def collect_klines_1m():
    log.info("collect_klines_1m")
    for sym in OHLCV_SYMBOLS:
        df = fetch_klines_recent(sym, "1m", limit=60)
        n  = upsert_df(df, "ohlcv_1m", extra_key="symbol")
    log.debug(f"  1m done")

def collect_klines_5m():
    log.info("collect_klines_5m")
    for sym in OHLCV_SYMBOLS:
        for iv in ("5m", "15m"):
            df = fetch_klines_recent(sym, iv, limit=200)
            upsert_df(df, f"ohlcv_{iv}", extra_key="symbol")

def collect_klines_slow():
    """4h et 1d — moins fréquent."""
    log.info("collect_klines_slow (4h, 1d)")
    for sym in OHLCV_SYMBOLS:
        for iv, limit in (("4h", 180), ("1d", 365)):
            df = fetch_klines_recent(sym, iv, limit=limit)
            upsert_df(df, f"ohlcv_{iv}", extra_key="symbol")


# ── 2. Binance Futures — Funding, OI, L/S, Liquidations proxy ────────────────

BINANCE_FAPI   = "https://fapi.binance.com/fapi/v1"
BINANCE_FDATA  = "https://fapi.binance.com/futures/data"

def fetch_binance_funding_recent(symbol: str = PRIMARY_SYMBOL, limit: int = 100) -> pd.DataFrame:
    rows = _get(f"{BINANCE_FAPI}/fundingRate", {"symbol": symbol, "limit": limit})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"]    = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["mark_price"]   = pd.to_numeric(df.get("markPrice", 0), errors="coerce").fillna(0)
    df["symbol"]       = symbol
    df["exchange"]     = "binance"
    return df[["timestamp","symbol","exchange","funding_rate","mark_price"]]

def fetch_bybit_funding(symbol: str = "BTCUSDT", limit: int = 100) -> pd.DataFrame:
    url  = "https://api.bybit.com/v5/market/funding/history"
    data = _get(url, {"category": "linear", "symbol": symbol, "limit": limit}, ok_404=True)
    rows = (data or {}).get("result", {}).get("list", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"]    = pd.to_datetime(df["fundingRateTimestamp"].astype(float), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["symbol"]       = symbol
    df["exchange"]     = "bybit"
    return df[["timestamp","symbol","exchange","funding_rate"]]

def fetch_okx_ls_ratio(inst: str = "BTC-USDT-SWAP", period: str = "1H", limit: int = 96) -> pd.DataFrame:
    url  = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader"
    data = _get(url, {"instId": inst, "period": period, "limit": str(limit)}, ok_404=True)
    rows = (data or {}).get("data", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts","longShortRatio"])
    df["timestamp"] = pd.to_datetime(df["ts"].astype(float), unit="ms", utc=True)
    df["ls_ratio_top_okx"] = pd.to_numeric(df["longShortRatio"], errors="coerce")
    df["symbol"]   = "BTC"
    df["exchange"] = "okx"
    return df[["timestamp","symbol","exchange","ls_ratio_top_okx"]]

def _fetch_fdata(endpoint: str, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    data = _get(f"{BINANCE_FDATA}/{endpoint}",
                {"symbol": PRIMARY_SYMBOL, "period": period, "limit": limit}, ok_404=True)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    ts = next((c for c in df.columns if "time" in c.lower() or "Time" in c), None)
    if ts:
        df["timestamp"] = pd.to_datetime(df[ts].astype(float), unit="ms", utc=True)
    return df

def fetch_open_interest_hist() -> pd.DataFrame:
    df = _fetch_fdata("openInterestHist")
    if df.empty:
        return df
    df["oi"]       = pd.to_numeric(df["sumOpenInterest"], errors="coerce")
    df["oi_value"] = pd.to_numeric(df["sumOpenInterestValue"], errors="coerce")
    df["symbol"]   = PRIMARY_SYMBOL
    return df[["timestamp","symbol","oi","oi_value"]]

def fetch_ls_global() -> pd.DataFrame:
    df = _fetch_fdata("globalLongShortAccountRatio")
    if df.empty:
        return df
    df["ls_ratio_global"] = pd.to_numeric(df.get("longShortRatio", 0), errors="coerce")
    df["ls_long_pct"]     = pd.to_numeric(df.get("longAccount",    0), errors="coerce").mul(100)
    df["ls_short_pct"]    = pd.to_numeric(df.get("shortAccount",   0), errors="coerce").mul(100)
    df["symbol"]          = PRIMARY_SYMBOL
    return df[["timestamp","symbol","ls_ratio_global","ls_long_pct","ls_short_pct"]]

def fetch_ls_top() -> pd.DataFrame:
    df = _fetch_fdata("topLongShortPositionRatio")
    if df.empty:
        return df
    df["ls_ratio_top_binance"] = pd.to_numeric(df.get("longShortRatio", 0), errors="coerce")
    df["symbol"] = PRIMARY_SYMBOL
    return df[["timestamp","symbol","ls_ratio_top_binance"]]

def collect_derivatives():
    log.info("collect_derivatives (funding, OI, L/S — Binance+Bybit+OKX)")

    # Funding multi-exchange
    df_bf = fetch_binance_funding_recent()
    df_by = fetch_bybit_funding()
    if not df_bf.empty:
        upsert_df(df_bf, "derivatives_funding", extra_key="symbol")
    if not df_by.empty:
        upsert_df(df_by, "derivatives_funding", extra_key=None)

    # Funding spread cross-exchange
    if not df_bf.empty and not df_by.empty:
        try:
            last_binance = float(df_bf.iloc[-1]["funding_rate"])
            last_bybit   = float(df_by.iloc[-1]["funding_rate"])
            spread = last_binance - last_bybit
            store_signal("funding_spread", {
                "btc_funding_binance": last_binance,
                "btc_funding_bybit":   last_bybit,
                "btc_funding_spread":  spread,
                "abs_spread":          abs(spread),
            })
            log.info(f"  Funding: Binance={last_binance:.5f} Bybit={last_bybit:.5f} spread={spread:.5f}")
        except Exception as e:
            log.warning(f"  Funding spread error: {e}")

    # OI
    df_oi = fetch_open_interest_hist()
    if not df_oi.empty:
        upsert_df(df_oi, "derivatives_oi", extra_key="symbol")

    # L/S global + top traders (Binance + OKX)
    df_lsg = fetch_ls_global()
    df_lst = fetch_ls_top()
    df_okx = fetch_okx_ls_ratio()
    if not df_lsg.empty:
        upsert_df(df_lsg, "derivatives_ls", extra_key="symbol")
    if not df_lst.empty:
        upsert_df(df_lst, "derivatives_ls_top", extra_key="symbol")
    if not df_okx.empty:
        upsert_df(df_okx, "derivatives_ls_top", extra_key="symbol")

    log.info("  derivatives done")


# ── 3. Deribit Options ────────────────────────────────────────────────────────

def collect_options():
    log.info("collect_options (Deribit BTC)")
    url  = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    data = _get(url, {"currency": "BTC", "kind": "option"}, ok_404=True)
    rows = (data or {}).get("result", [])
    if not rows:
        log.warning("  Deribit: aucune donnée options")
        return

    df = pd.DataFrame(rows)

    # Filtrer les options actives avec volume
    df["mark_iv"]     = pd.to_numeric(df.get("mark_iv", np.nan), errors="coerce")
    df["bid_iv"]      = pd.to_numeric(df.get("bid_iv",  np.nan), errors="coerce")
    df["ask_iv"]      = pd.to_numeric(df.get("ask_iv",  np.nan), errors="coerce")
    df["volume"]      = pd.to_numeric(df.get("volume",  0),       errors="coerce").fillna(0)
    df["open_interest"] = pd.to_numeric(df.get("open_interest", 0), errors="coerce").fillna(0)
    df["underlying_price"] = pd.to_numeric(df.get("underlying_price", np.nan), errors="coerce")

    # Décomposer le nom de l'instrument (BTC-27JUN25-100000-C)
    def parse_inst(name: str):
        parts = name.split("-")
        if len(parts) == 4:
            return parts[1], float(parts[2]), parts[3]  # expiry, strike, C/P
        return None, None, None

    df[["expiry","strike","option_type"]] = pd.DataFrame(
        df["instrument_name"].apply(lambda x: pd.Series(parse_inst(x)))
    )
    df = df.dropna(subset=["strike","mark_iv"])
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

    # Calculer les métriques agrégées
    now = datetime.now(timezone.utc)
    calls = df[df["option_type"] == "C"]
    puts  = df[df["option_type"] == "P"]

    # ATM IV (strike le plus proche du spot)
    spot   = df["underlying_price"].dropna().mean()
    atm_iv = None
    if spot and not df.empty:
        df["dist_from_atm"] = (df["strike"] - spot).abs()
        atm_row = df.nsmallest(5, "dist_from_atm")
        atm_iv  = float(atm_row["mark_iv"].mean()) if not atm_row.empty else None

    # Put/Call ratio (volume)
    call_vol = float(calls["volume"].sum())
    put_vol  = float(puts["volume"].sum())
    pc_ratio = put_vol / (call_vol + 1e-9)

    # Put/Call OI ratio
    call_oi  = float(calls["open_interest"].sum())
    put_oi   = float(puts["open_interest"].sum())
    pc_oi    = put_oi / (call_oi + 1e-9)

    # 25-delta skew approx (puts 25d more expensive than calls 25d → fear)
    # Approximation : IV weighted average
    weighted_put_iv  = float((puts["mark_iv"]  * puts["open_interest"]).sum()  / (put_oi  + 1e-9))
    weighted_call_iv = float((calls["mark_iv"] * calls["open_interest"]).sum() / (call_oi + 1e-9))
    skew_25d = weighted_put_iv - weighted_call_iv  # >0 = fear (puts premium)

    metrics = {
        "timestamp":      now,
        "symbol":         "BTC",
        "spot_price":     spot,
        "atm_iv":         atm_iv,
        "put_call_vol_ratio":   pc_ratio,
        "put_call_oi_ratio":    pc_oi,
        "skew_25d_approx":      skew_25d,
        "total_call_oi":        call_oi,
        "total_put_oi":         put_oi,
        "total_call_vol":       call_vol,
        "total_put_vol":        put_vol,
        "n_instruments":        len(df),
    }

    if not DRY_RUN:
        db()["options_btc"].insert_one(_clean_doc(metrics))

    log.info(f"  Options: ATM_IV={atm_iv:.1f}% P/C={pc_ratio:.2f} skew={skew_25d:.1f}% spot=${spot:,.0f}")


# ── 4. On-chain — Mempool.space ───────────────────────────────────────────────

MEMPOOL = "https://mempool.space/api"

def collect_onchain():
    log.info("collect_onchain (mempool.space)")
    now = datetime.now(timezone.utc)
    doc = {"timestamp": now, "source": "mempool"}

    # Frais recommandés
    fees = _get(f"{MEMPOOL}/v1/fees/recommended")
    if fees:
        doc["mempool_fee_fastest"]  = fees.get("fastestFee", 0)
        doc["mempool_fee_halfhour"] = fees.get("halfHourFee", 0)
        doc["mempool_fee_hour"]     = fees.get("hourFee", 0)
        doc["mempool_fee_economy"]  = fees.get("economyFee", 0)

    # Mempool stats
    stats = _get(f"{MEMPOOL}/mempool")
    if stats:
        doc["mempool_tx_count"]       = stats.get("count", 0)
        doc["mempool_vsize"]          = stats.get("vsize", 0)
        doc["mempool_total_fee_btc"]  = float(stats.get("total_fee", 0)) / 1e8

    # Dernier bloc
    tip = _get(f"{MEMPOOL}/blocks/tip/height")
    if tip:
        doc["btc_block_height"] = int(tip)

    # Hashrate (approximatif via derniers blocs)
    hashrate = _get(f"{MEMPOOL}/v1/mining/hashrate/1m")
    if isinstance(hashrate, dict):
        h = hashrate.get("currentHashrate", None)
        if h:
            doc["btc_hashrate_eh"] = float(h) / 1e18  # EH/s

    # Difficulty
    diff = _get(f"{MEMPOOL}/v1/difficulty-adjustment")
    if isinstance(diff, dict):
        doc["btc_difficulty_change_pct"] = diff.get("difficultyChange", None)
        doc["btc_remaining_blocks"]      = diff.get("remainingBlocks", None)
        doc["btc_estimated_retarget"]    = diff.get("estimatedRetargetDate", None)

    if not DRY_RUN and len(doc) > 2:
        db()["onchain_btc"].insert_one(_clean_doc(doc))
        log.info(f"  mempool: height={doc.get('btc_block_height',0):,} "
                 f"fee={doc.get('mempool_fee_fastest',0)} sat/vB "
                 f"txs={doc.get('mempool_tx_count',0):,}")


# ── 5. Macro — Yahoo Finance ──────────────────────────────────────────────────

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

def _yahoo_price(ticker: str) -> Optional[Tuple[float, float]]:
    """Retourne (last_price, chg_1d_pct) via l'API Yahoo Finance directe."""
    url = YAHOO_URL.format(ticker=ticker)
    try:
        r = _session.get(url, params={"interval": "1h", "range": "5d"},
                         headers=YAHOO_HEADERS, timeout=10)
        if not r.ok:
            return None
        data   = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        closes  = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes  = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None
        last   = float(closes[-1])
        prev1d = float(closes[-24]) if len(closes) >= 24 else float(closes[0])
        chg    = (last - prev1d) / (prev1d + 1e-12) * 100
        return last, chg
    except Exception:
        return None

def collect_macro():
    log.info("collect_macro (Yahoo Finance direct API)")
    TICKERS = {
        "DX-Y.NYB": "dxy",
        "^GSPC":    "spx",
        "GC=F":     "gold",
        "^VIX":     "vix",
        "^TNX":     "us10y_yield",
        "CL=F":     "oil_wti",
    }
    doc = {"timestamp": datetime.now(timezone.utc), "source": "yahoo_finance"}
    for ticker, name in TICKERS.items():
        result = _yahoo_price(ticker)
        if result:
            doc[f"{name}_price"],  doc[f"{name}_chg_1d"] = result

    if not DRY_RUN and len(doc) > 2:
        db()["macro_global"].insert_one(_clean_doc(doc))
    log.info(f"  macro: DXY={doc.get('dxy_price',0):.2f} "
             f"SPX={doc.get('spx_price',0):.0f} "
             f"VIX={doc.get('vix_price',0):.1f} "
             f"Gold=${doc.get('gold_price',0):.0f} "
             f"Oil=${doc.get('oil_wti_price',0):.2f}")


# ── 6. CoinGecko — Dominance + Market Cap ────────────────────────────────────

def collect_coingecko():
    log.info("collect_coingecko")
    now = datetime.now(timezone.utc)

    global_data = _get("https://api.coingecko.com/api/v3/global")
    if global_data:
        gd  = global_data.get("data", {})
        doc = {
            "timestamp":               now,
            "source":                  "coingecko",
            "total_market_cap_usd":    gd.get("total_market_cap", {}).get("usd", 0),
            "total_volume_24h_usd":    gd.get("total_volume", {}).get("usd", 0),
            "btc_dominance":           gd.get("market_cap_percentage", {}).get("btc", 0),
            "eth_dominance":           gd.get("market_cap_percentage", {}).get("eth", 0),
            "stablecoin_volume_24h":   gd.get("total_volume", {}).get("usdt", 0),
            "active_cryptocurrencies": gd.get("active_cryptocurrencies", 0),
            "market_cap_change_24h":   gd.get("market_cap_change_percentage_24h_usd", 0),
            "defi_volume_24h":         gd.get("total_volume", {}).get("usd", 0),
        }
        if not DRY_RUN:
            db()["coingecko_global"].insert_one(_clean_doc(doc))
        log.info(f"  CoinGecko: BTC dom={doc['btc_dominance']:.1f}% "
                 f"mcap=${doc['total_market_cap_usd']/1e12:.2f}T "
                 f"chg24h={doc['market_cap_change_24h']:.1f}%")

    # Top coins snapshot
    coins = _get("https://api.coingecko.com/api/v3/coins/markets", {
        "vs_currency": "usd",
        "ids": "bitcoin,ethereum,solana,bnb,xrp,cardano",
        "order": "market_cap_desc",
        "price_change_percentage": "1h,24h,7d",
    })
    if coins and not DRY_RUN:
        for coin in coins:
            coin["timestamp"] = now
            coin["source"]    = "coingecko_markets"
            db()["coingecko_coins"].update_one(
                {"id": coin["id"]},
                {"$set": _clean_doc(coin)},
                upsert=True,
            )


# ── 7. Fear & Greed ───────────────────────────────────────────────────────────

def collect_fear_greed():
    log.info("collect_fear_greed")
    data = _get("https://api.alternative.me/fng/?limit=3&format=json")
    rows = (data or {}).get("data", [])
    if not rows:
        return

    latest = rows[0]
    fng_val = int(latest["value"])
    doc = {
        "timestamp":    pd.Timestamp(int(latest["timestamp"]), unit="s", tz="UTC").to_pydatetime(),
        "fng_value":    fng_val,
        "fng_class":    latest["value_classification"],
        "source":       "alternative_me",
    }
    if not DRY_RUN:
        db()["sentiment_fng"].update_one(
            {"timestamp": doc["timestamp"]},
            {"$set": doc},
            upsert=True,
        )
    log.info(f"  F&G: {fng_val} ({latest['value_classification']})")


# ── 8. News RSS + NLP ────────────────────────────────────────────────────────

def collect_news():
    log.info("collect_news (RSS multi-sources + VADER sentiment)")
    try:
        import subprocess
        r = subprocess.run(
            ["/usr/bin/python3", str(ROOT / "scripts" / "fetch_news.py"), "--update"],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        )
        lines = (r.stdout + r.stderr).strip().split("\n")
        for l in lines[-5:]:
            if any(k in l for k in ["TOTAL", "Collectés", "nouveaux"]):
                log.info(f"  {l.strip()}")
    except Exception as e:
        log.warning(f"  news error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# COMPUTATION DES FEATURES ML MANQUANTES
# ─────────────────────────────────────────────────────────────────────────────

def compute_and_enrich_ohlcv():
    """
    Calcule toutes les features dérivées manquantes dans la collection OHLCV/features.
    Requiert par le modèle ML :
      - funding_rate_z_24/72
      - oihist_sumOpenInterest_z_24/72
      - fear_greed_value_z_24/72
      - global_ls_longShortRatio_z_24/72
      - taker_ls_buySellRatio_z_24
      - liq_short_spike_12, liq_long_spike_12, liq_imbalance
      - oi_x_fng, funding_x_global_ls
      - vol_imbalance, trade_intensity
    """
    log.info("compute_and_enrich_ohlcv — calcul des features ML manquantes")

    ohlcv_coll = db()[FEATURE_COLLECTION]
    total      = ohlcv_coll.count_documents({})
    if total == 0:
        return

    # Charger toutes les barres avec les champs nécessaires
    fields = {
        "_id": 1, "timestamp": 1,
        "funding_rate": 1, "oi": 1, "fng_value": 1, "ls_ratio_global": 1,
        "taker_buy_ratio_base": 1, "volume": 1, "close": 1, "high": 1, "low": 1,
        "taker_buy_quote": 1, "taker_buy_cumul_12": 1, "delta_taker_cumul_12": 1,
    }
    docs = list(ohlcv_coll.find({}, fields).sort("timestamp", ASCENDING))
    if not docs:
        return

    df = pd.DataFrame(docs)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.sort_values("timestamp").reset_index(drop=True)

    # ── Features requises mais manquantes ────────────────────────────────────

    # Z-scores rolling sur les séries dérivées
    def rolling_z(series: pd.Series, window: int) -> pd.Series:
        m = series.rolling(window, min_periods=1).mean()
        s = series.rolling(window, min_periods=1).std().replace(0, 1)
        return (series - m) / s

    if "funding_rate" in df.columns:
        df["funding_rate_z_24"] = rolling_z(df["funding_rate"].fillna(0), 24)
        df["funding_rate_z_72"] = rolling_z(df["funding_rate"].fillna(0), 72)

    if "oi" in df.columns:
        df["oihist_sumOpenInterest_z_24"] = rolling_z(df["oi"].fillna(method="ffill").fillna(0), 24)
        df["oihist_sumOpenInterest_z_72"] = rolling_z(df["oi"].fillna(method="ffill").fillna(0), 72)

    if "fng_value" in df.columns:
        # F&G est daily → ffill pour aligner sur chaque heure
        df["fng_value"] = df["fng_value"].fillna(method="ffill")
        df["fear_greed_value_z_24"] = rolling_z(df["fng_value"].fillna(50), 24)
        df["fear_greed_value_z_72"] = rolling_z(df["fng_value"].fillna(50), 72)

    if "ls_ratio_global" in df.columns:
        df["ls_ratio_global"] = df["ls_ratio_global"].fillna(method="ffill")
        df["global_ls_longShortRatio_z_24"] = rolling_z(df["ls_ratio_global"].fillna(1), 24)
        df["global_ls_longShortRatio_z_72"] = rolling_z(df["ls_ratio_global"].fillna(1), 72)

    # Taker L/S imbalance
    if "taker_buy_ratio_base" in df.columns:
        tr = df["taker_buy_ratio_base"].fillna(0.5)
        df["taker_ls_imbalance"]       = tr - 0.5           # > 0 = buy pressure
        df["taker_ls_buySellRatio_z_24"] = rolling_z(tr, 24)

    # Liquidation proxy : spike de volume avec retournement de prix
    # Quand le prix monte fort + volume spike → liquidation shorts
    # Quand le prix baisse fort + volume spike → liquidation longs
    if "close" in df.columns and "volume" in df.columns:
        ret   = df["close"].pct_change().fillna(0)
        vol   = df["volume"].fillna(0)
        vol_z = rolling_z(vol, 24)

        # Spike courts liquidés : retour haussier + volume anormal
        df["liq_short_spike_12"] = (
            (ret.rolling(1).mean() > ret.rolling(24).mean() + ret.rolling(24).std()) &
            (vol_z > 2.0)
        ).astype(int).rolling(12, min_periods=1).sum()

        # Spike longs liquidés : retour baissier + volume anormal
        df["liq_long_spike_12"]  = (
            (ret.rolling(1).mean() < ret.rolling(24).mean() - ret.rolling(24).std()) &
            (vol_z > 2.0)
        ).astype(int).rolling(12, min_periods=1).sum()

        df["liq_imbalance"] = df["liq_short_spike_12"] - df["liq_long_spike_12"]

    # Vol imbalance (buy vol vs sell vol)
    if "taker_buy_quote" in df.columns and "volume" in df.columns:
        buy_vol  = df["taker_buy_quote"].fillna(0)
        sell_vol = df["volume"].fillna(0) - buy_vol
        df["vol_imbalance"] = (buy_vol - sell_vol) / (buy_vol + sell_vol + 1e-9)

    # Trade intensity (trades / avg_volume)
    if "trades" in df.columns and "volume" in df.columns:
        df["trade_intensity"] = (
            df["trades"].fillna(0) /
            (df["volume"].rolling(24, min_periods=1).mean().replace(0, 1))
        )
    elif "volume" in df.columns:
        df["trade_intensity"] = df["volume"] / (df["volume"].rolling(24, min_periods=1).mean().replace(0, 1) + 1e-9)

    # Cross-features (produits entre sources)
    if "funding_rate" in df.columns and "fng_value" in df.columns:
        df["oi_x_fng"] = df["funding_rate"].fillna(0) * df["fng_value"].fillna(50) / 100

    if "funding_rate_z_24" in df.columns and "global_ls_longShortRatio_z_24" in df.columns:
        df["funding_x_global_ls"] = df["funding_rate_z_24"] * df["global_ls_longShortRatio_z_24"]

    # ── Upsert dans MongoDB ───────────────────────────────────────────────────
    DERIVED = [
        "funding_rate_z_24","funding_rate_z_72",
        "oihist_sumOpenInterest_z_24","oihist_sumOpenInterest_z_72",
        "fear_greed_value_z_24","fear_greed_value_z_72",
        "global_ls_longShortRatio_z_24","global_ls_longShortRatio_z_72",
        "taker_ls_imbalance","taker_ls_buySellRatio_z_24",
        "liq_short_spike_12","liq_long_spike_12","liq_imbalance",
        "vol_imbalance","trade_intensity",
        "oi_x_fng","funding_x_global_ls",
    ]
    available = [c for c in DERIVED if c in df.columns]

    ops = []
    for _, row in df[["_id"] + available].iterrows():
        update = {c: _native(row[c]) for c in available}
        ops.append(UpdateOne({"_id": row["_id"]}, {"$set": update}))
        if len(ops) >= 2000:
            if not DRY_RUN:
                ohlcv_coll.bulk_write(ops, ordered=False)
            ops = []
    if ops and not DRY_RUN:
        ohlcv_coll.bulk_write(ops, ordered=False)

    log.info(f"  features dérivées calculées: {available}")
    log.info(f"  {total} barres OHLCV enrichies")


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP HISTORIQUE
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_historical():
    """Télécharge tout l'historique disponible au premier démarrage."""
    log.info("=== BOOTSTRAP HISTORIQUE ===")
    ohlcv_coll = db()[FEATURE_COLLECTION]

    # Multi-TF historique
    intervals_history = {
        "5m":  "2023-01-01",
        "15m": "2021-01-01",
        "4h":  "2017-08-17",
        "1d":  "2017-08-17",
    }
    for sym in OHLCV_SYMBOLS:
        for iv, since in intervals_history.items():
            coll_name = f"ohlcv_{iv}"
            n_existing = db()[coll_name].count_documents({"symbol": sym})
            if n_existing > 100:
                log.info(f"  {sym} {iv}: déjà {n_existing:,} bars, skip")
                continue
            log.info(f"  Bootstrap {sym} {iv} depuis {since}...")
            df = fetch_klines_since(sym, iv, since)
            n  = upsert_df(df, coll_name, extra_key="symbol")
            log.info(f"  {sym} {iv}: {len(df):,} bars stockées ({n} nouvelles)")

    # Funding historique complet
    df_fund = fetch_klines_since.__module__  # juste pour vérifier import
    from scripts.ingest_alpha_data import (
        fetch_funding_rates, fetch_fear_greed,
        FUNDING_START_MS, store_collection
    )
    log.info("  Bootstrap funding rates complet...")
    df_fr = fetch_funding_rates(FUNDING_START_MS)
    store_collection(df_fr, "derivatives_funding")

    log.info("  Bootstrap Fear & Greed...")
    df_fg = fetch_fear_greed()
    store_collection(df_fg, "sentiment_fng")

    log.info("=== BOOTSTRAP TERMINÉ ===")


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

def run_safe(fn, name: str):
    """Wrapper qui loggue les erreurs sans crasher le daemon."""
    try:
        fn()
    except Exception as e:
        log.error(f"[{name}] ERREUR: {e}", exc_info=True)

def setup_schedule():
    # Toutes les 1 minute
    schedule.every(1).minutes.do(lambda: run_safe(collect_klines_1m, "klines_1m"))

    # Toutes les 5 minutes
    schedule.every(5).minutes.do(lambda: run_safe(collect_klines_5m, "klines_5m"))
    schedule.every(5).minutes.do(lambda: run_safe(collect_fear_greed, "fear_greed"))

    # Toutes les heures
    schedule.every(1).hours.do(lambda: run_safe(collect_derivatives, "derivatives"))
    schedule.every(1).hours.do(lambda: run_safe(collect_onchain, "onchain"))
    schedule.every(1).hours.do(lambda: run_safe(collect_coingecko, "coingecko"))

    # Toutes les 4 heures
    schedule.every(4).hours.do(lambda: run_safe(collect_klines_slow, "klines_slow"))
    schedule.every(4).hours.do(lambda: run_safe(collect_options, "options"))
    schedule.every(4).hours.do(lambda: run_safe(collect_macro, "macro"))
    schedule.every(4).hours.do(lambda: run_safe(compute_and_enrich_ohlcv, "enrich_ohlcv"))
    schedule.every(2).hours.do(lambda: run_safe(collect_news, "news"))

    log.info("Scheduler configuré — prochains jobs:")
    for job in schedule.jobs:
        log.info(f"  {job}")


def run_once():
    """Un cycle complet de toutes les collectes."""
    log.info("=== RUN ONCE ===")
    for fn, name in [
        (collect_klines_1m,    "klines_1m"),
        (collect_klines_5m,    "klines_5m"),
        (collect_klines_slow,  "klines_slow"),
        (collect_derivatives,  "derivatives"),
        (collect_onchain,      "onchain"),
        (collect_coingecko,    "coingecko"),
        (collect_fear_greed,   "fear_greed"),
        (collect_options,      "options"),
        (collect_macro,        "macro"),
        (collect_news,         "news"),
        (compute_and_enrich_ohlcv, "enrich_ohlcv"),
    ]:
        run_safe(fn, name)
    log.info("=== RUN ONCE TERMINÉ ===")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global DRY_RUN
    parser = argparse.ArgumentParser(description="Data Daemon — collecteur multi-source automatisé")
    parser.add_argument("--dry-run",   action="store_true", help="Test sans écriture MongoDB")
    parser.add_argument("--once",      action="store_true", help="Un cycle puis exit")
    parser.add_argument("--no-boot",   action="store_true", help="Pas de bootstrap historique")
    parser.add_argument("--only",      help="Collecteur unique: derivatives|onchain|macro|options|enrich")
    args = parser.parse_args()

    DRY_RUN = args.dry_run
    if DRY_RUN:
        log.info("DRY-RUN activé — aucune écriture MongoDB")

    log.info("=" * 60)
    log.info("FUTUR DATA DAEMON v2.0")
    log.info(f"MongoDB: {MONGO_URI} / {DB_MAIN}")
    log.info("=" * 60)

    # Collecteur unique
    if args.only:
        MAP = {
            "derivatives": collect_derivatives,
            "onchain":     collect_onchain,
            "macro":       collect_macro,
            "options":     collect_options,
            "coingecko":   collect_coingecko,
            "fear_greed":  collect_fear_greed,
            "enrich":      compute_and_enrich_ohlcv,
            "klines_1m":   collect_klines_1m,
            "klines_5m":   collect_klines_5m,
            "klines_slow": collect_klines_slow,
        }
        fn = MAP.get(args.only)
        if fn:
            run_safe(fn, args.only)
        else:
            log.error(f"Collecteur inconnu: {args.only}. Disponibles: {list(MAP.keys())}")
        return

    # Bootstrap historique
    if not args.no_boot:
        try:
            bootstrap_historical()
        except Exception as e:
            log.error(f"Bootstrap error: {e}")

    # Un seul cycle
    if args.once:
        run_once()
        return

    # Daemon continu
    run_once()           # Premier cycle immédiat
    setup_schedule()

    log.info("Daemon démarré — Ctrl+C pour arrêter")
    while True:
        schedule.run_pending()
        time.sleep(15)


if __name__ == "__main__":
    main()
