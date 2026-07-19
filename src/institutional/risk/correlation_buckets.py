"""
src/institutional/risk/correlation_buckets.py
─────────────────────────────────────────────────────────────────────────────
Buckets de corrélation (univers 50) — empêche de prendre N positions qui sont
en réalité le même trade. Max 2 positions/bucket, max 1 meme, etc.
"""
from __future__ import annotations

from typing import Dict, List

CORRELATION_BUCKETS: Dict[str, List[str]] = {
    "majors": ["BTCUSDT", "ETHUSDT"],
    "sol_beta": ["SOLUSDT", "JUPUSDT", "PYTHUSDT", "WIFUSDT"],
    "eth_l2": ["OPUSDT", "ARBUSDT", "IMXUSDT", "LDOUSDT"],
    "ai": ["FETUSDT", "RNDRUSDT", "TAOUSDT", "WLDUSDT"],
    "defi": ["AAVEUSDT", "MKRUSDT", "UNIUSDT", "PENDLEUSDT", "ENAUSDT"],
    "memes": ["DOGEUSDT", "PEPEUSDT", "WIFUSDT"],
    "legacy_large": ["XRPUSDT", "ADAUSDT", "LTCUSDT", "BCHUSDT", "ETCUSDT", "TRXUSDT", "DOTUSDT"],
    "infra": ["AVAXUSDT", "LINKUSDT", "NEARUSDT", "ATOMUSDT", "ICPUSDT", "HBARUSDT",
              "VETUSDT", "FILUSDT", "ALGOUSDT", "GRTUSDT", "RUNEUSDT", "ARUSDT",
              "INJUSDT", "APTUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT"],
    "metaverse": ["SANDUSDT", "MANAUSDT"],
    "bitcoin_beta": ["ORDIUSDT", "STXUSDT"],
}

_ASSET_TO_BUCKET = {a: b for b, assets in CORRELATION_BUCKETS.items() for a in assets}


def bucket_of(symbol: str) -> str:
    return _ASSET_TO_BUCKET.get(symbol, "other")


def is_meme(symbol: str) -> bool:
    return symbol in CORRELATION_BUCKETS["memes"]
