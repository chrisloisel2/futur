#!/usr/bin/env python3
"""
TRUE_FIRST_LISTING_FORWARD_ONLY -- le perpetuel Binance est le premier marche collecte, partout.

Six evenements historiques. Aucun historique ne suffira : cette hypothese ne peut etre regardee que vers
l'avant, quand la tape d'etat de marche (P4) aura enregistre assez de vraies naissances. Ce module REFUSE
tout verdict historique par construction.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MECHANISM_ID = "true_first_listing_forward_only_v1"
FAMILY = "news"
STATUS = "FORWARD_ONLY"
POPULATION = "TRUE_BINANCE_PERP_FIRST"
HISTORICAL_EVENTS = 6
MIN_EVENTS_BEFORE_FIRST_LOOK = 80          # meme seuil que la porte P10 ; a 1 546 bps de dispersion il en faudrait bien plus
ASSUMED_SIGMA_BPS = 1546.0                 # dispersion par evenement mesuree au regard seq 8

QUESTIONS = [
    "How many true first listings per year does Binance produce? (P9: 6 of 174 launches over 2023-2026 were first anywhere; the rate is the binding constraint)",
    "What is the minimal market-state tape for such an event: first book, first trade, first mark/index/OI/funding, tick L2 for 6 h? (P4 records exactly this on trigger)",
    "How many events are needed before a first look, at the observed dispersion, to detect 30 bps? (see required_events)",
    "What capacity conditions must be imposed at entry: CAPACITY_OK at +15 min within 20 bps for 1 000 USDT, or the event is paper-only.",
]
REQUIRED_DATA = ["P4 market_state_tape captures with trigger new_perp_listing (live)", "P9 cross-venue precedence at the time of each future launch (to certify 'first anywhere')",
                 "P7 announcement body for the announced opening time", "account_actual fees"]
EXCLUSIONS = ["any event with a dated listing on another venue before the Binance launch (it belongs to OTHER_VENUE_FIRST)",
              "any event with an undated market elsewhere (UNKNOWN_PRECEDENCE): 'first' is not certified",
              "any historical event: the six known ones are description material, never a sample"]
FAILURE_MODES = [
    "Count: at ~2 true first listings per year the sample needed for a 30 bps effect takes decades; the honest expectation is that this hypothesis is never decided.",
    "Certification drift: 'first anywhere' depends on the venue clients' coverage; adding a venue can move an event out of this population after the fact.",
    "The pooled H2 result (seq 8) is not evidence for this population: 6 events were inside a 174-event average.",
]


def required_events(effect_bps: float = 30.0, sigma_bps: float = ASSUMED_SIGMA_BPS, t_threshold: float = 2.33) -> int:
    """n tel que sigma / sqrt(n) * t <= effect : pure arithmetique de puissance, pas un resultat."""
    import math
    return int(math.ceil((t_threshold * sigma_bps / effect_bps) ** 2))


def spec() -> Dict[str, Any]:
    return {
        "mechanism_id": MECHANISM_ID,
        "hypothesis": "When a Binance USDS-M perpetual is the first market anywhere for an asset, the price over the primary horizon after the entry window moves by more than 30 bps gross in the direction fixed before the look, because the opening book is built with no external reference and the first participants set the price under leverage.",
        "economic_reason": "With no prior market there is no arbitrage anchor: market makers quote wide, leveraged retail enters at the open, and the first hours are a price-discovery process rather than a repricing. The payer is the participant forced to trade at the open with no reference. Whether the discovery overshoots or underreacts is unknown and cannot be learned from six historical events.",
        "data_sources": ["market_state_tape_new_perp_listing_windows", "official_event_tape", "cross_venue_precedence_at_launch", "ACCOUNT_EXECUTION_REALITY"],
        "universe": ["FUTURE_TRUE_BINANCE_PERP_FIRST_LAUNCHES"],
        "timeframe": "1m", "horizon": "6h", "side_mode": "long_short",
        "entry_rule": {"population": "TRUE_BINANCE_PERP_FIRST certified at launch time by the venue clients", "entry_at": "tradable_start + 15 min", "capacity_gate": "CAPACITY_OK at the entry window within 20 bps for 1 000 USDT", "direction": "fixed before the look", "min_bps_expected": 30},
        "exit_rule": {"exit_at": "entry + 6h", "no_stop": True},
        "cost_model": {"maker_fee_bps": 2.0, "taker_fee_bps": 5.0, "maker_ratio": 0.0, "spread_bps": 8.0, "slippage_bps": 6.0, "adverse_selection_bps": 0.0, "n_legs": 1, "notes": "declared; replaced by measured values at the look"},
        "validation_window": {"start": "2026-09-12", "end": "2036-09-12", "nature": "FORWARD ONLY: no historical window exists; the six known events are excluded by rule"},
        "placebo_tests": ["same_event_shifted_minus_48h", "synthetic_positive_control"],
        "declustering_rule": {"method": "cluster_by_launch_day", "window_seconds": 86400, "note": "one cluster per launch day"},
        "multiplicity_family": FAMILY,
        "kill_criteria": {"gross_bps_lt": 30, "gross_lt_cost_x": 3.0, "placebo_gte_gross": True},
        "promotion_criteria": {"n_min": MIN_EVENTS_BEFORE_FIRST_LOOK, "gross_bps_min": 30, "gross_over_cost_min": 3.0, "t_min_one_sided_family_bonferroni": "threshold_t(family size at seal)", "capacity_ok_share_min": 0.80, "fee_provenance_required": "account_actual", "verdict_if_all_met": "FORWARD_SEAL_REQUIRED"},
        "sensitivities": {"horizon": ["60m", "24h"]},
        "declared_data_latency_ms": 60000,
        "notes": "FORWARD_ONLY. Defined by P11. Historical verdicts are refused by the module itself. First look not before %d certified events have been captured live." % MIN_EVENTS_BEFORE_FIRST_LOOK,
    }


def select(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Les evenements historiques TRUE_BINANCE_PERP_FIRST sont DESCRITS, jamais echantillonnes."""
    hist = [r for r in rows if r.get("population") == POPULATION]
    return {"mechanism_id": MECHANISM_ID, "historical_described": hist, "n_historical": len(hist), "sample": [], "n_sample": 0,
            "note": "historical events are excluded from any sample by rule; the sample is built forward by the P4 tape", "no_return_computed": True}


def verdict(events: Optional[List[Dict[str, Any]]] = None, *_: Any, **__: Any) -> None:
    n = len(events or [])
    raise RuntimeError("%s is FORWARD_ONLY: a historical verdict is refused (%d event(s) offered; %d certified live events required before any look)." % (MECHANISM_ID, n, MIN_EVENTS_BEFORE_FIRST_LOOK))
