#!/usr/bin/env python3
"""
h2_population_labels.py -- fonctions PURES : a quelle POPULATION appartient un lancement H2, et ce qui le BLOQUE.

Deux axes, jamais confondus :
  population  : la structure du marche (qui a cote l'actif en premier, avec quelle avance)
  blockers    : ce qui manque pour qu'un test serieux soit possible (horodatage, capacite, cout, fournisseur)

"H2" n'est pas une population. C'est une union de populations que le regard seq 8 a moyennees.
Aucun prix, aucun retour, aucun verdict.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

POP_TRUE_FIRST = "TRUE_BINANCE_PERP_FIRST"
POP_MEXC = "MEXC_FIRST"
POP_OKX = "OKX_FIRST"
POP_BYBIT = "BYBIT_FIRST"
POP_KUCOIN = "KUCOIN_FIRST"
POP_OTHER = "OTHER_VENUE_FIRST"
POP_GATE_UNDATED = "GATE_FIRST_UNKNOWN_DATE"
POP_BINANCE_SPOT = "BINANCE_SPOT_FIRST"
POP_UNKNOWN = "UNKNOWN_PRECEDENCE"
POPULATIONS = (POP_TRUE_FIRST, POP_MEXC, POP_OKX, POP_BYBIT, POP_KUCOIN, POP_OTHER, POP_GATE_UNDATED, POP_BINANCE_SPOT, POP_UNKNOWN)

BLK_TIMESTAMP = "BAD_TIMESTAMP"
BLK_CAPACITY = "NO_CAPACITY"
BLK_COST = "UNKNOWN_EXECUTION_COST"
BLK_PROVIDER = "PROVIDER_NEEDED"
BLOCKERS = (BLK_TIMESTAMP, BLK_PROVIDER, BLK_CAPACITY, BLK_COST)      # ordre de priorite pour la classe unique
ALL_CLASSES = POPULATIONS + BLOCKERS

VENUE_POP = {"mexc": POP_MEXC, "okx": POP_OKX, "bybit": POP_BYBIT, "kucoin": POP_KUCOIN}
LEAD_BUCKETS = ("< 1h", "1h-24h", "1d-7d", "7d-30d", "30d-180d", ">180d", "unknown")


def lead_bucket(lead_days: Optional[float]) -> str:
    if lead_days is None:
        return "unknown"
    h = lead_days * 24.0
    if h < 1:
        return "< 1h"
    if h < 24:
        return "1h-24h"
    if lead_days < 7:
        return "1d-7d"
    if lead_days < 30:
        return "7d-30d"
    if lead_days < 180:
        return "30d-180d"
    return ">180d"


def population(precedence: Optional[str], first_venue: Optional[str], undated_venues: Optional[List[str]], binance_spot_before: bool) -> str:
    """precedence : classification P9 (OTHER_VENUE_FIRST / BINANCE_FIRST / NO_OTHER_VENUE / UNKNOWN_PRECEDENCE / None)."""
    if binance_spot_before:
        return POP_BINANCE_SPOT
    if precedence == "OTHER_VENUE_FIRST":
        return VENUE_POP.get((first_venue or "").lower(), POP_OTHER)
    if precedence in ("BINANCE_FIRST", "NO_OTHER_VENUE"):
        return POP_TRUE_FIRST
    if precedence == "UNKNOWN_PRECEDENCE" and undated_venues and set(v.lower() for v in undated_venues) == {"gate"}:
        return POP_GATE_UNDATED
    return POP_UNKNOWN


def blockers(ev: Dict[str, Any], require_actual_fees: bool = True) -> List[str]:
    """Ce qui manque, dans l'ordre de priorite. Un champ absent est un manque, jamais une valeur favorable."""
    out: List[str] = []
    if ev.get("timestamp_bad"):
        out.append(BLK_TIMESTAMP)
    if ev.get("provider_needed"):
        out.append(BLK_PROVIDER)
    if not ev.get("capacity_measured"):
        out.append(BLK_CAPACITY)
    if require_actual_fees and not ev.get("actual_fee_known"):
        out.append(BLK_COST)
    return out


def label(ev: Dict[str, Any], require_actual_fees: bool = True) -> Dict[str, Any]:
    pop = population(ev.get("venue_precedence"), ev.get("first_venue"), ev.get("undated_venues"), bool(ev.get("binance_spot_before")))
    blk = blockers(ev, require_actual_fees)
    return {"population": pop, "blockers": blk, "class": blk[0] if blk else pop, "clean": not blk,
            "lead_time_bucket": lead_bucket(ev.get("lead_days")),
            "excluded_reason": "; ".join(blk) if blk else None}


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    from collections import Counter
    pops = Counter(r["population"] for r in rows)
    only = lambda b: sum(1 for r in rows if r["blockers"] == [b])
    clean_if_lifted = Counter(r["population"] for r in rows if all(b in (BLK_COST, BLK_CAPACITY) for b in r["blockers"]))
    return {"n": len(rows), "by_population": {p: pops.get(p, 0) for p in POPULATIONS if pops.get(p)},
            "by_class": dict(Counter(r["class"] for r in rows)), "by_lead_bucket": dict(Counter(r["lead_time_bucket"] for r in rows)),
            "clean_now": sum(1 for r in rows if r["clean"]),
            "answers": {"1_true_first_listings": pops.get(POP_TRUE_FIRST, 0), "2_mexc_first": pops.get(POP_MEXC, 0),
                        "3_other_venue_first_excluding_mexc": sum(pops.get(p, 0) for p in (POP_OKX, POP_BYBIT, POP_KUCOIN, POP_OTHER)),
                        "4_bad_timestamps": sum(1 for r in rows if BLK_TIMESTAMP in r["blockers"]),
                        "5_blocked_only_by_cost": only(BLK_COST), "6_blocked_only_by_capacity": only(BLK_CAPACITY),
                        "6b_blocked_by_cost_and_capacity_only": sum(1 for r in rows if set(r["blockers"]) == {BLK_COST, BLK_CAPACITY}),
                        "7_clean_per_population_if_cost_and_capacity_lifted": dict(clean_if_lifted)}}
