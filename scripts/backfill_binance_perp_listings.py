#!/usr/bin/env python3
"""
scripts/backfill_binance_perp_listings.py
─────────────────────────────────────────────────────────────────────────────
Backfill GRATUIT du calendrier de listings perp Binance + prix post-listing.

Mécanisme visé (edge LISTING) : momentum de découverte / reversion post-euphorie
dans les heures→jours qui suivent un listing perp. Point-in-time propre :
`onboardDate` (exchangeInfo) est l'heure exacte d'ouverture du contrat, connue
à l'avance via l'annonce officielle.

Anti-survivorship : les symboles délistés absents d'exchangeInfo sont récupérés
via l'énumération S3 de data.binance.vision (29 connus, la plupart pré-2023) ;
fapi sert encore les klines de certains (LUNA oui, BDXN non) — les manquants
sont comptés MISSING_DATA, jamais ignorés en silence.

Sortie : data/listings_backfill/binance/
  listings_calendar.parquet          (symbol, onboard_ts, first_kline_ts, status, source)
  klines_5m/<SYM>.parquet            (premières 72 h)
  klines_1h/<SYM>.parquet            (premiers 30 j)
  funding/<SYM>.parquet              (premiers ~30 j)
+ registry artifacts/data_registry/listings_backfill_store.yaml

    python3 scripts/backfill_binance_perp_listings.py --start 2023-01-01
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.data.atomic_parquet import atomic_write_parquet

FAPI = "https://fapi.binance.com"
VISION_S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
VISION_PREFIX = "data/futures/um/monthly/klines/"
OUT = ROOT / "data" / "listings_backfill" / "binance"
REG = ROOT / "artifacts" / "data_registry" / "listings_backfill_store.yaml"

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume",
              "close_time", "quote_volume", "n_trades", "taker_buy_base",
              "taker_buy_quote", "_ignore"]


def _get(url: str, tries: int = 4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):          # symbole inconnu de fapi
                raise
            if e.code in (418, 429):          # rate limit → backoff long
                time.sleep(30.0 * (k + 1))
                continue
            if k == tries - 1:
                raise
            time.sleep(2.0 + k)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2.0 + k)


def vision_symbols() -> set[str]:
    """Tous les symboles um ayant existé (S3 listing paginé)."""
    syms, marker = [], ""
    while True:
        url = f"{VISION_S3}?delimiter=/&prefix={VISION_PREFIX}"
        if marker:
            url += f"&marker={marker}"
        with urllib.request.urlopen(url, timeout=30) as r:
            xml = r.read().decode()
        page = re.findall(r"<Prefix>" + re.escape(VISION_PREFIX) + r"([^<]+)/</Prefix>", xml)
        syms.extend(page)
        if "<IsTruncated>true</IsTruncated>" in xml and page:
            marker = VISION_PREFIX + page[-1] + "/"
        else:
            return set(syms)


def klines(sym: str, interval: str, start_ms: int, limit: int) -> pd.DataFrame:
    data = _get(f"{FAPI}/fapi/v1/klines?symbol={sym}&interval={interval}"
                f"&startTime={start_ms}&limit={limit}")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data, columns=KLINE_COLS)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["n_trades"] = df["n_trades"].astype(int)
    return df[["timestamp", "open", "high", "low", "close", "volume",
               "quote_volume", "n_trades", "taker_buy_quote"]].reset_index(drop=True)


def funding(sym: str, start_ms: int) -> pd.DataFrame:
    data = _get(f"{FAPI}/fapi/v1/fundingRate?symbol={sym}&startTime={start_ms}&limit=1000")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).drop_duplicates("fundingTime")
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    return df.dropna(subset=["funding_rate"])[["timestamp", "funding_rate"]] \
             .sort_values("timestamp").reset_index(drop=True)


def build_calendar(start: pd.Timestamp) -> pd.DataFrame:
    info = _get(f"{FAPI}/fapi/v1/exchangeInfo")
    rows = []
    ei_syms = set()
    for s in info["symbols"]:
        ei_syms.add(s["symbol"])
        if not s["symbol"].endswith("USDT") or s.get("contractType") != "PERPETUAL":
            continue
        rows.append({"symbol": s["symbol"],
                     "onboard_ts": pd.Timestamp(int(s["onboardDate"]), unit="ms", tz="UTC"),
                     "status": s["status"], "source": "exchangeInfo"})
    # délistés retirés d'exchangeInfo : onboard = premier kline fapi si encore servi
    for sym in sorted(v for v in vision_symbols() if v.endswith("USDT")) :
        if sym in ei_syms:
            continue
        try:
            first = klines(sym, "1h", 0, 1)
        except Exception:
            rows.append({"symbol": sym, "onboard_ts": pd.NaT,
                         "status": "DELISTED_NO_DATA", "source": "vision_only"})
            continue
        ts = first["timestamp"].iloc[0] if len(first) else pd.NaT
        rows.append({"symbol": sym, "onboard_ts": ts,
                     "status": "DELISTED", "source": "vision+fapi"})
        time.sleep(0.15)
    cal = pd.DataFrame(rows).sort_values("onboard_ts").reset_index(drop=True)
    cal["in_window"] = cal["onboard_ts"] >= start
    return cal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--hours-5m", type=int, default=72)
    ap.add_argument("--days-1h", type=int, default=30)
    args = ap.parse_args()
    start = pd.Timestamp(args.start, tz="UTC")

    print("Construction du calendrier (exchangeInfo + vision anti-survivorship)…")
    cal = build_calendar(start)
    atomic_write_parquet(cal, OUT / "listings_calendar.parquet")
    n_delisted_nodata = int((cal["status"] == "DELISTED_NO_DATA").sum())
    targets = cal[cal["in_window"] & cal["onboard_ts"].notna()]
    print(f"  {len(cal)} perps USDT au total, {len(targets)} listés ≥ {args.start}, "
          f"{n_delisted_nodata} délistés sans data fapi (MISSING_DATA)")

    registry: dict = {"_meta": {"start": args.start, "n_targets": int(len(targets)),
                                "missing_delisted": n_delisted_nodata}}
    ok = miss = 0
    for i, row in enumerate(targets.itertuples(), 1):
        sym, onboard_ms = row.symbol, int(row.onboard_ts.timestamp() * 1000)
        try:
            k5 = klines(sym, "5m", onboard_ms, min(args.hours_5m * 12, 1500))
            time.sleep(0.12)
            k1 = klines(sym, "1h", onboard_ms, min(args.days_1h * 24, 1500))
            time.sleep(0.12)
            fu = funding(sym, onboard_ms)
            time.sleep(0.12)
        except Exception as e:
            registry[sym] = {"status": "MISSING_DATA", "error": str(e)[:120]}
            miss += 1
            continue
        if not len(k5) and not len(k1):
            registry[sym] = {"status": "MISSING_DATA", "error": "klines vides"}
            miss += 1
            continue
        if len(k5):
            atomic_write_parquet(k5, OUT / "klines_5m" / f"{sym}.parquet")
        if len(k1):
            atomic_write_parquet(k1, OUT / "klines_1h" / f"{sym}.parquet")
        if len(fu):
            atomic_write_parquet(fu, OUT / "funding" / f"{sym}.parquet")
        registry[sym] = {"status": "PASS", "k5": int(len(k5)), "k1": int(len(k1)),
                         "funding": int(len(fu)),
                         "first_kline": str(k1["timestamp"].iloc[0]) if len(k1) else None}
        ok += 1
        if i % 25 == 0:
            print(f"  {i}/{len(targets)} … {ok} PASS, {miss} MISSING")

    REG.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    REG.write_text(yaml.safe_dump(registry, sort_keys=True, allow_unicode=True))
    print(f"\nLISTINGS_BACKFILL : {ok} PASS / {miss} MISSING sur {len(targets)} cibles "
          f"→ {REG.relative_to(ROOT)}")
    print(f"  calendrier : {len(cal)} lignes → data/listings_backfill/binance/listings_calendar.parquet")
    print(f"  ⚠ {n_delisted_nodata} perps délistés sans data gratuite — biais de survivance "
          f"résiduel à mentionner dans tout résultat")


if __name__ == "__main__":
    main()
