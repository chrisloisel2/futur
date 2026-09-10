"""Costs, applied before any result is looked at.

The single most expensive mistake this repository has already made is measuring
a signal that is statistically real and economically under the cost floor.  So
cost is not a post-processing step here: ``run_mechanism`` computes the cost
floor from the spec and kills the mechanism at gate 2, before robustness,
placebo or multiplicity are ever computed.

Two conventions matter and are easy to get wrong:

* ``t`` is computed on the GROSS series, never on the net one.  A ``t`` on a net
  series with a constant cost measures the cost: ``|t_net| -> cost * sqrt(n) / sigma``
  grows without bound in ``n`` for a mechanism with no edge at all.
* Cost is compared to gross in LEVEL (``breakeven_capture``), not by subtracting
  and then testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping

#: Gate 2.  Gross edge must be at least this multiple of the estimated cost or
#: the mechanism is dead on arrival.
COST_WALL_MULTIPLE = 3.0

#: Cost multipliers reported in every cost_sensitivity.json.
DEFAULT_COST_MULTIPLIERS = (1.0, 1.5, 2.0, 3.0)


def estimate_round_trip_cost_bps(
    maker_fee_bps: float,
    taker_fee_bps: float,
    spread_bps: float,
    slippage_bps: float,
    adverse_selection_bps: float,
    maker_ratio: float,
) -> float:
    """Round-trip cost of one leg, in basis points.

    ``maker_ratio`` is the fraction of notional expected to be filled passively.
    Fees are charged on both sides; spread, slippage and adverse selection are
    already round-trip quantities as measured.
    """
    if not 0.0 <= maker_ratio <= 1.0:
        raise ValueError("maker_ratio must be in [0, 1], got %r" % (maker_ratio,))
    fee_bps = maker_ratio * maker_fee_bps + (1.0 - maker_ratio) * taker_fee_bps
    return 2.0 * fee_bps + spread_bps + slippage_bps + adverse_selection_bps


def apply_costs(gross_bps: float, cost_bps: float) -> float:
    return gross_bps - cost_bps


def cost_x2(cost_bps: float) -> float:
    return 2.0 * cost_bps


def breakeven_capture(gross_bps: float, cost_bps: float) -> float:
    """Fraction of the gross move that must be captured to break even.

    Above 1.0 the mechanism needs more than the whole move it predicts, which is
    a cleaner way to say ``dead`` than a negative net number.
    """
    if gross_bps <= 0.0:
        return float("inf")
    return cost_bps / gross_bps


def passes_cost_wall(
    gross_bps: float, cost_bps: float, multiple: float = COST_WALL_MULTIPLE
) -> bool:
    return gross_bps >= multiple * cost_bps


def cost_sensitivity(
    gross_bps: float,
    cost_bps: float,
    multipliers: Iterable[float] = DEFAULT_COST_MULTIPLIERS,
) -> List[Dict[str, float]]:
    rows = []
    for m in multipliers:
        c = m * cost_bps
        rows.append(
            {
                "cost_multiplier": float(m),
                "cost_bps": float(c),
                "net_edge_bps": float(gross_bps - c),
                "breakeven_capture": float(breakeven_capture(gross_bps, c)),
            }
        )
    return rows


@dataclass(frozen=True)
class CostModel:
    """Cost assumptions of one mechanism, declared in ``spec.json``.

    Accepts either an explicit maker/taker split or a single ``fees_bps`` figure.
    ``n_legs`` exists because a market-neutral or cross-venue mechanism pays
    every leg: a cross-exchange test priced on one leg is not a test.
    """

    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    adverse_selection_bps: float = 0.0
    maker_ratio: float = 0.0
    n_legs: int = 1
    notes: str = ""

    @classmethod
    def from_spec(cls, payload: Mapping[str, Any]) -> "CostModel":
        data = dict(payload)
        if "fees_bps" in data:
            flat = float(data.pop("fees_bps"))
            # A single fees figure is read as the effective per-side fee for the
            # declared execution style; maker_ratio then has no further effect.
            data.setdefault("maker_fee_bps", flat)
            data.setdefault("taker_fee_bps", flat)
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError("unknown cost_model keys: %s" % ", ".join(unknown))
        return cls(**{k: data[k] for k in data})

    def round_trip_bps(self) -> float:
        per_leg = estimate_round_trip_cost_bps(
            maker_fee_bps=self.maker_fee_bps,
            taker_fee_bps=self.taker_fee_bps,
            spread_bps=self.spread_bps,
            slippage_bps=self.slippage_bps,
            adverse_selection_bps=self.adverse_selection_bps,
            maker_ratio=self.maker_ratio,
        )
        if self.n_legs < 1:
            raise ValueError("n_legs must be >= 1, got %r" % (self.n_legs,))
        return per_leg * float(self.n_legs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "maker_fee_bps": self.maker_fee_bps,
            "taker_fee_bps": self.taker_fee_bps,
            "spread_bps": self.spread_bps,
            "slippage_bps": self.slippage_bps,
            "adverse_selection_bps": self.adverse_selection_bps,
            "maker_ratio": self.maker_ratio,
            "n_legs": self.n_legs,
            "round_trip_bps": self.round_trip_bps(),
            "notes": self.notes,
        }
