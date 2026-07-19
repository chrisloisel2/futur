"""
trading-system/src/institutional/portfolio/portfolio_state.py
═══════════════════════════════════════════════════════════════════════════════
Contrats PortfolioState et Position.

Importe depuis : institutional.contracts (SignalFrame, Direction, EngineID)
N'importe PAS depuis : data/schemas, risk, experiments.

Mutable (slots=True, frozen=False) : l'état du portefeuille évolue en continu.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Self

from institutional.contracts import Direction, EngineID


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════


class Side(StrEnum):
    BUY  = "buy"
    SELL = "sell"


# ══════════════════════════════════════════════════════════════════════════════
# Position
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class Position:
    """
    Position ouverte dans le portefeuille.

    Mutable : `current_price`, `notional_usd`, `unrealized_pnl` se mettent
    à jour à chaque barre via `update_price()`.

    Invariants :
        size != 0          (une position nulle doit être supprimée)
        entry_price > 0
        stop_price > 0
        take_profit_price > 0
    """

    asset:              str
    size:               float          # en unité d'actif (négatif = short)
    entry_price:        float
    current_price:      float
    notional_usd:       float          # |size| × current_price
    unrealized_pnl:     float          # size × (current - entry)
    weight:             float          # fraction de l'equity totale
    engine_name:        str
    signal_name:        str
    open_timestamp:     datetime
    stop_price:         float
    take_profit_price:  float
    max_holding_until:  datetime | None = None

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        errors: list[str] = []
        if self.size == 0.0:
            errors.append("size ne peut pas être 0 (supprimer la position)")
        if self.entry_price <= 0.0:
            errors.append(f"entry_price={self.entry_price!r} doit être > 0")
        if self.stop_price <= 0.0:
            errors.append(f"stop_price={self.stop_price!r} doit être > 0")
        if self.take_profit_price <= 0.0:
            errors.append(f"take_profit_price={self.take_profit_price!r} doit être > 0")
        if not self.asset.strip():
            errors.append("asset ne peut pas être vide")
        if not self.engine_name.strip():
            errors.append("engine_name ne peut pas être vide")
        if errors:
            raise ValueError(
                f"Position invalide :\n" + "\n".join(f"  • {e}" for e in errors)
            )

    # ── Mutations ─────────────────────────────────────────────────────────────

    def update_price(self, price: float) -> None:
        """Met à jour le prix courant et recalcule notional + unrealized PnL."""
        if price <= 0.0:
            raise ValueError(f"price={price!r} doit être > 0")
        self.current_price = price
        self.notional_usd  = abs(self.size) * price
        self.unrealized_pnl = self.size * (price - self.entry_price)

    # ── Propriétés ────────────────────────────────────────────────────────────

    @property
    def is_long(self) -> bool:
        return self.size > 0

    @property
    def is_short(self) -> bool:
        return self.size < 0

    @property
    def direction(self) -> Direction:
        return Direction.LONG if self.is_long else Direction.SHORT

    @property
    def return_pct(self) -> float:
        """Rendement non réalisé en fraction du notional d'entrée."""
        entry_notional = abs(self.size) * self.entry_price
        return self.unrealized_pnl / entry_notional if entry_notional else 0.0

    def stop_triggered(self) -> bool:
        if self.is_long:
            return self.current_price <= self.stop_price
        return self.current_price >= self.stop_price

    def tp_triggered(self) -> bool:
        if self.is_long:
            return self.current_price >= self.take_profit_price
        return self.current_price <= self.take_profit_price

    def time_expired(self, now: datetime) -> bool:
        return self.max_holding_until is not None and now >= self.max_holding_until

    # ── Sérialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, object]:
        return {
            "asset":             self.asset,
            "size":              self.size,
            "entry_price":       self.entry_price,
            "current_price":     self.current_price,
            "notional_usd":      self.notional_usd,
            "unrealized_pnl":    self.unrealized_pnl,
            "weight":            self.weight,
            "engine_name":       self.engine_name,
            "signal_name":       self.signal_name,
            "open_timestamp":    self.open_timestamp.isoformat(),
            "stop_price":        self.stop_price,
            "take_profit_price": self.take_profit_price,
            "max_holding_until": (
                self.max_holding_until.isoformat()
                if self.max_holding_until else None
            ),
        }


# ══════════════════════════════════════════════════════════════════════════════
# PortfolioState
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class PortfolioState:
    """
    État courant du portefeuille.

    Mutable : les positions s'ouvrent/ferment, l'equity fluctue.
    `refresh()` recalcule toutes les métriques agrégées depuis les positions.

    Invariants post-refresh :
        equity = cash + unrealized_pnl
        gross_exposure = Σ|notional_usd|
        net_exposure   = Σnotional_usd (signé)
        leverage       = gross_exposure / max(equity, ε)
    """

    timestamp:           datetime
    cash:                float
    equity:              float

    # ── Positions ─────────────────────────────────────────────────────────────
    positions:           dict[str, Position] = field(default_factory=dict)

    # ── PnL ───────────────────────────────────────────────────────────────────
    unrealized_pnl:      float = 0.0
    realized_pnl_today:  float = 0.0
    realized_pnl_total:  float = 0.0

    # ── Exposition ────────────────────────────────────────────────────────────
    gross_exposure:      float = 0.0
    net_exposure:        float = 0.0
    leverage:            float = 0.0
    n_positions:         int   = 0

    _EPS: float = field(default=1e-9, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.cash < 0:
            raise ValueError(f"cash={self.cash!r} doit être ≥ 0")

    # ── Mutations ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Recalcule toutes les métriques agrégées depuis self.positions."""
        self.unrealized_pnl = sum(p.unrealized_pnl for p in self.positions.values())
        self.equity         = self.cash + self.unrealized_pnl
        self.gross_exposure = sum(abs(p.notional_usd) for p in self.positions.values())
        self.net_exposure   = sum(p.notional_usd for p in self.positions.values())
        self.leverage       = self.gross_exposure / max(self.equity, self._EPS)
        self.n_positions    = len(self.positions)

    def add_position(self, position: Position) -> None:
        key = position.asset
        if key in self.positions:
            raise ValueError(
                f"Position déjà ouverte sur {key!r} — fermer avant d'en ouvrir une nouvelle"
            )
        self.positions[key] = position
        self.refresh()

    def remove_position(
        self,
        asset: str,
        *,
        realized_pnl: float,
    ) -> Position:
        """Ferme une position et enregistre le PnL réalisé."""
        if asset not in self.positions:
            raise KeyError(f"Aucune position ouverte sur {asset!r}")
        pos = self.positions.pop(asset)
        self.cash += pos.notional_usd + realized_pnl
        self.realized_pnl_today += realized_pnl
        self.realized_pnl_total += realized_pnl
        self.refresh()
        return pos

    def update_prices(self, prices: dict[str, float]) -> None:
        """Met à jour les prix de toutes les positions et rafraîchit l'état."""
        for asset, price in prices.items():
            if asset in self.positions:
                self.positions[asset].update_price(price)
        self.refresh()

    # ── Vues ──────────────────────────────────────────────────────────────────

    def per_engine_exposure(self) -> dict[str, float]:
        """Exposition brute en USD par engine_name."""
        result: dict[str, float] = {}
        for p in self.positions.values():
            result[p.engine_name] = result.get(p.engine_name, 0.0) + abs(p.notional_usd)
        return result

    def per_asset_exposure(self) -> dict[str, float]:
        """Exposition brute en USD par asset."""
        return {k: abs(v.notional_usd) for k, v in self.positions.items()}

    def long_positions(self) -> dict[str, Position]:
        return {k: v for k, v in self.positions.items() if v.is_long}

    def short_positions(self) -> dict[str, Position]:
        return {k: v for k, v in self.positions.items() if v.is_short}

    # ── Sérialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp":          self.timestamp.isoformat(),
            "cash":               self.cash,
            "equity":             self.equity,
            "unrealized_pnl":     self.unrealized_pnl,
            "realized_pnl_today": self.realized_pnl_today,
            "realized_pnl_total": self.realized_pnl_total,
            "gross_exposure":     self.gross_exposure,
            "net_exposure":       self.net_exposure,
            "leverage":           self.leverage,
            "n_positions":        self.n_positions,
            "positions": {
                k: v.to_dict() for k, v in self.positions.items()
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def empty(
        cls,
        *,
        initial_cash: float,
        timestamp: datetime | None = None,
    ) -> Self:
        """Crée un portefeuille vide avec le capital initial."""
        if initial_cash <= 0:
            raise ValueError(f"initial_cash={initial_cash!r} doit être > 0")
        return cls(
            timestamp=timestamp or datetime.now(timezone.utc),
            cash=initial_cash,
            equity=initial_cash,
        )
