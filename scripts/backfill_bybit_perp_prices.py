#!/usr/bin/env python3
"""
scripts/backfill_bybit_perp_prices.py
─────────────────────────────────────────────────────────────────────────────
Backfill GRATUIT des klines Bybit USDT-perpetual (category=linear) via
l'endpoint public v5 /v5/market/kline (aucune auth requise) — comble le gap
bloquant identifié dans
research/edge_factory/funding_relative_value_cross_venue/DATA_INVENTORY.yaml
(step 3) : le funding Bybit est propre 2022-11-03 → 2026-06-28, mais AUCUN
prix perp Bybit n'existait sur disque, ce qui empêchait de simuler les deux
jambes du carry cross-venue (funding reçu/payé net d'exécution).

Résolution : horaire (interval=60 minutes). Suffisant pour reconstruire le
PnL de spread de funding réalisé (cadence de règlement funding = 8h) sans
faire exploser la taille sur ~4-6 ans x 4 actifs.

Pagination — vérifié empiriquement avant d'écrire ce script :
  - `limit` est plafonné à 1000 bougies par requête, SANS erreur si on
    demande plus (silently clamped : demander 5000 renvoie 1000). Donc ne
    jamais supposer qu'un run "one-shot" a tout pris — toujours paginer.
  - Avec `start` seul (sans `end`), l'API renvoie la fenêtre de `limit`
    bougies la PLUS ANCIENNE disponible à partir de `start` (ou la borne
    réelle de listing si `start` est antérieur), triée décroissante dans le
    tableau JSON (index 0 = la plus récente de la page, index -1 = la plus
    ancienne). On avance donc `cursor = ts(page[0]) + interval_ms` à chaque
    page, jusqu'à une page de taille < `limit` (bord "maintenant").
  - Contiguïté vérifiée (pas de trou entre pages consécutives).
  - La date de listing réelle par symbole n'est PAS devinée : elle est lue
    depuis la première page retournée par l'API (peut être bien après
    --start si le perp n'existait pas encore à cette date).

Idempotent : si le parquet existe déjà, reprend depuis le dernier timestamp
+ 1 intervalle plutôt que de tout retélécharger.

Rate limiting : endpoint public Bybit, limites IP modestes mais non
négligeables — 0.25s entre pages + backoff exponentiel sur erreur transitoire.

Sortie : data/derivatives_backfill/bybit/perp_klines_1h/<SYMBOL>.parquet

    .venv/bin/python scripts/backfill_bybit_perp_prices.py --start 2019-01-01
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

API = "https://api.bybit.com/v5/market/kline"
OUT = ROOT / "data" / "derivatives_backfill" / "bybit" / "perp_klines_1h"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
INTERVAL = "60"           # minutes -> bougies horaires
INTERVAL_MS = 3_600_000
LIMIT = 1000              # max documenté /v5/market/kline (clamp silencieux au-delà)
SLEEP_S = 0.25            # citoyen correct de l'API publique
KCOLS = ["start", "open", "high", "low", "close", "volume", "turnover"]


def _get(url: str, tries: int = 5) -> dict:
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))


def fetch_page(symbol: str, start_ms: int) -> list:
    url = (f"{API}?category=linear&symbol={symbol}&interval={INTERVAL}"
           f"&start={start_ms}&limit={LIMIT}")
    d = _get(url)
    if d.get("retCode") != 0:
        raise RuntimeError(f"retCode={d.get('retCode')} {d.get('retMsg')}")
    return d.get("result", {}).get("list", [])


def backfill(symbol: str, start_ms: int) -> pd.DataFrame:
    now_ms = int(time.time() * 1000)
    cursor = start_ms
    rows: list = []
    while cursor < now_ms:
        try:
            page = fetch_page(symbol, cursor)
        except Exception as e:
            print(f"  {symbol}: arrêt anticipé à {cursor} ({repr(e)[:70]}) "
                  f"— {len(rows)} bougies déjà collectées seront écrites, "
                  f"reprise possible au prochain run", flush=True)
            break
        if not page:
            break
        rows.extend(page)
        newest_ts = int(page[0][0])          # page triée décroissante -> [0] = plus récente
        next_cursor = newest_ts + INTERVAL_MS
        if next_cursor <= cursor:            # garde-fou anti-boucle infinie
            break
        cursor = next_cursor
        if len(page) < LIMIT:                # dernière page (bord "maintenant")
            break
        time.sleep(SLEEP_S)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=KCOLS).drop_duplicates("start")
    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["start"], errors="coerce"),
                                      unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return (df.dropna(subset=["timestamp", "close"])
              [["timestamp", "open", "high", "low", "close", "volume", "turnover"]]
              .sort_values("timestamp").reset_index(drop=True))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01",
                    help="borne basse demandée ; le premier point RÉEL par symbole "
                         "peut être bien plus tardif (date de listing du perp) — "
                         "jamais deviné, lu depuis la première page renvoyée par l'API")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    default_start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]

    print(f"{'Symbol':<10}{'rows':>10}  range")
    print("-" * 60)
    for sym in syms:
        pq = OUT / f"{sym}.parquet"
        old = pd.read_parquet(pq) if pq.exists() else None
        start_ms = default_start_ms
        if old is not None and len(old):
            start_ms = int(old["timestamp"].max().timestamp() * 1000) + INTERVAL_MS

        new = backfill(sym, start_ms)

        if old is not None and len(old):
            combined = (pd.concat([old, new], ignore_index=True)
                        .drop_duplicates("timestamp")
                        .sort_values("timestamp").reset_index(drop=True))
        else:
            combined = new

        if not len(combined):
            print(f"{sym:<10}  rien (échec ou pas de nouvelle donnée)")
            continue

        atomic_write_parquet(combined, pq)
        span = (f"{combined['timestamp'].min()} -> {combined['timestamp'].max()}")
        print(f"{sym:<10}{len(combined):>10,}  {span}")

    print(f"\n-> {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
