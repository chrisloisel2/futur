#!/usr/bin/env python3
"""
vision_paths.py -- fonctions PURES : quels fichiers Binance Vision couvrent une fenetre d'evenement.

Fenetre = t0 - pre -> t0 + post (defaut 30 min / 6 h). Un fichier journalier couvre un jour UTC ;
fundingRate est mensuel. Aucune requete ici : uniquement des URL, des chemins locaux et des jours.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
VISION = "https://data.binance.vision/data"
LOCAL_ROOT = ROOT / "data" / "vision_backfill"          # data/* est gitignore
PRE_S, POST_S = 30 * 60, 6 * 3600

# dataset -> (segment, kind, priorite, role)
DATASETS: Dict[str, Dict[str, str]] = {
    "markPriceKlines":    {"kind": "klines", "priority": "P0", "role": "first mark price, mark path (reference)"},
    "indexPriceKlines":   {"kind": "klines", "priority": "P0", "role": "first index price, external reference"},
    "premiumIndexKlines": {"kind": "klines", "priority": "P0", "role": "premium = mark - index (overshoot vs outside)"},
    "aggTrades":          {"kind": "daily",  "priority": "P0", "role": "first trade, trades tick-by-tick, entry/exit prints"},
    "metrics":            {"kind": "daily",  "priority": "P1", "role": "open interest 5-min, L/S ratios"},
    "fundingRate":        {"kind": "monthly", "priority": "P1", "role": "funding at first settlements"},
    "bookDepth":          {"kind": "daily",  "priority": "P1", "role": "1-min depth at % levels (capacity, slippage)"},
    "klines":             {"kind": "klines", "priority": "P2", "role": "1-min OHLCV: fallback / outcome ruler only"},
    "trades":             {"kind": "daily",  "priority": "P2", "role": "raw trades (aggTrades suffice; optional)"},
    "bookTicker":         {"kind": "daily",  "priority": "P2", "role": "tick BBO (absent for new symbols on Vision)"},
}
DEFAULT_SET = ["markPriceKlines", "indexPriceKlines", "premiumIndexKlines", "aggTrades", "metrics", "fundingRate", "bookDepth", "klines"]


def parse_ts(ts: str) -> datetime:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def window(t0: str, pre_s: int = PRE_S, post_s: int = POST_S) -> Dict[str, str]:
    t = parse_ts(t0).astimezone(timezone.utc)     # les fichiers Vision sont decoupes en jours UTC
    return {"t0": t.isoformat(timespec="seconds"), "start": (t - timedelta(seconds=pre_s)).isoformat(timespec="seconds"), "end": (t + timedelta(seconds=post_s)).isoformat(timespec="seconds")}


def days_for_window(t0: str, pre_s: int = PRE_S, post_s: int = POST_S) -> List[str]:
    t = parse_ts(t0).astimezone(timezone.utc); a = (t - timedelta(seconds=pre_s)).date(); b = (t + timedelta(seconds=post_s)).date()
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def months_for_window(t0: str, pre_s: int = PRE_S, post_s: int = POST_S) -> List[str]:
    return sorted({d[:7] for d in days_for_window(t0, pre_s, post_s)})


def file_name(dataset: str, symbol: str, period: str) -> str:
    kind = DATASETS[dataset]["kind"]
    if kind == "klines":
        return f"{symbol}-1m-{period}.zip"
    return f"{symbol}-{dataset}-{period}.zip"


def url_for(dataset: str, symbol: str, period: str) -> str:
    kind = DATASETS[dataset]["kind"]
    if kind == "klines":
        return f"{VISION}/futures/um/daily/{dataset}/{symbol}/1m/{file_name(dataset, symbol, period)}"
    if kind == "monthly":
        return f"{VISION}/futures/um/monthly/{dataset}/{symbol}/{file_name(dataset, symbol, period)}"
    return f"{VISION}/futures/um/daily/{dataset}/{symbol}/{file_name(dataset, symbol, period)}"


def checksum_url(dataset: str, symbol: str, period: str) -> str:
    return url_for(dataset, symbol, period) + ".CHECKSUM"


def local_path(dataset: str, symbol: str, period: str, root=None) -> Path:
    """root resolu a l'appel : un test qui redirige LOCAL_ROOT n'ecrit jamais dans le vrai entrepot."""
    import data_lake.collectors.vision_paths as _self   # relecture du module : un test peut rediriger LOCAL_ROOT
    return Path(root if root is not None else _self.LOCAL_ROOT) / "um" / dataset / symbol / file_name(dataset, symbol, period)


def files_for_event(symbol: str, t0: str, datasets: List[str] = None, pre_s: int = PRE_S, post_s: int = POST_S, root=None) -> List[Dict[str, str]]:
    """Tous les fichiers (dataset, periode, url, chemin) qui couvrent la fenetre de l'evenement."""
    out = []
    for ds in (datasets or DEFAULT_SET):
        periods = months_for_window(t0, pre_s, post_s) if DATASETS[ds]["kind"] == "monthly" else days_for_window(t0, pre_s, post_s)
        for p in periods:
            out.append({"dataset": ds, "period": p, "url": url_for(ds, symbol, p), "checksum_url": checksum_url(ds, symbol, p), "local": str(local_path(ds, symbol, p, root)), "priority": DATASETS[ds]["priority"]})
    return out
