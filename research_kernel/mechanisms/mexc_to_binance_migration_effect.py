#!/usr/bin/env python3
"""
MEXC_TO_BINANCE_MIGRATION_EFFECT -- l'actif existe d'abord sur MEXC, puis arrive sur Binance perp.

Sous-population de OTHER_VENUE_FIRST (113 des 137 evenements dates), avec une tape pre-Binance propre a
MEXC. La dynamique MEXC AVANT Binance est l'etat conditionnant ; ce que fait Binance APRES n'est pas
calcule ici. Mecanisme seulement : aucun retour, aucun verdict, aucun budget.
"""
from __future__ import annotations

from typing import Any, Dict, List

MECHANISM_ID = "mexc_to_binance_migration_effect_v1"
FAMILY = "news"
STATUS = "RESEARCH_ONLY"
POPULATION = "MEXC_FIRST"

QUESTIONS = [
    "Does a pre-Binance pump on MEXC (high pre_binance_pump_score) precede a fade on Binance?",
    "Does an illiquid MEXC market (low pre_binance_liquidity_proxy) precede price discovery on Binance rather than a fade?",
    "Does an explosion of MEXC volume in the last 24 h before the launch mark anticipation of the Binance listing?",
    "Is the effect different for long MEXC lead times (months) versus short ones (hours to days)?",
    "Does the MEXC exhaustion score (drawdown from the 7-day high plus volume drying up) carry information about the post-Binance behaviour?",
]
REQUIRED_DATA = ["MEXC_PRE_BINANCE_FEATURES (pre-t0 state on MEXC, daily and hourly)", "H2_CAUSAL_POPULATION_MATRIX (MEXC_FIRST rows, lead time)",
                 "H2_DEPTH_CAPACITY_FEATURES (Binance book at the entry window)", "Vision window archives on the Binance side", "account_actual fees"]
EXCLUSIONS = [
    "every population other than MEXC_FIRST",
    "MEXC_FIRST events whose MEXC pre-Binance tape is not_collected (no conditioning state)",
    "events whose MEXC market at t0 is the perpetual only when the question is about spot discovery, and vice versa (state the market in the preregistration)",
    "events flagged BAD_TIMESTAMP or PROVIDER_NEEDED",
    "events whose Binance capacity at the entry window is NO_DEPTH or BAD_BOOK",
    "the conditioning variable is chosen BEFORE the look; a conditioning chosen after seeing Binance returns is a second look",
]
FAILURE_MODES = [
    "Conditioning on a pre-Binance feature and then choosing the horizon after the look: that is data mining, and the family bar does not protect against it.",
    "MEXC data quality: wash trading on MEXC inflates pre_binance_volume_* and the pump score; a result driven by inflated volume is a MEXC artefact.",
    "5-minute history is absent on MEXC for older launches: any 6-hour pre-Binance feature is only available for recent events, which biases the sample toward 2025-2026.",
    "Survivorship: assets delisted from MEXC before the Binance launch have no tape and are silently absent.",
    "The effect may be the OTHER_VENUE_FIRST effect with a MEXC label: it must be compared to OKX/Bybit-first events, not tested alone.",
    "Small conditioning bins: splitting 113 events by pump score and lead time yields cells of 10-20 events, far below the dispersion seen in seq 8.",
]


def spec() -> Dict[str, Any]:
    return {
        "mechanism_id": MECHANISM_ID,
        "hypothesis": "When an asset that first traded on MEXC arrives on a Binance USDS-M perpetual, the Binance price measured against the MEXC price over the primary horizon after the entry window moves by more than 30 bps gross in the direction fixed by the preregistered conditioning on the MEXC pre-launch state, because MEXC positioning built without a short path is unwound once Binance provides one.",
        "economic_reason": "MEXC lists early and thin; holders accumulate without leverage or a short path; the Binance perpetual is the first venue where the position can be hedged or attacked at size. The payer is the MEXC holder who bought the pre-listing run-up and the leveraged Binance entrant of the first minutes. The size of the unwind should scale with what was built on MEXC before, which the pre-Binance tape measures.",
        "data_sources": ["MEXC_PRE_BINANCE_FEATURES", "H2_CAUSAL_POPULATION_MATRIX", "H2_DEPTH_CAPACITY_FEATURES", "binance_vision_um_klines_1m", "mexc_spot_klines", "ACCOUNT_EXECUTION_REALITY"],
        "universe": ["H2_MEXC_FIRST_POPULATION"],
        "timeframe": "1m", "horizon": "6h", "side_mode": "long_short",
        "entry_rule": {"population": "MEXC_FIRST with a collected pre-Binance tape", "entry_at": "tradable_start + 15 min", "reference_price": "MEXC price at the same minute",
                       "conditioning": "one pre-Binance feature named in the preregistration (pump score, liquidity proxy, lead-time bucket or exhaustion score), never two", "direction": "fixed per conditioning bin before the look", "min_bps_expected": 30},
        "exit_rule": {"exit_at": "entry + 6h", "no_stop": True},
        "cost_model": {"maker_fee_bps": 2.0, "taker_fee_bps": 5.0, "maker_ratio": 0.0, "spread_bps": 8.0, "slippage_bps": 6.0, "adverse_selection_bps": 0.0, "n_legs": 1,
                       "notes": "declared; replaced by account_actual fee and depth-derived spread/slippage before any promotion"},
        "validation_window": {"start": "2023-01-01", "end": "2026-09-10", "nature": "historical MEXC-first population frozen by P11; not run"},
        "placebo_tests": ["same_event_shifted_minus_48h", "conditioning_permuted_across_events", "synthetic_positive_control"],
        "declustering_rule": {"method": "cluster_by_launch_day", "window_seconds": 86400, "note": "one cluster per launch day"},
        "multiplicity_family": FAMILY,
        "kill_criteria": {"gross_bps_lt": 30, "gross_lt_cost_x": 3.0, "placebo_gte_gross": True, "conditioning_permutation_gte_gross": True},
        "promotion_criteria": {"gross_bps_min": 30, "gross_over_cost_min": 3.0, "t_min_one_sided_family_bonferroni": "threshold_t(family size at seal)", "n_eff_min": 30, "top1_share_max": 0.10,
                               "capacity_ok_share_min": 0.80, "fee_provenance_required": "account_actual", "verdict_if_all_met": "FORWARD_SEAL_REQUIRED"},
        "sensitivities": {"horizon": ["60m", "24h"]},
        "declared_data_latency_ms": 60000,
        "notes": "RESEARCH_ONLY. Defined by P11, never run. Sub-population of other_venue_first_binance_perp_effect_v1: the two are one family and a look on either raises the bar for both.",
    }


def select(rows: List[Dict[str, Any]], tape_status: Dict[str, str]) -> Dict[str, Any]:
    """tape_status : event_id -> statut de la tape MEXC pre-Binance (collected / partial / not_collected)."""
    kept, excluded = [], []
    for r in rows:
        why = None
        if r.get("population") != POPULATION:
            why = "population %s is not MEXC_FIRST" % r.get("population")
        elif tape_status.get(r.get("event_id")) not in ("collected", "partial"):
            why = "MEXC pre-Binance tape %s" % tape_status.get(r.get("event_id"), "absent")
        elif "BAD_TIMESTAMP" in (r.get("blockers") or []) or "PROVIDER_NEEDED" in (r.get("blockers") or []):
            why = "BAD_TIMESTAMP or PROVIDER_NEEDED"
        elif r.get("capacity_status") in ("NO_DEPTH", "BAD_BOOK", "NOT_COMPUTED", None):
            why = "capacity unmeasured (%s)" % r.get("capacity_status")
        (excluded if why else kept).append({**r, "exclusion_reason": why} if why else r)
    return {"mechanism_id": MECHANISM_ID, "kept": kept, "excluded": excluded, "n_kept": len(kept), "n_excluded": len(excluded), "no_return_computed": True}


def verdict(*_: Any, **__: Any) -> None:
    raise RuntimeError("%s is a definition: no verdict can be produced from it." % MECHANISM_ID)
