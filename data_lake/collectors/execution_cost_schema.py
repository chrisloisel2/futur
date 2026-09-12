#!/usr/bin/env python3
"""
execution_cost_schema.py -- fonctions PURES : ce qu'est un cout d'execution, d'ou il vient, et ce qu'il
autorise a dire. Aucun reseau, aucun ordre, aucun signal.

Une regle tient tout le module : **un cout porte sa provenance**. Un chiffre publie n'est pas un chiffre
constate sur le compte, et un chiffre declare dans une spec n'est ni l'un ni l'autre. Un mur de cout
calcule a partir d'une valeur declaree ne peut pas conclure ; il peut seulement dire ce qu'il faudrait mesurer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Par ordre de force decroissante. Une comparaison H2/H3 n'est "confirmee" qu'a partir de account_actual.
PROVENANCE = ("account_actual", "official_published", "third_party", "declared")
PROVENANCE_RANK = {p: i for i, p in enumerate(PROVENANCE)}

FEE_RECORD = ["venue", "market_type", "symbol", "maker_bps", "taker_bps", "provenance", "source", "as_of", "fee_tier", "bnb_discount_applied", "raw_hash"]
CONSTRAINT_RECORD = ["venue", "market_type", "symbol", "status", "tick_size", "step_size", "min_qty", "min_notional", "max_market_qty",
                     "market_take_bound", "max_move_order_limit", "liquidation_fee", "maint_margin_percent", "required_margin_percent",
                     "max_leverage", "margin_borrowable", "source", "as_of"]
COST_RECORD = ["market_type", "fee_bps_per_side", "fee_provenance", "spread_bps", "spread_provenance", "slippage_bps", "slippage_provenance",
               "round_trip_bps", "wall_3x_bps", "weakest_provenance", "is_measured", "note"]


class SchemaError(ValueError):
    pass


def validate(kind: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    fields = {"fee": FEE_RECORD, "constraint": CONSTRAINT_RECORD, "cost": COST_RECORD}.get(kind)
    if fields is None:
        raise SchemaError("unknown record kind %r" % kind)
    missing = [f for f in fields if f not in rec]
    if missing:
        raise SchemaError("%s: missing fields %s" % (kind, missing))
    if kind in ("fee", "cost"):
        p = rec.get("provenance") or rec.get("weakest_provenance")
        if p not in PROVENANCE:
            raise SchemaError("%s: provenance %r not in %s" % (kind, p, PROVENANCE))
    return rec


def weakest(provenances: List[str]) -> str:
    """Une chaine de cout ne vaut que son maillon le plus faible."""
    known = [p for p in provenances if p in PROVENANCE_RANK]
    if not known:
        raise SchemaError("no known provenance in %r" % (provenances,))
    return max(known, key=lambda p: PROVENANCE_RANK[p])


def round_trip(fee_bps_per_side: float, spread_bps: float, slippage_bps: float, n_legs: int = 1) -> float:
    """Aller-retour d'une jambe : deux fois les frais, plus le spread et le glissement (deja en aller-retour)."""
    if n_legs < 1:
        raise SchemaError("n_legs must be >= 1")
    return (2.0 * fee_bps_per_side + spread_bps + slippage_bps) * n_legs


def build_cost(market_type: str, fee_bps_per_side: float, fee_provenance: str, spread_bps: float, spread_provenance: str,
               slippage_bps: float, slippage_provenance: str, n_legs: int = 1, note: str = "") -> Dict[str, Any]:
    rt = round_trip(fee_bps_per_side, spread_bps, slippage_bps, n_legs)
    w = weakest([fee_provenance, spread_provenance, slippage_provenance])
    return validate("cost", {"market_type": market_type, "fee_bps_per_side": fee_bps_per_side, "fee_provenance": fee_provenance,
                             "spread_bps": spread_bps, "spread_provenance": spread_provenance, "slippage_bps": slippage_bps,
                             "slippage_provenance": slippage_provenance, "round_trip_bps": round(rt, 4), "wall_3x_bps": round(3.0 * rt, 4),
                             "weakest_provenance": w, "is_measured": w in ("account_actual", "official_published"), "note": note})


def compare_to_assumption(cost: Dict[str, Any], assumed_round_trip_bps: float, tol_bps: float = 0.5) -> Dict[str, Any]:
    """Le cout suppose par une hypothese tient-il ? Trois reponses, jamais un verdict alpha :
    confirmed / contradicted / unknown (quand la chaine repose sur du declare)."""
    diff = cost["round_trip_bps"] - assumed_round_trip_bps
    if not cost["is_measured"]:
        status = "unknown"
    elif abs(diff) <= tol_bps:
        status = "confirmed"
    else:
        status = "contradicted"
    return {"assumed_round_trip_bps": assumed_round_trip_bps, "measured_round_trip_bps": cost["round_trip_bps"],
            "difference_bps": round(diff, 4), "status": status, "weakest_provenance": cost["weakest_provenance"],
            "note": "a cost chain that rests on a declared number cannot confirm or contradict anything" if status == "unknown" else ""}


def realised_slippage_bps(fill_price: float, reference_price: float, side: str) -> float:
    """Glissement constate d'une execution : positif = paye plus cher que la reference. Pure arithmetique
    sur une execution DEJA passee (jamais une prevision, jamais un signal)."""
    if reference_price <= 0 or fill_price <= 0:
        raise SchemaError("prices must be positive")
    s = 1.0 if side.lower() in ("buy", "long") else -1.0
    return round(s * (fill_price - reference_price) / reference_price * 1e4, 4)
