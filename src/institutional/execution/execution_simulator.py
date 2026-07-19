"""
src/institutional/execution/execution_simulator.py
─────────────────────────────────────────────────────────────────────────────
Simulateur d'exécution institutionnel.

Aucun résultat de backtest ne doit être reporté sans :
  - frais (maker/taker)
  - slippage (volatilité-dépendant)
  - latence
  - participation rate limit

Tous les paramètres sont configurables et auditables.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class ExecutionConfig:
    """Configuration complète du simulateur d'exécution."""
    taker_fee_bps: float = 5.0       # taker fee Binance futures
    maker_fee_bps: float = 2.0       # maker fee Binance futures
    fixed_slippage_bps: float = 2.0  # slippage minimal
    vol_slippage_mult: float = 0.5   # slippage × this × current_vol
    spread_mult: float = 1.0         # multiplicateur bid/ask spread
    latency_seconds: float = 0.5     # latence d'exécution simulée
    max_participation_rate: float = 0.10  # max 10% du volume de la barre
    min_order_notional: float = 10.0      # ordre min en USD
    assume_taker: bool = True             # True = utiliser taker_fee


@dataclass
class Fill:
    """Résultat d'exécution d'un ordre."""
    timestamp: pd.Timestamp
    asset: str
    side: str               # "buy" | "sell"
    size: float             # taille en unité d'actif
    price: float            # prix d'exécution simulé
    notional: float         # taille en USD
    fee: float              # frais en USD
    slippage_bps: float     # slippage réalisé en bps
    latency_seconds: float
    fill_rate: float        # fraction de l'ordre rempli (0-1)
    rejected: bool = False
    reject_reason: str = ""

    @property
    def total_cost(self) -> float:
        return self.fee + self.notional * (self.slippage_bps / 10_000)


class ExecutionSimulator:
    """
    Simule l'exécution d'ordres dans un backtest événementiel.

    Usage
    -----
    sim = ExecutionSimulator(config)
    fill = sim.execute(
        timestamp=ts,
        asset="BTCUSDT",
        side="buy",
        size=0.01,
        bar_price=50000,
        bar_volume=1000,
        current_vol=0.015,
    )
    """

    def __init__(self, config: Optional[ExecutionConfig] = None):
        self.config = config or ExecutionConfig()
        self._fills: List[Fill] = []

    def execute(
        self,
        timestamp: pd.Timestamp,
        asset: str,
        side: str,                   # "buy" | "sell"
        size: float,                 # en unité d'actif
        bar_price: float,            # prix OHLCV de la barre
        bar_volume: float = 0.0,     # volume de la barre (pour participation rate)
        current_vol: float = 0.01,   # vol réalisée courante (fraction)
        min_notional: Optional[float] = None,
    ) -> Fill:
        """
        Exécute un ordre et retourne le Fill.

        Le prix d'exécution inclut le slippage basé sur la volatilité.
        """
        cfg = self.config
        notional = abs(size) * bar_price

        # Vérification notional minimum
        min_n = min_notional or cfg.min_order_notional
        if notional < min_n:
            return Fill(
                timestamp=timestamp, asset=asset, side=side,
                size=size, price=bar_price, notional=notional,
                fee=0, slippage_bps=0, latency_seconds=0, fill_rate=0,
                rejected=True, reject_reason=f"notional {notional:.2f} < min {min_n:.2f}",
            )

        # Participation rate check
        fill_rate = 1.0
        if bar_volume > 0 and size > bar_volume * cfg.max_participation_rate:
            fill_rate = cfg.max_participation_rate * bar_volume / size
            size = size * fill_rate
            notional = abs(size) * bar_price

        # Slippage (vol-dépendant)
        slippage_bps = cfg.fixed_slippage_bps + cfg.vol_slippage_mult * current_vol * 10_000
        slip_frac = slippage_bps / 10_000

        # Direction du slippage (adverse)
        if side == "buy":
            exec_price = bar_price * (1 + slip_frac)
        else:
            exec_price = bar_price * (1 - slip_frac)

        # Frais
        fee_rate = cfg.taker_fee_bps if cfg.assume_taker else cfg.maker_fee_bps
        fee = notional * fee_rate / 10_000

        fill = Fill(
            timestamp=timestamp,
            asset=asset,
            side=side,
            size=size,
            price=exec_price,
            notional=notional,
            fee=fee,
            slippage_bps=slippage_bps,
            latency_seconds=cfg.latency_seconds,
            fill_rate=fill_rate,
        )
        self._fills.append(fill)
        return fill

    def compute_round_trip_cost_bps(self, current_vol: float = 0.01) -> float:
        """
        Coût aller-retour estimé en bps (pour calibrer les barrières de labels).
        """
        cfg = self.config
        fee_rt = (cfg.taker_fee_bps if cfg.assume_taker else cfg.maker_fee_bps) * 2
        slip_rt = (cfg.fixed_slippage_bps + cfg.vol_slippage_mult * current_vol * 10_000) * 2
        return fee_rt + slip_rt

    def get_fills_df(self) -> pd.DataFrame:
        if not self._fills:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "timestamp": f.timestamp,
                "asset": f.asset,
                "side": f.side,
                "size": f.size,
                "price": f.price,
                "notional": f.notional,
                "fee": f.fee,
                "slippage_bps": f.slippage_bps,
                "fill_rate": f.fill_rate,
                "rejected": f.rejected,
            }
            for f in self._fills
        ])

    def total_fees_paid(self) -> float:
        return sum(f.fee for f in self._fills if not f.rejected)

    def total_slippage_estimate(self) -> float:
        return sum(f.notional * f.slippage_bps / 10_000 for f in self._fills if not f.rejected)

    def summary(self) -> Dict[str, Any]:
        fills = [f for f in self._fills if not f.rejected]
        return {
            "n_fills": len(fills),
            "n_rejected": sum(1 for f in self._fills if f.rejected),
            "total_notional": sum(f.notional for f in fills),
            "total_fees": self.total_fees_paid(),
            "total_slippage_est": self.total_slippage_estimate(),
            "avg_slippage_bps": float(np.mean([f.slippage_bps for f in fills])) if fills else 0,
        }
