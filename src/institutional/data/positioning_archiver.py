"""
src/institutional/data/positioning_archiver.py
─────────────────────────────────────────────────────────────────────────────
Archiveur du positionnement Binance USD-M (top traders / global / taker).

Pourquoi : les 4 endpoints fapi `/futures/data/*Ratio` ne conservent que
30 JOURS d'historique. Sans archivage récurrent, cette donnée est perdue.
Les dumps Vision `metrics` couvrent les mêmes ratios à 5 min mais avec un
retard J-2 et sans garantie de pérennité (le dossier um liquidationSnapshot
a déjà été retiré de Vision) ; l'archiveur capture donc la fenêtre J-2 → now
et sert d'assurance contre une disparition du flux Vision.

Endpoints archivés (period 5m, limit 500 ≈ 41,6 h par appel) :
  top_position  /futures/data/topLongShortPositionRatio   (top 20 % comptes, pondéré positions)
  top_account   /futures/data/topLongShortAccountRatio    (top 20 % comptes, comptage comptes)
  global_account /futures/data/globalLongShortAccountRatio (tous comptes)
  taker_vol     /futures/data/takerlongshortRatio          (volume taker buy/sell)

Stockage : data/positioning/{SYM}_{endpoint}.parquet — append + dedup sur
timestamp via atomic_parquet (jamais d'écriture directe).

Tolérance aux pannes : à cadence 6 h, un trou n'apparaît que si le timer
échoue > 41 h d'affilée ; la rétention API de 30 j permet de rattraper
manuellement avec period=1h (720 points = 30 j) si besoin.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT_DIR = ROOT / "data" / "positioning"

FAPI = "https://fapi.binance.com/futures/data"

# nom court → (chemin API, colonnes numériques attendues)
ENDPOINTS: Dict[str, tuple] = {
    "top_position": ("topLongShortPositionRatio",
                     ["longAccount", "shortAccount", "longShortRatio"]),
    "top_account": ("topLongShortAccountRatio",
                    ["longAccount", "shortAccount", "longShortRatio"]),
    "global_account": ("globalLongShortAccountRatio",
                       ["longAccount", "shortAccount", "longShortRatio"]),
    "taker_vol": ("takerlongshortRatio",
                  ["buySellRatio", "buyVol", "sellVol"]),
}

# univers du collecteur dérivés (futur-derivatives.service)
UNIVERSE_50: List[str] = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "DOTUSDT",
    "NEARUSDT", "OPUSDT", "ARBUSDT", "INJUSDT", "ATOMUSDT", "APTUSDT",
    "SUIUSDT", "FILUSDT", "TRXUSDT", "ETCUSDT", "UNIUSDT", "AAVEUSDT",
    "MKRUSDT", "RNDRUSDT", "FETUSDT", "TAOUSDT", "SEIUSDT", "TIAUSDT",
    "WIFUSDT", "PEPEUSDT", "ORDIUSDT", "STXUSDT", "IMXUSDT", "GRTUSDT",
    "RUNEUSDT", "ARUSDT", "JUPUSDT", "PYTHUSDT", "ENAUSDT", "PENDLEUSDT",
    "LDOUSDT", "WLDUSDT", "ALGOUSDT", "ICPUSDT", "HBARUSDT", "VETUSDT",
    "SANDUSDT", "MANAUSDT",
]


def fetch_endpoint(symbol: str, endpoint: str, period: str = "5m",
                   limit: int = 500, timeout: int = 20) -> List[dict]:
    """GET brut d'un endpoint positioning. Lève en cas d'erreur réseau/HTTP."""
    path, _ = ENDPOINTS[endpoint]
    qs = urllib.parse.urlencode(
        {"symbol": symbol, "period": period, "limit": limit})
    url = f"{FAPI}/{path}?{qs}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def normalize(payload: List[dict], endpoint: str, symbol: str,
              period: str) -> pd.DataFrame:
    """Payload API → DataFrame typé (timestamp UTC, colonnes numériques)."""
    _, num_cols = ENDPOINTS[endpoint]
    df = pd.DataFrame(payload)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(
        pd.to_numeric(df["timestamp"]), unit="ms", utc=True)
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["symbol"] = symbol
    df["period"] = period
    keep = ["timestamp", "symbol", "period"] + [c for c in num_cols
                                                if c in df.columns]
    return df[keep].sort_values("timestamp").reset_index(drop=True)


def merge_archive(old: Optional[pd.DataFrame],
                  new: pd.DataFrame) -> pd.DataFrame:
    """Append + dedup (timestamp, period) en gardant la version la plus récente."""
    if old is not None and len(old):
        out = pd.concat([old, new], ignore_index=True)
    else:
        out = new.copy()
    return (out.drop_duplicates(subset=["timestamp", "period"], keep="last")
               .sort_values("timestamp").reset_index(drop=True))


def archive_symbol(symbol: str, out_dir: Path = DEFAULT_OUT_DIR,
                   period: str = "5m", limit: int = 500) -> Dict[str, dict]:
    """Archive les 4 endpoints pour un symbole. Une erreur n'arrête pas les autres."""
    from src.institutional.data.atomic_parquet import append_enriched_atomic

    out_dir.mkdir(parents=True, exist_ok=True)
    stats: Dict[str, dict] = {}
    for ep in ENDPOINTS:
        pq = out_dir / f"{symbol}_{ep}.parquet"
        try:
            payload = fetch_endpoint(symbol, ep, period=period, limit=limit)
            new = normalize(payload, ep, symbol, period)
            if new.empty:
                stats[ep] = {"status": "empty", "rows_fetched": 0}
                continue
            total = append_enriched_atomic(
                pq, new, timestamp_col="timestamp",
                dedupe_cols=("timestamp", "period"))
            stats[ep] = {"status": "ok", "rows_fetched": len(new),
                         "rows_total": int(total),
                         "last_ts": new["timestamp"].iloc[-1].isoformat()}
        except Exception as e:                       # noqa: BLE001
            stats[ep] = {"status": f"err_{type(e).__name__}", "rows_fetched": 0}
    return stats
