#!/usr/bin/env python3
"""
scripts/fetch_cme_basis_inputs.py -- les trois entrees du test CME_SEGMENTATION_V1.

NE S'EXECUTE QU'APRES le scellement du pre-enregistrement (etape 5 du protocole) :
un chargement anterieur detruirait la propriete qui rend le test valide -- la
donnee n'etait pas sur disque, le regard etait physiquement impossible.

  1. CME Bitcoin futures, front-month continu, quotidien : Yahoo BTC=F (chart API,
     period1 explicite -- range=max echantillonne en hebdomadaire).
  2. Binance SPOT BTCUSDT, klines 1h (Vision, mensuel) -- la jambe spot du basis.
  3. Binance USDT-M perp BTCUSDT, klines 1h (Vision, mensuel) -- l'instrument trade.

Sorties : data/cme_backfill/*.parquet + MANIFEST.json (sha256, URL, horodatage).
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cme_backfill"
UA = "Mozilla/5.0 (X11; Linux x86_64)"
KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
         "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def yahoo_btc_f() -> tuple[Path, str]:
    p1 = int(datetime(2017, 12, 1, tzinfo=timezone.utc).timestamp())
    p2 = int(time.time())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/BTC=F?interval=1d"
           f"&period1={p1}&period2={p2}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.load(r)["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    df = pd.DataFrame({"ts_utc": pd.to_datetime(d["timestamp"], unit="s", utc=True),
                       **{k: q[k] for k in ("open", "high", "low", "close", "volume")}})
    df = df.dropna(subset=["close"]).sort_values("ts_utc").reset_index(drop=True)
    out = OUT / "BTC_F_yahoo_daily.parquet"
    df.to_parquet(out, index=False)
    return out, url


def _months(y0, m0):
    today = date.today()
    y, m = y0, m0
    while (y, m) <= (today.year, today.month):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def vision_1h(market: str, sym: str, y0: int, m0: int) -> tuple[Path, list]:
    base = ("https://data.binance.vision/data/spot/monthly/klines" if market == "spot"
            else "https://data.binance.vision/data/futures/um/monthly/klines")
    frames, missing = [], []
    for ym in _months(y0, m0):
        url = f"{base}/{sym}/1h/{sym}-1h-{ym}.zip"
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                raw = r.read()
        except Exception:
            missing.append(ym)
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if isinstance(df.iloc[0, 0], str) and not str(df.iloc[0, 0]).isdigit():
            df = df.iloc[1:].reset_index(drop=True)
        df.columns = KCOLS[: len(df.columns)]
        frames.append(df[["open_time", "open", "high", "low", "close", "quote_volume"]])
    d = pd.concat(frames, ignore_index=True)
    for c in d.columns:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    ot = d["open_time"]
    ot = ot.where(ot < 1e14, ot / 1000.0)                      # us -> ms, LIGNE PAR LIGNE
    d["open_time"] = pd.to_datetime(ot, unit="ms", utc=True)
    d = d.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    out = OUT / f"{sym}_{market}_1h.parquet"
    d.to_parquet(out, index=False)
    return out, missing


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = datetime.now(timezone.utc).isoformat(timespec="seconds")
    man = {"fetched_at_utc": t0, "files": {}}
    p, url = yahoo_btc_f()
    man["files"][p.name] = {"sha256": _sha(p), "source": url, "rows": len(pd.read_parquet(p))}
    print(f"  {p.name}: {man['files'][p.name]['rows']} lignes", flush=True)
    for market, y0, m0 in (("spot", 2017, 8), ("um", 2020, 1)):
        p, miss = vision_1h(market, "BTCUSDT", y0, m0)
        man["files"][p.name] = {"sha256": _sha(p), "source": f"binance vision {market} 1h monthly",
                                "rows": len(pd.read_parquet(p)), "months_missing": miss}
        print(f"  {p.name}: {man['files'][p.name]['rows']} lignes, manquants {miss}", flush=True)
    (OUT / "MANIFEST.json").write_text(json.dumps(man, indent=2))
    print(f"-> {OUT / 'MANIFEST.json'}")


if __name__ == "__main__":
    main()
