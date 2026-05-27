"""
execution/smart_router.py — Smart Order Router

Décide de la stratégie d'exécution optimale:
  - Order type: market vs limit
  - Execution strategy: immediate / TWAP / VWAP
  - Slicing: quantité unique ou découpée

Logique:
  urgency HIGH    → market immédiat (pas de slicing)
  urgency NORMAL  → TWAP avec 4 tranches limit
  urgency LOW     → VWAP si profil disponible, sinon TWAP

  Si taille > size_threshold → toujours slicer
  Si spread > spread_threshold → market (pour éviter le front-running)

Usage:
  router = SmartRouter(slippage_model, twap_engine, vwap_engine)
  plan = router.route(signal, current_price=50000, bar=bar_dict, urgency="normal")
  print(plan.strategy, plan.slices)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from execution.slippage_model import SlippageModel, SlippageEstimate
from execution.twap_engine import TWAPEngine, TWAPSlice
from execution.vwap_engine import VWAPEngine, VWAPSlice


@dataclass
class ExecutionPlan:
    strategy:        str              # "immediate_market" | "immediate_limit" | "twap" | "vwap"
    order_type:      str              # "market" | "limit"
    sliced:          bool
    twap_slices:     list[TWAPSlice] = field(default_factory=list)
    vwap_slices:     list[VWAPSlice] = field(default_factory=list)
    quantity:        float           = 0.0
    slippage_est:    Optional[SlippageEstimate] = None
    adjusted_threshold: Optional[float] = None
    reasoning:       list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "strategy":     self.strategy,
            "order_type":   self.order_type,
            "sliced":       self.sliced,
            "n_slices":     len(self.twap_slices) or len(self.vwap_slices),
            "quantity":     self.quantity,
            "slippage_bps": round(self.slippage_est.bps, 2) if self.slippage_est else None,
            "reasoning":    self.reasoning,
        }


class SmartRouter:
    """
    Routeur d'ordres intelligent.

    Prend en compte:
    - Urgence du signal (haute → exécution immédiate market)
    - Taille relative (grande → slicer)
    - Spread estimé (large spread → préférer market)
    - Slippage estimé (ajuste le seuil de décision)
    """

    def __init__(
        self,
        slippage_model:     Optional[SlippageModel]  = None,
        twap_engine:        Optional[TWAPEngine]     = None,
        vwap_engine:        Optional[VWAPEngine]     = None,
        size_threshold:     float = 0.005,    # > 0.5% ADV → slicer
        spread_threshold:   float = 0.005,    # > 50bps spread → market
        default_n_slices:   int   = 4,
    ):
        self._slippage    = slippage_model or SlippageModel()
        self._twap        = twap_engine    or TWAPEngine(n_slices=default_n_slices)
        self._vwap        = vwap_engine    or VWAPEngine()
        self._size_thr    = size_threshold
        self._spread_thr  = spread_threshold
        self._n_slices    = default_n_slices

    # ------------------------------------------------------------------
    # Main routing
    # ------------------------------------------------------------------

    def route(
        self,
        quantity:       float,
        side:           str,
        current_price:  float,
        bar:            dict | pd.Series,
        urgency:        str = "normal",        # "high" | "normal" | "low"
        horizon_bars:   int = 8,
        raw_threshold:  Optional[float] = None,
        start_hour:     int = 0,
    ) -> ExecutionPlan:
        """
        Détermine le plan d'exécution optimal.

        Args:
            quantity      : quantité à exécuter (fraction ou unité)
            side          : "long" | "short"
            current_price : prix courant
            bar           : features de la barre (pour slippage)
            urgency       : "high" / "normal" / "low"
            horizon_bars  : horizon du trade
            raw_threshold : seuil de probabilité brut (pour ajustement)
        """
        if isinstance(bar, pd.Series):
            bar = bar.to_dict()

        reasoning = []

        # 1. Estimer le slippage
        vol_ratio  = float(bar.get("vol_ratio_24", 1.0))
        slip_est   = self._slippage.predict_from_bar(bar, quantity_frac=quantity)
        reasoning.append(f"slippage_est_{slip_est.bps:.1f}bps")

        # 2. Ajuster le seuil si fourni
        adj_thr = None
        if raw_threshold is not None:
            adj_thr = self._slippage.adjust_threshold(raw_threshold, slip_est.bps)
            if adj_thr > raw_threshold:
                reasoning.append(f"threshold_raised_{raw_threshold:.3f}→{adj_thr:.3f}")

        # 3. Décision d'urgence
        spread_est = float(bar.get("intrabar_range_pct", 0.01))
        must_market = spread_est > self._spread_thr
        if must_market:
            reasoning.append("wide_spread_force_market")

        # 4. Décision de slicing
        should_slice = quantity > self._size_thr and urgency != "high"
        if urgency == "high":
            reasoning.append("high_urgency_no_slice")

        # 5. Construire le plan
        if urgency == "high" or must_market:
            return ExecutionPlan(
                strategy           = "immediate_market",
                order_type         = "market",
                sliced             = False,
                quantity           = quantity,
                slippage_est       = slip_est,
                adjusted_threshold = adj_thr,
                reasoning          = reasoning,
            )

        if should_slice:
            # VWAP si profil disponible, TWAP sinon
            if self._vwap.is_fitted() and urgency == "low":
                slices = self._vwap.schedule(quantity, horizon_bars, start_hour)
                reasoning.append(f"vwap_{len(slices)}_slices")
                return ExecutionPlan(
                    strategy           = "vwap",
                    order_type         = "limit",
                    sliced             = True,
                    vwap_slices        = slices,
                    quantity           = quantity,
                    slippage_est       = slip_est,
                    adjusted_threshold = adj_thr,
                    reasoning          = reasoning,
                )
            else:
                # TWAP
                self._twap.start(
                    quantity    = quantity,
                    horizon_bars= horizon_bars,
                    entry_price = current_price,
                    side        = side,
                    n_slices    = self._n_slices,
                )
                slices = []
                for _ in range(self._n_slices):
                    s = self._twap.next_slice(current_price, urgency=urgency)
                    if s:
                        slices.append(s)
                reasoning.append(f"twap_{len(slices)}_slices")
                return ExecutionPlan(
                    strategy           = "twap",
                    order_type         = "limit",
                    sliced             = True,
                    twap_slices        = slices,
                    quantity           = quantity,
                    slippage_est       = slip_est,
                    adjusted_threshold = adj_thr,
                    reasoning          = reasoning,
                )

        # Ordre limit immédiat
        reasoning.append("immediate_limit")
        return ExecutionPlan(
            strategy           = "immediate_limit",
            order_type         = "limit",
            sliced             = False,
            quantity           = quantity,
            slippage_est       = slip_est,
            adjusted_threshold = adj_thr,
            reasoning          = reasoning,
        )

    # ------------------------------------------------------------------
    # Maker/taker advisor
    # ------------------------------------------------------------------

    def maker_or_taker(
        self,
        urgency:    str,
        spread_bps: float,
        fill_prob:  float = 0.70,     # probabilité de fill limit estimée
    ) -> str:
        """
        Conseille maker (limit) ou taker (market).
        Maker = moins cher mais risque de non-fill.
        """
        if urgency == "high":
            return "taker"
        if spread_bps > self._spread_thr * 10000:
            return "taker"    # spread trop large → aller market
        if fill_prob >= 0.65:
            return "maker"    # bon P(fill) → utiliser le limit
        return "taker"
