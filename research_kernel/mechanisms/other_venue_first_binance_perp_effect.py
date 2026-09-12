#!/usr/bin/env python3
"""
OTHER_VENUE_FIRST_BINANCE_PERP_EFFECT -- l'arrivee de Binance perp sur un actif deja price ailleurs.

Ce n'est pas une naissance de marche. Le prix existe deja sur au moins une place datee ; l'evenement est
l'arrivee d'une nouvelle infrastructure (levier, acces au short, liquidite Binance) sur un actif qui a un
historique. Mecanisme seulement : aucun retour calcule, aucun verdict, aucun budget.
"""
from __future__ import annotations

from typing import Any, Dict, List

MECHANISM_ID = "other_venue_first_binance_perp_effect_v1"
FAMILY = "news"
STATUS = "RESEARCH_ONLY"
POPULATION = "OTHER_VENUE_FIRST"
REQUIRED_POPULATIONS = ("MEXC_FIRST", "OKX_FIRST", "BYBIT_FIRST", "KUCOIN_FIRST", "OTHER_VENUE_FIRST")
EXCLUDED_POPULATIONS = ("TRUE_BINANCE_PERP_FIRST", "BINANCE_SPOT_FIRST", "UNKNOWN_PRECEDENCE", "GATE_FIRST_UNKNOWN_DATE")

QUESTIONS = [
    "Does the Binance perpetual opening change the asset's liquidity (depth within 20 bps, effective spread) relative to its state on the first venue?",
    "Does it change realised volatility relative to the pre-Binance 7-day volatility on the first venue?",
    "Is there a migration effect: does volume move from the first venue to Binance, and over what horizon?",
    "Does any effect depend on the external lead time (hours, days, months) between the first listing and the Binance launch?",
    "Does it depend on MEXC having been the first venue, versus OKX / Bybit / KuCoin?",
    "Does it depend on the pre-Binance pump level (pre_binance_pump_score) on the first venue?",
]
REQUIRED_DATA = ["H2_CAUSAL_POPULATION_MATRIX (population, lead time)", "H2_DEPTH_CAPACITY_FEATURES (capacity status per window)",
                 "MEXC_PRE_BINANCE_FEATURES or the equivalent for the first venue (pre-t0 state)", "Vision window archives (mark, index, premium, trades) for the Binance side",
                 "ACCOUNT_EXECUTION_REALITY with account_actual fees (currently unknown)"]
EXCLUSIONS = [
    "events whose population is TRUE_BINANCE_PERP_FIRST (no external reference: a different mechanism)",
    "events whose venue precedence is UNKNOWN_PRECEDENCE or GATE_FIRST_UNKNOWN_DATE (the premise cannot be checked)",
    "events flagged BAD_TIMESTAMP (announced opening and first traded bar disagree by more than 15 min)",
    "events flagged PROVIDER_NEEDED (no free depth or index reference for the launch day)",
    "events whose capacity status at the entry window is NO_DEPTH or BAD_BOOK (executability unmeasured)",
    "events whose first-venue pre-Binance tape is not_collected (the conditioning state does not exist)",
]
FAILURE_MODES = [
    "The effect is a re-labelled H2 fade: if the pooled result reappears with the same dispersion, the population split added nothing.",
    "Reference contamination: measuring against BTC instead of the first venue's own price re-creates the seq 8 error.",
    "Lead-time confounding: long-lead assets are old tokens, short-lead assets are fresh launches; an effect that tracks lead time may be an age effect.",
    "MEXC dominance: 113 of 137 events are MEXC-first; a result on the pooled OTHER_VENUE_FIRST population is mostly a MEXC result and must be stated as such.",
    "Capacity illusion: an effect that only exists where DEPTH_TOO_THIN cannot be executed and must be reported as paper-only.",
    "Survivorship of the first venue: assets delisted from MEXC before the Binance launch are missing from the tape.",
]


def spec() -> Dict[str, Any]:
    """Une spec acceptable par research_kernel.mechanism_spec. Les valeurs numeriques sont des primaires plus au plus deux sensibilites."""
    return {
        "mechanism_id": MECHANISM_ID,
        "hypothesis": "When Binance opens a USDS-M perpetual on an asset that already has a dated market on another venue, the asset's price on Binance, measured against the first venue's own price, moves by more than 30 bps gross over the primary horizon after the entry window, because the arrival of leveraged access and short access changes who can trade the asset and at what size.",
        "economic_reason": "The first venue priced the asset without leverage or with thin depth; Binance brings leveraged retail, index inclusion and a short path. The participants forced to act are the holders on the first venue who could not short, and the market makers who must quote a new book against a known external price. The mispricing is bounded by the cost of arbitrage between the two venues, which the pre-Binance tape and the Binance depth archives make measurable.",
        "data_sources": ["H2_CAUSAL_POPULATION_MATRIX", "H2_DEPTH_CAPACITY_FEATURES", "MEXC_PRE_BINANCE_FEATURES", "binance_vision_um_klines_1m", "binance_vision_um_aggTrades", "ACCOUNT_EXECUTION_REALITY"],
        "universe": ["H2_OTHER_VENUE_FIRST_POPULATION"],
        "timeframe": "1m", "horizon": "6h", "side_mode": "long_short",
        "entry_rule": {"population": "OTHER_VENUE_FIRST (MEXC_FIRST, OKX_FIRST, BYBIT_FIRST, KUCOIN_FIRST)", "entry_at": "tradable_start + 15 min", "reference_price": "first venue's own price at the same minute, not BTC",
                       "capacity_gate": "capacity_status at the entry window in (CAPACITY_OK, UNKNOWN); DEPTH_TOO_THIN and SPREAD_TOO_WIDE reported as paper-only", "direction": "fixed in the preregistration, not chosen after the look", "min_bps_expected": 30},
        "exit_rule": {"exit_at": "entry + 6h (open of the first bar)", "no_stop": True},
        "cost_model": {"maker_fee_bps": 2.0, "taker_fee_bps": 5.0, "maker_ratio": 0.0, "spread_bps": 8.0, "slippage_bps": 6.0, "adverse_selection_bps": 0.0, "n_legs": 1,
                       "notes": "declared; must be replaced by account_actual fee and by the depth-derived spread and slippage before any promotion"},
        "validation_window": {"start": "2023-01-01", "end": "2026-09-10", "nature": "historical population frozen by P10/P11; not run; a future preregistration names one sub-population"},
        "placebo_tests": ["same_event_shifted_minus_48h", "synthetic_positive_control"],
        "declustering_rule": {"method": "cluster_by_launch_day", "window_seconds": 86400, "note": "several launches on one day are one cluster"},
        "multiplicity_family": FAMILY,
        "kill_criteria": {"gross_bps_lt": 30, "gross_lt_cost_x": 3.0, "placebo_gte_gross": True, "pre_launch_drift_gte_gross": True},
        "promotion_criteria": {"gross_bps_min": 30, "gross_over_cost_min": 3.0, "t_min_one_sided_family_bonferroni": "threshold_t(family size at seal)", "n_eff_min": 30, "top1_share_max": 0.10,
                               "capacity_ok_share_min": 0.80, "fee_provenance_required": "account_actual", "verdict_if_all_met": "FORWARD_SEAL_REQUIRED"},
        "sensitivities": {"horizon": ["60m", "24h"]},
        "declared_data_latency_ms": 60000,
        "notes": "RESEARCH_ONLY. Defined by P11, never run. Excludes TRUE_BINANCE_PERP_FIRST by rule. Budget 0; no look is authorised by this definition.",
    }


def select(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Filtre pur sur les lignes de la matrice causale : qui entre, qui sort, et pourquoi. Aucune donnee de prix."""
    kept, excluded = [], []
    for r in rows:
        pop = r.get("population"); why = None
        if pop in EXCLUDED_POPULATIONS or pop not in REQUIRED_POPULATIONS:
            why = "population %s excluded" % pop
        elif "BAD_TIMESTAMP" in (r.get("blockers") or []):
            why = "BAD_TIMESTAMP"
        elif "PROVIDER_NEEDED" in (r.get("blockers") or []):
            why = "PROVIDER_NEEDED"
        elif r.get("capacity_status") in ("NO_DEPTH", "BAD_BOOK", "NOT_COMPUTED", None):
            why = "capacity unmeasured (%s)" % r.get("capacity_status")
        (excluded if why else kept).append({**r, "exclusion_reason": why} if why else r)
    return {"mechanism_id": MECHANISM_ID, "kept": kept, "excluded": excluded, "n_kept": len(kept), "n_excluded": len(excluded), "no_return_computed": True}


def verdict(*_: Any, **__: Any) -> None:
    raise RuntimeError("%s is a definition: no verdict can be produced from it. A verdict requires a sealed preregistration and budget > 0." % MECHANISM_ID)
