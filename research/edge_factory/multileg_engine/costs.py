"""
research/edge_factory/multileg_engine/costs.py — CostModel (interface 3/5).

Ne recalcule rien qui existe déjà : fee() et borrow() enveloppent
src.alpha20.costs.fee_registry.effective_costs() et
src.alpha20.costs.borrow_registry.effective_borrow() (réel si un snapshot
existe, sinon défaut "assumed" étiqueté — jamais une constante silencieuse).
funding_lookup() est la seule pièce réellement neuve : aucun registre existant
ne lit le funding RÉEL archivé (data/derivatives_backfill/<venue>/funding/).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from src.alpha20 import ROOT
from src.alpha20.contracts import CostSnapshot
from src.alpha20.costs.borrow_registry import effective_borrow
from src.alpha20.costs.fee_registry import effective_costs

FUNDING_DIR = ROOT / "data" / "derivatives_backfill"

# venue court (répertoires data/, Instrument.venue) -> clé fee_registry/configs
FEE_REGISTRY_VENUE = {
    "binance": "binance_usdm",
    "hyperliquid": "hyperliquid",
}

# Bybit ajouté à configs/alpha20.yaml assumed_defaults dans ce dépôt (repris tel
# quel de reports/FUNDING_XVENUE_PROTOCOL.md, fee_bybit_bp=5.5), mais on ne
# dépend PAS de ce fichier étant à jour sur la machine d'exécution : c'est un
# fichier de config partagé potentiellement lu par des process live (tournoi
# ALPHA_20) — on ne l'écrase jamais depuis un script de recherche. Fallback
# local explicite, même valeur, même étiquette "assumed".
_BYBIT_ASSUMED = CostSnapshot(venue="bybit", instrument="BTCUSDT", maker_bp=2.0,
                              taker_bp=5.5, as_of="2026-07-21", source="assumed")

# nommage de fichier funding par venue : binance/bybit suffixent USDT, HL non
_FUNDING_FILENAME = {
    "binance": lambda symbol: f"{symbol}USDT.parquet",
    "bybit": lambda symbol: f"{symbol}USDT.parquet",
    "hyperliquid": lambda symbol: f"{symbol}.parquet",
}

_funding_cache: Dict[str, Optional[pd.DataFrame]] = {}


def _load_funding(venue: str, symbol: str) -> Optional[pd.DataFrame]:
    key = f"{venue}:{symbol}"
    if key in _funding_cache:
        return _funding_cache[key]
    namer = _FUNDING_FILENAME.get(venue)
    df = None
    if namer is not None:
        path = FUNDING_DIR / venue / "funding" / namer(symbol)
        if path.exists():
            df = pd.read_parquet(path)
    _funding_cache[key] = df
    return df


def funding_lookup(venue: str, symbol: str, ts: datetime) -> Dict:
    """Taux de funding réel le plus récent <= ts. Défaut assumed=0 étiqueté
    explicitement si rien n'est archivé — jamais un silence qui ressemblerait
    à un vrai zéro de funding."""
    df = _load_funding(venue, symbol)
    if df is not None and len(df):
        eligible = df[pd.to_datetime(df["timestamp"]) <= pd.Timestamp(ts, tz="UTC")]
        if len(eligible):
            row = eligible.sort_values("timestamp").iloc[-1]
            return {"venue": venue, "symbol": symbol,
                    "rate": float(row["funding_rate"]),
                    "ts": str(row["timestamp"]), "source": "archived_real"}
    return {"venue": venue, "symbol": symbol, "rate": 0.0,
            "ts": ts.isoformat(), "source": "assumed_missing"}


def fee(venue: str, instrument: str) -> CostSnapshot:
    if venue == "bybit":
        return _BYBIT_ASSUMED
    return effective_costs(FEE_REGISTRY_VENUE.get(venue, venue), instrument)


def borrow(venue: str, asset: str) -> Dict:
    return effective_borrow(venue, asset)
