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
    # Proxy de liquidité (item P0.2, plafond de fill partiel) : notional de
    # l'open interest courant (open_interest * mark_price), disponible
    # UNIQUEMENT pour DERIVATIVES_RAW_MARK (la seule source qui porte
    # open_interest). None pour REST_BOOKTICKER_MID/QUARTERLY_DAILY_CLOSE
    # -- fail-open (pas de plafond appliqué) plutôt qu'une valeur inventée.
    liquidity_notional: Optional[float] = None

    def is_stale(self) -> bool:
        threshold = STALE_MS_BY_SOURCE.get(self.mark_source, 15 * 60 * 1000)
        return self.mark_age_ms > threshold


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _oi_base(symbol: str) -> Path:
    return DERIVATIVES_RAW / "exchange=binance" / "market=usdm" / "stream=open_interest" / f"symbol={symbol}"


def eligible_files_for_as_of(base: Path, as_of: pd.Timestamp) -> list:
    """Le collecteur écrit UN fichier par poll (~450-500 fichiers/jour/
    symbole), pas un fichier par jour : prendre "les N derniers fichiers" du
    glob trié ne couvre en pratique que les ~20 dernières minutes, quel que
    soit as_of. Pour un as_of historique (pas "maintenant"), ça retournait
    silencieusement None/vide (root cause de la divergence P1_EQUAL_RISK vs
    P1_CONTROL sur les marks, cf compare_portfolios.py, et sous-comptage
    silencieux du funding pour tout as_of historique). Fix partagé : ne
    garder que les partitions date= <= as_of.date() (les 2 dernières, pour
    couvrir le cas où as_of tombe tôt dans sa journée), lire TOUS les
    fichiers de ces partitions."""
    if not base.exists():
        return []
    date_dirs = sorted(d for d in base.glob("date=*") if d.is_dir())
    as_of_date_str = as_of.strftime("%Y-%m-%d")
    eligible_dirs = [d for d in date_dirs if d.name.split("=", 1)[1] <= as_of_date_str]
    if not eligible_dirs:
        return []
    recent_dirs = eligible_dirs[-2:]
    files = []
    for d in recent_dirs:
        files.extend(sorted(d.glob("part-*.parquet")))
    return files


def _from_derivatives_raw(symbol: str, as_of: pd.Timestamp) -> Optional[MarkQuote]:
    base = _oi_base(symbol)
    files = eligible_files_for_as_of(base, as_of)
    if not files:
        return None
    frames = []
    for f in files:
        try:
            frames.append(pd.read_parquet(f, columns=["timestamp", "mark_price", "open_interest"]))
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
    price = float(last["mark_price"])
    oi = last.get("open_interest")
    liquidity_notional = float(oi) * price if pd.notna(oi) and oi > 0 else None
    return MarkQuote(
        instrument=symbol, price=price, mark_source="DERIVATIVES_RAW_MARK",
        mark_timestamp=ts, mark_age_ms=(as_of - ts).total_seconds() * 1000,
        liquidity_notional=liquidity_notional,
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
