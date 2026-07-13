#!/usr/bin/env python3
"""
scripts/backfill_binance_quarterly_vision.py
─────────────────────────────────────────────────────────────────────────────
Backfill des klines 1d de TOUS les contrats trimestriels USDT-M (Binance
Vision) — la donnée de la jambe BASIS_TERM (2e carry, indépendant du funding).

Sortie : data/derivatives_backfill/binance_vision_quarterly/{CONTRACT}_1d.parquet
         + contracts.json (échéances). Idempotent.
"""
from __future__ import annotations

import io
import json
import re
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "derivatives_backfill" / "binance_vision_quarterly"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "qvol", "count", "tbv", "tbqv", "ignore"]


def list_contracts():
    req = urllib.request.Request(
        f"{S3}?list-type=2&prefix=data/futures/um/monthly/klines/&delimiter=/&max-keys=1000")
    xml = urllib.request.urlopen(req, timeout=30).read().decode()
    names = re.findall(r"<Prefix>data/futures/um/monthly/klines/([A-Z]+USDT_\d{6})/</Prefix>", xml)
    return sorted(set(names))


def months_of(contract: str):
    yymmdd = contract.split("_")[1]
    exp = date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
    # un trimestriel liste ~6-7 mois avant échéance
    cur = date(exp.year - (1 if exp.month <= 7 else 0),
               ((exp.month - 7) % 12) + 1, 1)
    out = []
    while cur <= exp:
        out.append(f"{cur.year}-{cur.month:02d}")
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return exp, out


def fetch(contract: str, ym: str):
    url = f"{BASE}/{contract}/1d/{contract}-1d-{ym}.zip"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            raw = r.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]), header=None, names=COLS)
            if isinstance(df["open_time"].iloc[0], str) and not str(
                    df["open_time"].iloc[0]).isdigit():
                df = df.iloc[1:]
        df = df[["open_time", "close"]].apply(pd.to_numeric, errors="coerce").dropna()
        return df
    except Exception:
        return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    contracts = list_contracts()
    print(f"{len(contracts)} contrats trimestriels trouvés")
    reg = {}
    for c in contracts:
        exp, months = months_of(c)
        pq = OUT / f"{c}_1d.parquet"
        reg[c] = {"expiry": str(exp), "symbol": c.split("_")[0]}
        if pq.exists():
            continue
        frames = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            for fut in as_completed({ex.submit(fetch, c, m): m for m in months}):
                df = fut.result()
                if df is not None:
                    frames.append(df)
        if frames:
            allf = (pd.concat(frames).drop_duplicates("open_time")
                    .sort_values("open_time").reset_index(drop=True))
            allf["date"] = pd.to_datetime(allf["open_time"], unit="ms", utc=True)
            allf.to_parquet(pq, index=False)
            print(f"  {c}: {len(allf)} jours", flush=True)
    (OUT / "contracts.json").write_text(json.dumps(reg, indent=2))
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
