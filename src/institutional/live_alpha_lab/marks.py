"""
src/institutional/live_alpha_lab/marks.py
─────────────────────────────────────────────────────────────────────────────
Source de prix EXPLICITE par instrument pour le mark-to-market — jamais un
fallback silencieux (instruction utilisateur, phase ECONOMIC TRUTH, item 3).

Trois familles d'instruments, trois sources, chacune tracée :
  - perp du frozen-50 (déjà dans data/derivatives_raw, collecteur live
    ~5min) : mark_price le plus récent, source="DERIVATIVES_RAW_MARK".
  - perp hors frozen-50 (univers large CROSS_SECTIONAL_MOMENTUM_LIVE_V2) :
    appel REST direct Binance bookTicker (bid/ask), source="REST_BOOKTICKER_MID".
  - "<SYM>_QUARTERLY" (FUNDING_BASIS_DISAGREEMENT_V2) : dernier close connu
    du contrat trimestriel (cadence quotidienne, structurellement plus âgé),
    source="QUARTERLY_DAILY_CLOSE".
  - "<SYM>_PERP" (jambe perp de FUNDING_BASIS_DISAGREEMENT_V2) : même
    résolution que les perps normaux.

Chaque MarkQuote porte mark_source/mark_timestamp/mark_age_ms. Un
consommateur DOIT vérifier `is_stale()` avant de faire confiance au prix
pour une métrique "live valid" -- ne jamais l'ignorer silencieusement.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DERIVATIVES_RAW = ROOT / "data" / "derivatives_raw"
QUARTERLY_DIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_quarterly"

# Seuils de fraîcheur PAR SOURCE -- une source quotidienne n'est pas "cassée"
# juste parce qu'elle a >15min, c'est sa cadence normale. Documenté, pas une
# tolérance générique unique qui masquerait la vraie nature de la source.
STALE_MS_BY_SOURCE = {
    "DERIVATIVES_RAW_MARK": 15 * 60 * 1000,      # collecteur ~5min, marge x3
    "REST_BOOKTICKER_MID": 5 * 60 * 1000,        # appel live -- stale seulement si le cache est vieux
    "QUARTERLY_DAILY_CLOSE": 36 * 60 * 60 * 1000,  # cadence quotidienne + lag Vision ~2j -- 36h de marge
}

_rest_cache: Dict[str, "MarkQuote"] = {}


@dataclass(frozen=True)
class MarkQuote:
    instrument: str
    price: float
    mark_source: str
    mark_timestamp: pd.Timestamp
    mark_age_ms: float

    def is_stale(self) -> bool:
        threshold = STALE_MS_BY_SOURCE.get(self.mark_source, 15 * 60 * 1000)
        return self.mark_age_ms > threshold


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _from_derivatives_raw(symbol: str, as_of: pd.Timestamp) -> Optional[MarkQuote]:
    base = DERIVATIVES_RAW / "exchange=binance" / "market=usdm" / "stream=open_interest" / f"symbol={symbol}"
    if not base.exists():
        return None
    files = sorted(base.glob("date=*/part-*.parquet"))
    if not files:
        return None
    # les 2 derniers jours suffisent pour trouver le dernier mark connu <= as_of
    recent = files[-4:]
    frames = []
    for f in recent:
        try:
            frames.append(pd.read_parquet(f, columns=["timestamp", "mark_price"]))
        except Exception:
            continue
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    # `timestamp` est en epoch MILLISECONDES (entier) dans derivatives_raw --
    # PAS déjà un datetime. unit="ms" explicite, jamais laisser pandas deviner
    # (par défaut il suppose des nanosecondes pour un entier brut -> donnerait
    # une date en 1970, silencieusement fausse).
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df[df["timestamp"] <= as_of].sort_values("timestamp")
    if df.empty:
        return None
    last = df.iloc[-1]
    ts = pd.Timestamp(last["timestamp"])
    return MarkQuote(
        instrument=symbol, price=float(last["mark_price"]), mark_source="DERIVATIVES_RAW_MARK",
        mark_timestamp=ts, mark_age_ms=(as_of - ts).total_seconds() * 1000,
    )


def _from_rest_bookticker(symbol: str, as_of: pd.Timestamp) -> Optional[MarkQuote]:
    cached = _rest_cache.get(symbol)
    if cached is not None and (as_of - cached.mark_timestamp).total_seconds() * 1000 < 30_000:
        return cached   # cache 30s intra-run -- pas re-frapper l'API pour chaque instrument du même run
    try:
        req = urllib.request.Request(
            f"https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol={symbol}",
            headers={"User-Agent": "futur-mtm-marks"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        bid, ask = float(data["bidPrice"]), float(data["askPrice"])
        if bid <= 0 or ask <= 0:
            return None
        mid = (bid + ask) / 2.0
        q = MarkQuote(instrument=symbol, price=mid, mark_source="REST_BOOKTICKER_MID",
                     mark_timestamp=as_of, mark_age_ms=0.0)
        _rest_cache[symbol] = q
        return q
    except Exception:
        return None


def _from_quarterly_close(symbol_quarterly: str, as_of: pd.Timestamp) -> Optional[MarkQuote]:
    base_symbol = symbol_quarterly.replace("_QUARTERLY", "")
    candidates = sorted(QUARTERLY_DIR.glob(f"{base_symbol}_*_1d.parquet"))
    if not candidates:
        return None
    best = None
    for f in candidates:
        try:
            df = pd.read_parquet(f, columns=["date", "close"])
        except Exception:
            continue
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df[df["date"] <= as_of].sort_values("date")
        if df.empty:
            continue
        last = df.iloc[-1]
        ts = pd.Timestamp(last["date"])
        if best is None or ts > best[0]:
            best = (ts, float(last["close"]))
    if best is None:
        return None
    ts, price = best
    return MarkQuote(
        instrument=symbol_quarterly, price=price, mark_source="QUARTERLY_DAILY_CLOSE",
        mark_timestamp=ts, mark_age_ms=(as_of - ts).total_seconds() * 1000,
    )


def get_mark(instrument: str, as_of: Optional[pd.Timestamp] = None) -> Optional[MarkQuote]:
    """Jamais de fallback silencieux : retourne None explicitement si aucune
    source ne répond, plutôt qu'un prix inventé/périmé sans le dire."""
    as_of = as_of or _now()

    if instrument.endswith("_QUARTERLY"):
        return _from_quarterly_close(instrument, as_of)

    symbol = instrument.replace("_PERP", "")
    q = _from_derivatives_raw(symbol, as_of)
    if q is not None:
        return q
    return _from_rest_bookticker(symbol, as_of)
