#!/usr/bin/env python3
"""
scripts/backfill_deribit_option_trades.py
─────────────────────────────────────────────────────────────────────────────
Backfill GRATUIT des trades options Deribit (history.deribit.com, public).

Pourquoi : l'historique skew/OI-par-strike est payant partout, MAIS chaque
trade public porte iv, strike, expiry, direction, taille, index_price — on
peut donc reconstruire des features de positionnement (skew tradé, put/call
flow, strikes concentrés, blocs) depuis 2019. C'est la couche data du futur
moteur OPTIONS_POSITIONING (VRP a échoué ; ceci n'est pas du VRP).

Pagination asc par fenêtre temporelle, dédup par trade_id, chunks mensuels
idempotents : data/options_backfill/deribit/trades/<CUR>/<YYYY-MM>.parquet
(un mois déjà présent n'est pas re-téléchargé, sauf --force).

    .venv/bin/python scripts/backfill_deribit_option_trades.py \
        --currency BTC --start 2026-01 --end 2026-07
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.data.atomic_parquet import atomic_write_parquet

API = "https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time"
OUT = ROOT / "data" / "options_backfill" / "deribit" / "trades"

KEEP = ["trade_id", "timestamp", "instrument_name", "price", "mark_price",
        "iv", "index_price", "direction", "amount", "liquidation", "block_trade_id"]


def _get(url: str, tries: int = 5):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3.0 * (k + 1))


def parse_instrument(name: str) -> tuple[pd.Timestamp | None, float, str]:
    """BTC-30JUN23-8000-P → (expiry, strike, 'P')."""
    try:
        _, exp, strike, cp = name.split("-")
        return (pd.Timestamp(exp, tz="UTC") + pd.Timedelta(hours=8),  # settle 08:00 UTC
                float(strike), cp)
    except Exception:
        return None, float("nan"), "?"


def fetch_month(currency: str, month: pd.Period) -> pd.DataFrame:
    start_ms = int(month.start_time.tz_localize("UTC").timestamp() * 1000)
    end_ms = int((month + 1).start_time.tz_localize("UTC").timestamp() * 1000)
    end_ms = min(end_ms, int(time.time() * 1000))
    rows, cursor, n_req = [], start_ms, 0
    while cursor < end_ms:
        d = _get(f"{API}?currency={currency}&kind=option&start_timestamp={cursor}"
                 f"&end_timestamp={end_ms}&count=1000&sorting=asc")
        res = d.get("result", {})
        trades = res.get("trades", [])
        if not trades:
            break
        rows.extend(trades)
        n_req += 1
        last = trades[-1]["timestamp"]
        if not res.get("has_more") or last <= cursor - 1:
            break
        cursor = last  # inclusif : chevauchement volontaire, dédup par trade_id
        if trades[0]["timestamp"] == last and len(trades) == 1000:
            cursor = last + 1  # 1000 trades sur la même ms (théorique) : avancer
        time.sleep(0.06)
        if n_req % 200 == 0:
            print(f"    …{n_req} req, {len(rows)} trades, cursor={pd.Timestamp(last, unit='ms', tz='UTC')}")
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in KEEP:
        if c not in df.columns:
            df[c] = None
    df = df[KEEP].drop_duplicates("trade_id")
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    parsed = df["instrument_name"].map(parse_instrument)
    df["expiry"] = [p[0] for p in parsed]
    df["strike"] = [p[1] for p in parsed]
    df["cp"] = [p[2] for p in parsed]
    df["is_block"] = df["block_trade_id"].notna()
    df = df.drop(columns=["block_trade_id"])
    return df.sort_values("timestamp").reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    ap.add_argument("--start", default="2026-01", help="YYYY-MM inclus")
    ap.add_argument("--end", default=None, help="YYYY-MM inclus (défaut: mois courant)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    end = pd.Period(args.end, "M") if args.end else pd.Timestamp.utcnow().to_period("M")
    months = pd.period_range(pd.Period(args.start, "M"), end, freq="M")

    cur_month = pd.Timestamp.utcnow().to_period("M")
    total = 0
    for m in months:
        path = OUT / args.currency / f"{m}.parquet"
        # un mois clos déjà présent est final ; le mois courant est toujours rafraîchi
        if path.exists() and not args.force and m < cur_month:
            print(f"  {m} déjà présent — skip")
            continue
        t0 = time.time()
        df = fetch_month(args.currency, m)
        if not len(df):
            print(f"  {m} : 0 trade (rien écrit)")
            continue
        atomic_write_parquet(df, path)
        total += len(df)
        print(f"  {m} : {len(df):,} trades, iv médiane {df['iv'].median():.1f}, "
              f"{df['is_block'].mean()*100:.1f}% blocs ({time.time()-t0:.0f}s)")
    print(f"\nDERIBIT_TRADES_BACKFILL {args.currency} : {total:,} trades "
          f"→ {OUT.relative_to(ROOT)}/{args.currency}/")


if __name__ == "__main__":
    main()
