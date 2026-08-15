#!/usr/bin/env python3
"""
scripts/build_event_feature_eligibility_report.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, sections 12-13: reports/EVENT_FEATURE_ELIGIBILITY_REPORT.json
-- per-family row/symbol/year eligibility counts and rejection-reason
breakdown, PLUS the pre-economic "enough diversity to search" gate
(family_data_status: READY/LIMITED/NOT_READY). Reads ONLY structural
columns already in the rebuilt panel (data_v2/events/eligibility.py's
eligible_* columns, residual_std_30d, cross_section_size,
funding_is_settlement) -- never a return/PnL/label, none of which exist in
this panel or this script.

Minimum research-diversity gate (mission section 13, taken verbatim from
the mission text -- not invented here): >= 3 calendar years represented
(spanning multiple market regimes) AND >= 100 symbols potentially
observable where possible. No existing threshold to inherit was found in
this codebase for either number (checked first, per the mission's own
instruction not to hardcode arbitrarily if a different one already
existed).

Rejection reasons are NOT mutually exclusive -- a row can fail more than
one axis at once; each count is "how many eligible-candidate rows failed
on THIS axis", not a partition.

    python3 scripts/build_event_feature_eligibility_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_v2.events import eligibility as elig  # noqa: E402

PANEL_DIR = ROOT / "data_v2/normalized/event_feature_panel/venue=binance"
OUT_PATH = ROOT / "reports/EVENT_FEATURE_ELIGIBILITY_REPORT.json"

MIN_YEARS = 3
MIN_SYMBOLS = 100

FAMILIES = ["DELEVERAGING", "CROWDING", "RELATIVE_VALUE_DISLOCATION", "FORCED_FLOW_REVERSAL"]
ELIGIBLE_COL = {
    "DELEVERAGING": "eligible_deleveraging",
    "CROWDING": "eligible_crowding",
    "RELATIVE_VALUE_DISLOCATION": "eligible_rvd",
    "FORCED_FLOW_REVERSAL": "eligible_ffr",
}

# per-family rejection-reason predicates -- each returns a boolean Series
# ("this row fails on this axis"), evaluated only for reporting, never
# used to fill/mutate a feature.
REJECTION_PREDICATES = {
    "DELEVERAGING": {
        "missing_oi": lambda df: df["oi"].isna(),
        "missing_flow": lambda df: df["aggressive_sell_usd"].isna(),
        "residual_warmup": lambda df: df["residual_std_30d"].isna(),
        "causality": lambda df: df["research_available_at"].isna(),
    },
    "CROWDING": {
        "missing_funding": lambda df: df["funding_rate"].isna() | df["funding_rate_percentile_90d"].isna(),
        "missing_basis": lambda df: df["basis_z_1d"].isna(),
        "missing_oi": lambda df: df["oi"].isna(),
        "missing_flow": lambda df: df["aggressive_buy_usd"].isna() | df["aggressive_sell_usd"].isna(),
        "funding_warmup": lambda df: ~elig.funding_settlement_warmup(df["funding_is_settlement"], df["timestamp"]),
        "causality": lambda df: df["research_available_at"].isna(),
    },
    "RELATIVE_VALUE_DISLOCATION": {
        "residual_warmup": lambda df: df["residual_std_30d"].isna(),
        "missing_basis": lambda df: df["basis_z_1d"].isna(),
        "missing_flow": lambda df: df["signed_volume"].isna(),
        "cross_sectional_minimum": lambda df: df.get("cross_section_size", pd.Series(0, index=df.index)) < elig.MIN_CROSS_SECTION_SIZE,
        "causality": lambda df: df["research_available_at"].isna(),
    },
    "FORCED_FLOW_REVERSAL": {
        "missing_oi": lambda df: df["oi"].isna(),
        "missing_flow": lambda df: (~df["liq_feed_available"].fillna(False)) & df["signed_volume"].isna(),
        "causality": lambda df: df["research_available_at"].isna(),
    },
}


def _scan_symbols() -> list[str]:
    if not PANEL_DIR.exists():
        return []
    return sorted({p.name.split("=", 1)[1] for p in PANEL_DIR.glob("symbol=*") if p.is_dir()})


def _load_symbol(symbol: str) -> pd.DataFrame | None:
    parts = sorted((PANEL_DIR / f"symbol={symbol}").glob("year=*/event_feature_panel_5m.parquet"))
    if not parts:
        return None
    frames = [pd.read_parquet(p) for p in parts]
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    symbols = _scan_symbols()
    per_family = {
        fam: {
            "total_pit_rows": 0, "eligible_rows": 0,
            "eligible_symbols": set(), "years_represented": set(),
            "rows_rejected_for": {reason: 0 for reason in REJECTION_PREDICATES[fam]},
            "rows_rejected_for_lifecycle": 0,
            "by_year": {}, "by_symbol_eligible_rows": {},
        }
        for fam in FAMILIES
    }

    for i, symbol in enumerate(symbols, 1):
        df = _load_symbol(symbol)
        if df is None or df.empty:
            continue
        years = pd.to_datetime(df["timestamp"], utc=True).dt.year

        for fam in FAMILIES:
            col = ELIGIBLE_COL[fam]
            if col not in df.columns:
                continue
            entry = per_family[fam]
            n_total = len(df)
            eligible = df[col].fillna(False)
            n_eligible = int(eligible.sum())
            entry["total_pit_rows"] += n_total
            entry["eligible_rows"] += n_eligible
            if n_eligible > 0:
                entry["eligible_symbols"].add(symbol)
                entry["by_symbol_eligible_rows"][symbol] = n_eligible
                for y in years[eligible].unique().tolist():
                    entry["years_represented"].add(int(y))
                    entry["by_year"][str(int(y))] = entry["by_year"].get(str(int(y)), 0) + int((eligible & (years == y)).sum())

            for reason, pred in REJECTION_PREDICATES[fam].items():
                candidate = ~eligible  # only count rows that are NOT eligible
                try:
                    failed = pred(df) & candidate
                except KeyError:
                    continue
                entry["rows_rejected_for"][reason] += int(failed.sum())

        if i % 50 == 0:
            print(f"  scanned {i}/{len(symbols)} symbols", flush=True)

    out_families = {}
    for fam in FAMILIES:
        e = per_family[fam]
        n_years = len(e["years_represented"])
        n_symbols = len(e["eligible_symbols"])
        if n_years >= MIN_YEARS and n_symbols >= MIN_SYMBOLS:
            status = "READY"
        elif n_years >= 1 and n_symbols >= 1:
            status = "LIMITED"
        else:
            status = "NOT_READY"
        out_families[fam] = {
            "total_pit_rows": e["total_pit_rows"],
            "eligible_rows": e["eligible_rows"],
            "eligible_pct": round(e["eligible_rows"] / e["total_pit_rows"], 6) if e["total_pit_rows"] else 0.0,
            "eligible_symbols": n_symbols,
            "years_represented": sorted(e["years_represented"]),
            "n_years_represented": n_years,
            "rows_rejected_for": e["rows_rejected_for"],
            "rows_rejected_for_lifecycle": e["rows_rejected_for_lifecycle"],
            "by_year_eligible_rows": dict(sorted(e["by_year"].items())),
            "min_years_gate": MIN_YEARS,
            "min_symbols_gate": MIN_SYMBOLS,
            "family_data_status": status,
        }

    out = {
        "generated_at": str(pd.Timestamp.now(tz="UTC")),
        "symbols_scanned": len(symbols),
        "min_years_gate": MIN_YEARS,
        "min_symbols_gate": MIN_SYMBOLS,
        "min_years_gate_source": "mission section 13 text, verbatim -- no existing threshold found to inherit",
        "min_symbols_gate_source": "mission section 13 text, verbatim -- no existing threshold found to inherit",
        "families": out_families,
        "economic_results_referenced": False,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    for fam, v in out_families.items():
        print(f"{fam:<28} eligible_rows={v['eligible_rows']:>12} ({v['eligible_pct']*100:.2f}%) "
              f"symbols={v['eligible_symbols']:>4} years={v['n_years_represented']} status={v['family_data_status']}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
