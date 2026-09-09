"""A broker that moves no money.

It holds paper positions, applies simulated fills, charges the fees the spec
declares, and marks to market from the same book the decision saw.  Mark to
market is computed here and labelled as such: a portfolio layer that reports a
PnL without marking is reporting a wish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from paper_engine.fill_simulator import Fill

BPS = 1e4


@dataclass
class Position:
    symbol: str
    notional: float = 0.0  # signed, in quote currency
    avg_price: float = 0.0
    realised_pnl: float = 0.0
    fees_paid: float = 0.0

    @property
    def qty(self) -> float:
        return self.notional / self.avg_price if self.avg_price else 0.0


@dataclass
class PaperBroker:
    starting_cash: float = 200_000.0
    fee_bps_per_side: float = 4.0
    positions: Dict[str, Position] = field(default_factory=dict)
    cash: float = 0.0
    fees_paid: float = 0.0
    realised_pnl: float = 0.0
    deferred_notional: float = 0.0
    fills: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = float(self.starting_cash)

    # ------------------------------------------------------------------
    def apply(self, fill: Fill) -> Dict[str, Any]:
        pos = self.positions.setdefault(fill.symbol, Position(fill.symbol))
        signed = fill.filled_notional * (1.0 if fill.side in ("long", "buy") else -1.0)
        fee = abs(fill.filled_notional) * self.fee_bps_per_side / BPS

        if pos.notional == 0 or (pos.notional > 0) == (signed > 0):
            total = pos.notional + signed
            if total != 0:
                pos.avg_price = (
                    (abs(pos.notional) * pos.avg_price + abs(signed) * fill.price)
                    / (abs(pos.notional) + abs(signed))
                ) if (abs(pos.notional) + abs(signed)) else fill.price
            pos.notional = total
        else:
            closing = min(abs(signed), abs(pos.notional))
            direction = 1.0 if pos.notional > 0 else -1.0
            if pos.avg_price:
                pnl = direction * closing * (fill.price - pos.avg_price) / pos.avg_price
                pos.realised_pnl += pnl
                self.realised_pnl += pnl
                self.cash += pnl
            pos.notional += signed
            if abs(pos.notional) < 1e-9:
                pos.notional = 0.0
                pos.avg_price = 0.0

        pos.fees_paid += fee
        self.fees_paid += fee
        self.cash -= fee
        self.deferred_notional += fill.deferred_notional

        record = fill.to_dict()
        record["fee"] = fee
        record["position_notional_after"] = pos.notional
        self.fills.append(record)
        return record

    # ------------------------------------------------------------------
    def mark_to_market(self, marks: Dict[str, float]) -> Dict[str, Any]:
        """Unrealised PnL against supplied marks.  Symbols without a mark are
        reported, not assumed flat."""
        unrealised = 0.0
        unmarked: List[str] = []
        for sym, pos in self.positions.items():
            if pos.notional == 0:
                continue
            mark = marks.get(sym)
            if mark is None or not pos.avg_price:
                unmarked.append(sym)
                continue
            unrealised += pos.notional * (mark - pos.avg_price) / pos.avg_price
        return {
            "cash": self.cash,
            "realised_pnl": self.realised_pnl,
            "unrealised_pnl": unrealised,
            "fees_paid": self.fees_paid,
            "equity": self.cash + unrealised,
            "gross_exposure": sum(abs(p.notional) for p in self.positions.values()),
            "net_exposure": sum(p.notional for p in self.positions.values()),
            "deferred_notional": self.deferred_notional,
            "unmarked_symbols": unmarked,
            "mark_to_market": True,
        }
