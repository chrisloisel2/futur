"""
ai/alphas/registry.py — AlphaRegistry

Enregistre les micro-alphas, les exécute en parallèle, blend les signaux.

Blending:
  weight(alpha) = conviction × regime_multiplier × diversification_bonus
  diversification_bonus = 1 - corrélation_moyenne_avec_actifs_alphas_actifs

Usage:
  reg = AlphaRegistry()
  reg.register(FundingCarryAlpha())
  reg.register(OIMomentumAlpha())

  signals = reg.run_all(bar, context, regime="EXPANSION")
  blended = reg.blend(signals, regime="EXPANSION")
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ai.alphas.base import AlphaBase, AlphaSignal


@dataclass
class BlendedSignal:
    side:             str            # "long" | "short" | "neutral"
    conviction:       float          # [0, 1] convicti agrégée
    contributing:     list[str]      # alphas qui ont contribué
    total_allocation: float          # fraction du capital suggérée
    metadata:         dict = field(default_factory=dict)

    def is_actionable(self, min_conviction: float = 0.30) -> bool:
        return self.side != "neutral" and self.conviction >= min_conviction


class AlphaRegistry:
    """
    Registre centralisé des micro-alphas avec:
    - Exécution séquentielle (ordre d'enregistrement)
    - Tracking de corrélation des PnL (rolling 252 barres)
    - Blend pondéré par conviction × diversification
    - Stats d'activation par alpha
    """

    def __init__(self, pnl_window: int = 252):
        self._alphas:   list[AlphaBase] = []
        self._pnl_window = pnl_window
        # PnL rolling par alpha: {name → deque of float}
        self._pnl_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=pnl_window)
        )
        # Stats d'activation
        self._activation_count: dict[str, int] = defaultdict(int)
        self._call_count:       dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, alpha: AlphaBase) -> None:
        assert isinstance(alpha, AlphaBase), "Must inherit from AlphaBase"
        names = [a.name for a in self._alphas]
        assert alpha.name not in names, f"Alpha '{alpha.name}' already registered"
        self._alphas.append(alpha)

    def registered_names(self) -> list[str]:
        return [a.name for a in self._alphas]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_all(
        self,
        bar:     pd.Series,
        context: dict,
        regime:  Optional[str] = None,
    ) -> list[AlphaSignal]:
        """
        Exécute tous les alphas valides sur la barre courante.

        Args:
            bar     : features de la barre (pd.Series)
            context : dict de contexte (portfolio, equity, etc.)
            regime  : RegimeState actuel (str) pour filtrer les alphas

        Returns:
            list de AlphaSignal (seulement les actifs)
        """
        signals = []
        for alpha in self._alphas:
            self._call_count[alpha.name] += 1
            if not alpha.is_valid(bar):
                continue
            if not alpha.regime_allowed(regime):
                continue
            try:
                sig = alpha.generate(bar, context)
                if sig is not None and sig.is_directional():
                    signals.append(sig)
                    self._activation_count[alpha.name] += 1
            except Exception:
                pass  # silently ignore individual alpha failures
        return signals

    def blend(
        self,
        signals:     list[AlphaSignal],
        regime:      Optional[str] = None,
        max_total_alloc: float = 0.06,
    ) -> BlendedSignal:
        """
        Blend un ensemble de signaux en un signal agrégé.

        Stratégie:
          1. Séparer long/short
          2. Pondérer par conviction × diversification_bonus
          3. Agréger par côté dominant
          4. Cap la taille totale à max_total_alloc
        """
        if not signals:
            return BlendedSignal("neutral", 0.0, [], 0.0)

        # Votes pondérés par conviction
        long_weight  = sum(s.conviction for s in signals if s.side == "long")
        short_weight = sum(s.conviction for s in signals if s.side == "short")

        if long_weight == 0 and short_weight == 0:
            return BlendedSignal("neutral", 0.0, [], 0.0)

        # Côté dominant
        if long_weight >= short_weight:
            dominant_side    = "long"
            raw_conviction   = long_weight / max(long_weight + short_weight, 1e-6)
            contributing     = [s.name for s in signals if s.side == "long"]
            contributing_sigs= [s for s in signals if s.side == "long"]
        else:
            dominant_side    = "short"
            raw_conviction   = short_weight / max(long_weight + short_weight, 1e-6)
            contributing     = [s.name for s in signals if s.side == "short"]
            contributing_sigs= [s for s in signals if s.side == "short"]

        # Diversification bonus: plus d'alphas indépendants → bonus
        n_contrib = len(contributing_sigs)
        corr_penalty = self._mean_correlation(contributing) if n_contrib > 1 else 0.0
        div_bonus = max(0.0, 1.0 - corr_penalty)

        final_conviction = float(np.clip(raw_conviction * div_bonus, 0.0, 1.0))

        # Allocation: somme des max_alloc des alphas contributeurs × conviction
        alpha_map = {a.name: a for a in self._alphas}
        total_alloc = min(
            max_total_alloc,
            sum(alpha_map[n].max_allocation for n in contributing if n in alpha_map)
            * final_conviction
        )

        return BlendedSignal(
            side             = dominant_side,
            conviction       = round(final_conviction, 4),
            contributing     = contributing,
            total_allocation = round(total_alloc, 4),
            metadata         = {
                "long_weight":  round(long_weight, 4),
                "short_weight": round(short_weight, 4),
                "n_signals":    len(signals),
                "corr_penalty": round(corr_penalty, 4),
                "div_bonus":    round(div_bonus, 4),
            },
        )

    # ------------------------------------------------------------------
    # PnL tracking (pour correlation)
    # ------------------------------------------------------------------

    def record_pnl(self, alpha_name: str, pnl: float) -> None:
        self._pnl_history[alpha_name].append(pnl)

    def correlation_matrix(self) -> pd.DataFrame:
        names = [n for n, d in self._pnl_history.items() if len(d) >= 10]
        if len(names) < 2:
            return pd.DataFrame()
        data = {n: list(self._pnl_history[n]) for n in names}
        min_len = min(len(v) for v in data.values())
        df = pd.DataFrame({n: v[-min_len:] for n, v in data.items()})
        return df.corr()

    # ------------------------------------------------------------------
    # Stats & reporting
    # ------------------------------------------------------------------

    def report(self) -> dict:
        return {
            a.name: {
                "calls":           self._call_count[a.name],
                "activations":     self._activation_count[a.name],
                "activation_rate": round(
                    self._activation_count[a.name] / max(self._call_count[a.name], 1), 4
                ),
                "max_allocation":  a.max_allocation,
            }
            for a in self._alphas
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _mean_correlation(self, alpha_names: list[str]) -> float:
        """Corrélation moyenne par paires entre les alphas (PnL rolling)."""
        valid = [n for n in alpha_names if len(self._pnl_history[n]) >= 10]
        if len(valid) < 2:
            return 0.0
        corrs = []
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                a = np.array(self._pnl_history[valid[i]])
                b = np.array(self._pnl_history[valid[j]])
                min_len = min(len(a), len(b))
                if min_len < 5:
                    continue
                c = np.corrcoef(a[-min_len:], b[-min_len:])[0, 1]
                if not np.isnan(c):
                    corrs.append(abs(c))
        return float(np.mean(corrs)) if corrs else 0.0
