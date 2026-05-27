"""
execution/twap_engine.py — TWAP Engine (Time-Weighted Average Price)

Découpe une commande en N tranches égales sur horizon_bars barres.
Adaptif: si le prix évolue adversement > adverse_pct → skip la tranche.

Usage:
  twap = TWAPEngine()
  twap.start(quantity=1.0, horizon_bars=8, entry_price=50000.0, side="long")

  # Chaque barre:
  slice_order = twap.next_slice(current_price=50100.0)
  if slice_order:
      execute(slice_order.qty, slice_order.order_type)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TWAPSlice:
    qty:         float    # quantité à exécuter dans ce slice
    bar:         int      # barre index cible
    order_type:  str      # "market" | "limit"
    limit_price: Optional[float]
    slice_num:   int
    total_slices:int


class TWAPEngine:
    """
    TWAP adaptatif: découpe la commande en tranches temporelles égales.

    Adaptatif:
      - Si le prix s'est déplacé adversement > adverse_pct → sauter la tranche
      - Si urgency="high" → exécuter en market immédiatement
    """

    def __init__(
        self,
        n_slices:    int   = 4,        # nombre de tranches par défaut
        adverse_pct: float = 0.003,    # 0.3% mouvement adverse → skip
        limit_offset:float = 0.001,    # 0.1% pour les ordres limit
    ):
        self._n_slices    = n_slices
        self._adverse     = adverse_pct
        self._limit_off   = limit_offset

        # État courant
        self._quantity:   float = 0.0
        self._side:       str   = "long"
        self._entry_price:float = 0.0
        self._slice_qty:  float = 0.0
        self._start_bar:  int   = 0
        self._cur_bar:    int   = 0
        self._executed:   int   = 0    # slices exécutés
        self._active:     bool  = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        quantity:      float,
        horizon_bars:  int,
        entry_price:   float,
        side:          str = "long",
        n_slices:      Optional[int] = None,
    ) -> "TWAPEngine":
        n = n_slices or min(self._n_slices, horizon_bars)
        self._quantity    = quantity
        self._side        = side
        self._entry_price = entry_price
        self._slice_qty   = quantity / n
        self._n_slices    = n
        self._start_bar   = 0
        self._cur_bar     = 0
        self._executed    = 0
        self._active      = True
        return self

    def is_active(self) -> bool:
        return self._active and self._executed < self._n_slices

    def is_complete(self) -> bool:
        return self._executed >= self._n_slices

    def remaining_qty(self) -> float:
        remaining_slices = self._n_slices - self._executed
        return remaining_slices * self._slice_qty

    # ------------------------------------------------------------------
    # Bar-by-bar execution
    # ------------------------------------------------------------------

    def next_slice(
        self,
        current_price: float,
        urgency:       str = "normal",   # "high" | "normal" | "low"
    ) -> Optional[TWAPSlice]:
        """
        Retourne le prochain slice à exécuter, ou None si:
        - Pas de TWAP actif
        - Mouvement adverse détecté (sauf urgency="high")
        """
        if not self.is_active():
            return None

        self._cur_bar += 1

        # Vérifier le mouvement adverse
        if urgency != "high" and self._is_adverse_move(current_price):
            return None   # Skip ce slice

        # Déterminer le type d'ordre
        if urgency == "high":
            order_type  = "market"
            limit_price = None
        else:
            order_type  = "limit"
            limit_price = self._limit_price(current_price)

        self._executed += 1

        if self.is_complete():
            self._active = False

        return TWAPSlice(
            qty          = self._slice_qty,
            bar          = self._cur_bar,
            order_type   = order_type,
            limit_price  = limit_price,
            slice_num    = self._executed,
            total_slices = self._n_slices,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_adverse_move(self, current_price: float) -> bool:
        if self._entry_price <= 0:
            return False
        move = (current_price - self._entry_price) / self._entry_price
        if self._side == "long":
            return move > self._adverse    # prix monté → plus cher qu'attendu
        else:
            return move < -self._adverse   # prix baissé → plus cher pour shorter

    def _limit_price(self, current_price: float) -> float:
        if self._side == "long":
            return current_price * (1 + self._limit_off)   # acheter légèrement au-dessus du mid
        else:
            return current_price * (1 - self._limit_off)   # vendre légèrement en-dessous
