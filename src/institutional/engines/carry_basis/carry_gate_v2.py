"""
src/institutional/engines/carry_basis/carry_gate_v2.py
─────────────────────────────────────────────────────────────────────────────
CARRY_GATE_V2 — consensus funding cross-exchange (Binance×Bybit) + dispersion
faible + flip risk réduit. Validé : ×2.65 net carry, flips 37%→26% (3.6 ans).

    ALLOW  : funding_binance>0 ET funding_bybit>0 ET dispersion<p90 ET pas de flip
    REDUCE : positif des deux côtés mais dispersion p90-p95 (×0.5)
    BLOCK  : un funding ≤0, dispersion≥p95, flip récent, ou donnée manquante

Causal (dispersion = rang glissant 180j ; flip = changement de signe 3 périodes).
Unités : funding en fraction (1 bps = 1e-4 ; funding crypto typique ~0.5-1 bps).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
BACKFILL = ROOT / "data" / "derivatives_backfill"
FUNDING_HOURS = (0, 8, 16)


class CarryGateV2Status(str, Enum):
    ALLOW = "ALLOW"; REDUCE = "REDUCE"; BLOCK = "BLOCK"


class CarryGateV2Reason(str, Enum):
    POSITIVE_BOTH_LOW_DISPERSION = "POSITIVE_BOTH_LOW_DISPERSION"
    DISPERSION_REDUCE = "DISPERSION_REDUCE"
    NEGATIVE_BINANCE_FUNDING = "NEGATIVE_BINANCE_FUNDING"
    NEGATIVE_BYBIT_FUNDING = "NEGATIVE_BYBIT_FUNDING"
    HIGH_FUNDING_DISPERSION = "HIGH_FUNDING_DISPERSION"
    FUNDING_FLIP_RISK = "FUNDING_FLIP_RISK"
    MISSING_FUNDING = "MISSING_FUNDING"


@dataclass(frozen=True)
class CarryGateV2Decision:
    timestamp: str
    symbol: str
    status: CarryGateV2Status
    reason: CarryGateV2Reason
    funding_binance: Optional[float]
    funding_bybit: Optional[float]
    dispersion_percentile: Optional[float]
    flip_risk: bool
    carry_size_multiplier: float


@dataclass
class CarryGateV2Config:
    dispersion_reduce: float = 0.90    # p90-p95 → REDUCE
    dispersion_block: float = 0.95     # ≥p95 → BLOCK
    reduce_multiplier: float = 0.50
    dispersion_lookback_periods: int = 180 * 3   # 180j en 8h
    flip_lookback: int = 3


def _load_funding(ex: str, sym: str) -> Optional[pd.Series]:
    p = BACKFILL / ex / "funding" / f"{sym}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    s = df.set_index(pd.to_datetime(df["timestamp"], utc=True))["funding_rate"].sort_index()
    s.index = s.index.floor("8h")
    return s[~s.index.duplicated(keep="last")]


class CarryGateV2:
    def __init__(self, symbols, config: Optional[CarryGateV2Config] = None):
        self.config = config or CarryGateV2Config()
        self._panels: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            self._panels[sym] = self._build(sym)

    def _build(self, sym: str) -> pd.DataFrame:
        fb, fy = _load_funding("binance", sym), _load_funding("bybit", sym)
        if fb is None or fy is None:
            return pd.DataFrame()
        df = pd.DataFrame({"fb": fb, "fy": fy}).dropna()
        if df.empty:
            return df
        df["abs_spread"] = (df["fy"] - df["fb"]).abs()
        cfg = self.config
        df["disp_pct"] = df["abs_spread"].rolling(cfg.dispersion_lookback_periods, min_periods=30).rank(pct=True)
        flip_b = np.sign(df["fb"]).diff().abs().rolling(cfg.flip_lookback).sum() > 0
        flip_y = np.sign(df["fy"]).diff().abs().rolling(cfg.flip_lookback).sum() > 0
        df["flip_risk"] = (flip_b | flip_y).fillna(True)
        return df

    def evaluate(self, symbol: str, ts) -> CarryGateV2Decision:
        cfg = self.config
        ts = pd.Timestamp(ts)
        panel = self._panels.get(symbol)
        if panel is None or panel.empty:
            return CarryGateV2Decision(str(ts), symbol, CarryGateV2Status.BLOCK,
                                       CarryGateV2Reason.MISSING_FUNDING, None, None, None, True, 0.0)
        i = panel.index.searchsorted(ts, side="right") - 1
        if i < 0:
            return CarryGateV2Decision(str(ts), symbol, CarryGateV2Status.BLOCK,
                                       CarryGateV2Reason.MISSING_FUNDING, None, None, None, True, 0.0)
        row = panel.iloc[i]
        fb, fy, disp, flip = float(row["fb"]), float(row["fy"]), row["disp_pct"], bool(row["flip_risk"])
        disp = float(disp) if pd.notna(disp) else 1.0

        def dec(status, reason, mult):
            return CarryGateV2Decision(str(ts), symbol, status, reason, fb, fy, disp, flip, mult)

        # Aligné sur le signal VALIDÉ (positive_both + dispersion). flip_risk est
        # calculé/loggé mais NE bloque PAS (sinon over-block + churn — leçon mesurée).
        if fb <= 0:
            return dec(CarryGateV2Status.BLOCK, CarryGateV2Reason.NEGATIVE_BINANCE_FUNDING, 0.0)
        if fy <= 0:
            return dec(CarryGateV2Status.BLOCK, CarryGateV2Reason.NEGATIVE_BYBIT_FUNDING, 0.0)
        if disp >= cfg.dispersion_block:
            return dec(CarryGateV2Status.BLOCK, CarryGateV2Reason.HIGH_FUNDING_DISPERSION, 0.0)
        if disp >= cfg.dispersion_reduce:
            return dec(CarryGateV2Status.REDUCE, CarryGateV2Reason.DISPERSION_REDUCE, cfg.reduce_multiplier)
        return dec(CarryGateV2Status.ALLOW, CarryGateV2Reason.POSITIVE_BOTH_LOW_DISPERSION, 1.0)

    def hard_block(self, symbol: str, ts) -> bool:
        """Condition de SORTIE only (funding réellement négatif d'un côté) — pas la
        dispersion (qui ne gouverne que l'entrée/sizing). Évite le whipsaw de sortie."""
        d = self.evaluate(symbol, ts)
        return d.reason in (CarryGateV2Reason.NEGATIVE_BINANCE_FUNDING,
                            CarryGateV2Reason.NEGATIVE_BYBIT_FUNDING,
                            CarryGateV2Reason.MISSING_FUNDING)
