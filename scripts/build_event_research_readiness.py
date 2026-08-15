#!/usr/bin/env python3
"""
scripts/build_event_research_readiness.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, section 17: reports/EVENT_RESEARCH_READINESS.json --
combines the full-universe Data V2 verdict, the acquisition-exhaustion
verdict, the panel construction-integrity verdict, and each family's own
source-qualified data-readiness verdict into one document. A family can be
READY here even while DATA_V2_FULL_UNIVERSE_READY is False, PROVIDED its
own observations are exhaustively source-qualified, no missing required
feature was ever filled with zero, its eligibility mask was frozen before
any PnL, and its population is representative (EVENT_FEATURE_ELIGIBILITY_
REPORT.json's family_data_status == READY).

Pure combinator: reads the four upstream reports, asserts none of them are
missing, writes the combined verdict. No panel/economic data read directly.

    python3 scripts/build_event_research_readiness.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_V2_READINESS = ROOT / "reports/DATA_V2_READINESS.json"
ACQUISITION_FREEZE = ROOT / "reports/DATA_V2_ACQUISITION_FREEZE.json"
EVENT_PANEL_READINESS = ROOT / "reports/EVENT_PANEL_READINESS.json"
ELIGIBILITY_REPORT = ROOT / "reports/EVENT_FEATURE_ELIGIBILITY_REPORT.json"
OUT_PATH = ROOT / "reports/EVENT_RESEARCH_READINESS.json"

FAMILY_KEY = {
    "DELEVERAGING": "DELEVERAGING_DATA_READY",
    "CROWDING": "CROWDING_DATA_READY",
    "RELATIVE_VALUE_DISLOCATION": "RVD_DATA_READY",
    "FORCED_FLOW_REVERSAL": "FFR_DATA_READY",
}


def main() -> None:
    for p in (DATA_V2_READINESS, ACQUISITION_FREEZE, EVENT_PANEL_READINESS, ELIGIBILITY_REPORT):
        if not p.exists():
            print(f"FATAL: missing upstream report {p} -- run its builder first.")
            sys.exit(1)

    data_v2 = json.loads(DATA_V2_READINESS.read_text())
    freeze = json.loads(ACQUISITION_FREEZE.read_text())
    panel = json.loads(EVENT_PANEL_READINESS.read_text())
    elig_report = json.loads(ELIGIBILITY_REPORT.read_text())

    families_out = {}
    for fam, key in FAMILY_KEY.items():
        fam_data = elig_report["families"].get(fam, {})
        status = fam_data.get("family_data_status", "NOT_READY")
        families_out[key] = status == "READY"
        families_out[f"{key}_status"] = status
        families_out[f"{key}_eligible_rows"] = fam_data.get("eligible_rows", 0)
        families_out[f"{key}_eligible_symbols"] = fam_data.get("eligible_symbols", 0)
        families_out[f"{key}_years_represented"] = fam_data.get("n_years_represented", 0)

    out = {
        "generated_at": str(pd.Timestamp.now(tz="UTC")),
        "DATA_V2_FULL_UNIVERSE_READY": bool(data_v2.get("DATA_V2_READY", False)),
        "DATA_V2_ACQUISITION_EXHAUSTED": bool(freeze.get("DATA_V2_ACQUISITION_EXHAUSTED", False)),
        "EVENT_PANEL_READY": bool(panel.get("EVENT_PANEL_READY", False)),
        **families_out,
        "source_reports": {
            "data_v2_readiness_generated_at": data_v2.get("generated_at"),
            "acquisition_freeze_generated_at": freeze.get("timestamp"),
            "event_panel_readiness_generated_at": panel.get("generated_at"),
            "eligibility_report_generated_at": elig_report.get("generated_at"),
        },
        "note": (
            "A family's *_DATA_READY may be True even while DATA_V2_FULL_UNIVERSE_READY "
            "is False -- full-universe readiness requires funding>=99%/OI>=95%/"
            "aggTrades>=95% across ALL 312 PIT symbols regardless of whether a given "
            "family's detector actually uses a given symbol/period; family readiness "
            "requires only that the OBSERVATIONS a family's own eligibility mask "
            "actually selects are exhaustively source-qualified, causal, and "
            "representative -- a strictly narrower, and independently honest, claim."
        ),
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"DATA_V2_FULL_UNIVERSE_READY: {out['DATA_V2_FULL_UNIVERSE_READY']}")
    print(f"DATA_V2_ACQUISITION_EXHAUSTED: {out['DATA_V2_ACQUISITION_EXHAUSTED']}")
    print(f"EVENT_PANEL_READY: {out['EVENT_PANEL_READY']}")
    for key in FAMILY_KEY.values():
        print(f"{key}: {out[key]} ({out[key + '_status']})")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
