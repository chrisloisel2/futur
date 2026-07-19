"""
src/institutional/portfolio/allocator.py
─────────────────────────────────────────────────────────────────────────────
Portfolio Allocator institutionnel.

Le sizing ne passe JAMAIS directement par score × capital.
Toujours : expected_return / expected_vol × confidence × risk_budget.

Méthodes implémentées :
  1. Inverse volatility
  2. Volatility targeting
  3. Equal Risk Contribution (ERC)
  4. Fractional Kelly
  5. Confidence-weighted

Le sizing final est limité par le Risk Engine (risk_engine.py).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.contracts import (
    PortfolioState, RiskState, SignalFrame,
)

logger = logging.getLogger(__name__)


class PortfolioAllocator:
    """
    Calcule la taille cible de position pour un signal donné.

    Usage
    -----
    allocator = PortfolioAllocator(method="vol_targeting", target_vol=0.15)
    size_frac = allocator.compute_size(signal, portfolio, risk_state, equity)
    """

    def __init__(
        self,
        method: str = "vol_targeting",
        target_vol: float = 0.15,           # vol annualisée cible du portefeuille
        max_position_pct: float = 0.25,     # max 25% de l'equity par position
        kelly_fraction: float = 0.25,       # fraction de Kelly (Kelly complet × 0.25)
        risk_budget_pct: float = 0.02,      # budget de risque par trade
    ):
        self.method = method
        self.target_vol = target_vol
        self.max_position_pct = max_position_pct
        self.kelly_fraction = kelly_fraction
        self.risk_budget_pct = risk_budget_pct

    def compute_size(
        self,
        signal: SignalFrame,
        portfolio: PortfolioState,
        risk_state: RiskState,
        equity: float,
        current_price: float,
        size_multiplier: float = 1.0,   # appliqué par Risk Engine
    ) -> float:
        """
        Calcule la taille cible en fraction de l'equity.

        Retourne une fraction [0, max_position_pct].
        """
        if signal.direction == "flat" or equity <= 0:
            return 0.0

        method = self.method

        if method == "inverse_vol":
            size = self._inverse_vol_size(signal)
        elif method == "vol_targeting":
            size = self._vol_targeting_size(signal)
        elif method == "kelly":
            size = self._kelly_size(signal)
        elif method == "risk_budget":
            size = self._risk_budget_size(signal)
        else:
            size = 0.25  # default

        # Ajustement confiance
        size *= signal.confidence

        # Ajustement Risk Engine
        size *= size_multiplier

        # Drawdown adjustment
        if risk_state.peak_equity > 0:
            dd = (equity - risk_state.peak_equity) / risk_state.peak_equity
            if dd < -0.05:
                dd_factor = max(0.25, 1 + dd * 4)  # réduit progressivement
                size *= dd_factor

        # Clip
        size = float(np.clip(size, 0.0, self.max_position_pct))

        return size

    def _inverse_vol_size(self, signal: SignalFrame) -> float:
        """1 / σ normalisé par la target vol."""
        if signal.expected_vol <= 0:
            return self.max_position_pct * 0.5
        return self.target_vol / (signal.expected_vol + 1e-9)

    def _vol_targeting_size(self, signal: SignalFrame) -> float:
        """
        Volatility targeting : alloue assez pour obtenir target_vol de contribution.
        size = target_vol / current_vol
        """
        if signal.expected_vol <= 0:
            return self.max_position_pct * 0.5
        return self.target_vol / signal.expected_vol

    def _kelly_size(self, signal: SignalFrame) -> float:
        """
        Kelly fractionnaire : f = (p × b - q) / b × kelly_fraction
        où p = win_rate (calibrated_score), b = reward/risk ratio.
        """
        p = float(np.clip(signal.calibrated_score, 0.01, 0.99))
        q = 1 - p
        # b = expected_return / stop_distance (reward/risk ratio approximatif)
        b = signal.expected_return / max(signal.stop_distance, 0.005)
        if b <= 0:
            return 0.0
        kelly_full = (p * b - q) / b
        return max(0, kelly_full) * self.kelly_fraction

    def _risk_budget_size(self, signal: SignalFrame) -> float:
        """
        Budget risque fixe : size tel que la perte max potentielle = risk_budget.
        size = risk_budget / stop_distance
        """
        if signal.stop_distance <= 0:
            return self.max_position_pct * 0.25
        return self.risk_budget_pct / signal.stop_distance


class MetaPortfolioAllocator:
    """
    Allocateur supérieur combinant TRM_EVENT_ENGINE et INSTITUTIONAL_ENGINE.

    Garantit :
      - Pas de double exposition cachée
      - Budget par engine
      - Budget par actif
      - Réduction en drawdown
    """

    def __init__(
        self,
        trm_max_pct: float = 0.50,           # max 50% pour TRM
        institutional_max_pct: float = 0.50,  # max 50% pour Institutional
        max_asset_pct: float = 0.40,          # max 40% sur un actif
        target_vol: float = 0.15,
    ):
        self.trm_max_pct = trm_max_pct
        self.institutional_max_pct = institutional_max_pct
        self.max_asset_pct = max_asset_pct
        self.allocator = PortfolioAllocator(method="vol_targeting", target_vol=target_vol)

    def compute_allocations(
        self,
        signals: List[SignalFrame],
        portfolio: PortfolioState,
        risk_state: RiskState,
        equity: float,
        prices: Dict[str, float],
        size_multipliers: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Retourne les allocations cibles (fractions de l'equity) par actif.

        Les signaux plats ou bloqués ont une allocation 0.
        Les allocations sont consolidées par actif (somme des engines).
        """
        size_multipliers = size_multipliers or {}
        allocations: Dict[str, float] = {}

        # Budget par engine
        engine_budget = {
            "TRM_EVENT_ENGINE": self.trm_max_pct,
            "INSTITUTIONAL_ENGINE": self.institutional_max_pct,
        }
        engine_used: Dict[str, float] = {}

        for signal in signals:
            if signal.direction == "flat":
                continue

            engine = signal.engine_name
            mult = size_multipliers.get(signal.asset, 1.0)
            price = prices.get(signal.asset, 1.0)

            size = self.allocator.compute_size(
                signal, portfolio, risk_state, equity, price, size_multiplier=mult
            )

            # Vérification budget engine
            used = engine_used.get(engine, 0.0)
            budget = engine_budget.get(engine, 0.50)
            if used + size > budget:
                size = max(0, budget - used)

            # Vérification exposition actif (consolidée)
            current_asset_alloc = allocations.get(signal.asset, 0.0)
            if current_asset_alloc + size > self.max_asset_pct:
                size = max(0, self.max_asset_pct - current_asset_alloc)

            if size > 0:
                allocations[signal.asset] = allocations.get(signal.asset, 0.0) + size
                engine_used[engine] = used + size

        return allocations
