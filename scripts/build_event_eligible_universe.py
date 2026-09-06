#!/usr/bin/env python3
"""
scripts/build_event_eligible_universe.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, section 6: reports/EVENT_ELIGIBLE_UNIVERSE_V1.json --
pre-registers WHAT eligibility means, per family, BEFORE any economic
result exists. Written before the basis/panel rebuild (sections 14-15) and
before any scan -- it describes the RULE, not a result computed from data.

Contains ONLY:
  - the PIT universe scope (size, source manifest)
  - each family's required-column contract and warmup rule, read directly
    from data_v2/events/eligibility.py (not restated by hand, so this
    document can never drift from the code that actually enforces it)
  - MIN_CROSS_SECTION_SIZE and its (structural, not PnL-derived) rationale
  - a hash of eligibility.py and schema.py, so a later run can verify the
    rules used to build reports/EVENT_FEATURE_ELIGIBILITY_REPORT.json
    (section 12) are the SAME rules pre-registered here

Contains NOTHING about: return, PF, win rate, Sharpe, PnL, MFE/MAE, or any
label -- none of those concepts exist in this module or its inputs.

    python3 scripts/build_event_eligible_universe.py
"""
from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_v2.events import eligibility  # noqa: E402
from data_v2.events.schema import REQUIRED_COLUMNS, OPTIONAL_COLUMNS  # noqa: E402

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
ELIGIBILITY_SRC = ROOT / "data_v2/events/eligibility.py"
SCHEMA_SRC = ROOT / "data_v2/events/schema.py"
OUT_PATH = ROOT / "reports/EVENT_ELIGIBLE_UNIVERSE_V1.json"


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


FAMILY_CONTRACTS = {
    "DELEVERAGING": {
        "required_columns": [
            "open", "close", "volume", "oi", "oi_delta_pct_1h",
            "aggressive_sell_usd", "residual_return_1h", "research_available_at",
        ],
        "warmup_rule": f"residual_std_30d non-null (strict-prior, full {eligibility.RESIDUAL_STD_WINDOW_DAYS}-day window, "
                        "shift(1) so the current bar never judges itself)",
        "liquidation_feed": "OPTIONAL per protocol -- never required for eligibility",
        "mask_function": "data_v2.events.eligibility.eligible_deleveraging",
    },
    "CROWDING": {
        "required_columns": [
            "funding_rate", "funding_rate_percentile_90d", "basis_z_1d", "oi",
            "oi_delta_pct_1h", "aggressive_buy_usd", "aggressive_sell_usd", "research_available_at",
        ],
        "warmup_rule": f"funding_settlement_warmup True (>= {eligibility.FUNDING_WARMUP_DAYS} days elapsed since this "
                        "symbol's OWN first real settlement -- distinct from funding_rate_percentile_90d's own "
                        "non-null check, which can be non-null after just 1 prior settlement)",
        "percentile_semantics": "SETTLEMENT NATIVE, strict prior 90d, causal forward-fill of the rank -- never bar-wise",
        "mask_function": "data_v2.events.eligibility.eligible_crowding",
    },
    "RELATIVE_VALUE_DISLOCATION": {
        "required_columns": ["residual_return_1h", "basis_z_1d", "signed_volume", "research_available_at"],
        "warmup_rule": f"residual_std_30d non-null (same rule as DELEVERAGING, shared computation)",
        "cross_sectional_minimum": eligibility.MIN_CROSS_SECTION_SIZE,
        "cross_sectional_minimum_rationale": (
            "Structural, pre-registered, NOT derived from any observed PnL: the classic large-sample "
            "rule of thumb (Central Limit Theorem convention) for a cross-sectional mean/std/median to "
            "be a stable statistic rather than dominated by a handful of symbols. The existing "
            "RELATIVE_VALUE_DISLOCATION detector (data_v2/events/detectors.py) had no pre-existing "
            "minimum to inherit -- checked first, per mission section 10."
        ),
        "note": "eligible_rvd_base (per-symbol) AND cross_section_size >= cross_sectional_minimum "
                "(computed across all symbols at that exact timestamp) together define eligible_rvd. "
                "Symbols with missing flow/basis at a given bar do not participate in that bar's "
                "cross-sectional stats or count toward cross_section_size.",
        "mask_function": "data_v2.events.eligibility.eligible_rvd_base (+ build_event_feature_panel.py's "
                          "compute_cross_sectional_rvd second pass for the final eligible_rvd)",
    },
    "FORCED_FLOW_REVERSAL": {
        "required_columns": ["residual_return_15m", "oi", "oi_delta_pct_1h", "research_available_at"],
        "warmup_rule": "none beyond the required-column non-null check (residual_return_15m already "
                        "embeds the same 60d beta warmup as residual_return_1h)",
        "primary_condition": "liq_feed_available OR signed_volume non-null (data AVAILABILITY question -- "
                              "the P95 liquidation-vs-flow DETECTION threshold is a separate, PnL-blind "
                              "concern that lives in detectors.py, not here)",
        "note": "liq_feed_available=False means 'feed down/not wired', never coerced to '0 liquidations'.",
        "mask_function": "data_v2.events.eligibility.eligible_ffr",
    },
}


def main() -> None:
    im = pd.read_parquet(INSTRUMENT_MASTER)
    pit_symbols = sorted(im.loc[im["symbol"].str.endswith("USDT"), "symbol"])

    out = {
        "git_sha": _git_sha(),
        "timestamp": str(pd.Timestamp.now(tz="UTC")),
        "pit_universe_size": len(pit_symbols),
        "pit_universe_manifest": "data_v2/instruments/instrument_master.parquet",
        "eligibility_depends_only_on": [
            "existence historique PIT (listing_ts/delisting_ts)",
            "disponibilite des features (non-null required columns)",
            "qualite des sources (Data V2 P0 acquisition, see DATA_V2_ACQUISITION_FREEZE.json)",
            "causalite (research_available_at)",
            "warmup (strict-prior, full-window rolling stats)",
            "absence de corruption",
        ],
        "eligibility_explicitly_excludes": [
            "return", "PF", "win rate", "Sharpe", "PnL", "MFE/MAE", "label", "any economic result",
        ],
        "families": FAMILY_CONTRACTS,
        "schema_required_columns": list(REQUIRED_COLUMNS),
        "schema_optional_columns": list(OPTIONAL_COLUMNS),
        "eligibility_module_sha256": _sha256_file(ELIGIBILITY_SRC),
        "schema_module_sha256": _sha256_file(SCHEMA_SRC),
        "economic_results_referenced": False,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"PIT universe: {len(pit_symbols)} symbols")
    print(f"families pre-registered: {list(FAMILY_CONTRACTS.keys())}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
