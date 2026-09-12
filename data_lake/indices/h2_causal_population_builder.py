#!/usr/bin/env python3
"""
h2_causal_population_builder.py -- une ligne par lancement H2 : sa population causale, son avance externe,
ses bloqueurs. Lit les sorties de P6 a P9 et P11 (capacite) ; ne telecharge rien, ne calcule aucun retour.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_lake.indices import h2_population_labels as L
from data_lake.indices import h2_clean_dataset_builder as B
from data_lake.indices import h2_event_classifier as K

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "data_acquisition"
CAPACITY = OUT / "H2_DEPTH_CAPACITY_FEATURES.json"
PRECEDENCE = ROOT / "data" / "cross_venue" / "precedence.json"
COLUMNS = ["event_id", "symbol", "asset", "binance_opening_ts", "announced_opening_ts", "first_known_venue", "first_known_venue_listing_ts", "lead_time_days", "lead_time_bucket",
           "population", "class", "blockers", "capacity_status", "capacity_measured", "execution_cost_status", "announcement_body_status", "provider_needed", "excluded_reason"]


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def capacity_index() -> Dict[str, Dict[str, Any]]:
    if not CAPACITY.exists():
        return {}
    try:
        d = json.loads(CAPACITY.read_text())
    except ValueError:
        return {}
    return {e["event_id"]: {"status": e["capacity_status_t0"], "measured": bool(e["capacity_measured"]), "score": e["capacity_score_t0"], "resolution_bps": e.get("depth_resolution_bps")} for e in d.get("events", [])}


def precedence_details() -> Dict[str, Dict[str, Any]]:
    if not PRECEDENCE.exists():
        return {}
    try:
        return {d["asset"]: d for d in json.loads(PRECEDENCE.read_text())["decisions"]}
    except (ValueError, KeyError):
        return {}


def build(require_actual_fees: bool = True) -> Dict[str, Any]:
    base = B.build(); cap = capacity_index(); prec = precedence_details()
    rows: List[Dict[str, Any]] = []
    for e in base["events"]:
        k = K.classify(e, require_actual_fees=require_actual_fees)
        p = prec.get(e["asset"], {}); c = cap.get(e["event_id"], {})
        ev = {"venue_precedence": p.get("classification") or e.get("venue_precedence"), "first_venue": p.get("first_elsewhere_venue"),
              "undated_venues": p.get("undated_venues"), "binance_spot_before": e.get("binance_spot_existed_before"), "lead_days": p.get("lead_days"),
              "timestamp_bad": k["classification"] == K.BAD_TIMESTAMP, "provider_needed": bool(e.get("missing_requires_provider")),
              "capacity_measured": bool(c.get("measured")), "actual_fee_known": bool(e.get("actual_fee_known"))}
        lab = L.label(ev, require_actual_fees)
        rows.append({"event_id": e["event_id"], "symbol": e["symbol"], "asset": e["asset"], "binance_opening_ts": e["first_bar_ts"], "announced_opening_ts": e.get("announced_start_ts"),
                     "first_known_venue": p.get("first_elsewhere_venue"), "first_known_venue_listing_ts": p.get("first_elsewhere_ts"), "lead_time_days": p.get("lead_days"),
                     "lead_time_bucket": lab["lead_time_bucket"], "population": lab["population"], "class": lab["class"], "blockers": lab["blockers"],
                     "capacity_status": c.get("status") or ("NOT_COMPUTED" if not cap else "NO_DEPTH"), "capacity_measured": bool(c.get("measured")),
                     "execution_cost_status": "known" if e.get("actual_fee_known") else "unknown",
                     "announcement_body_status": "extracted_time" if e.get("announced_start_ts") else ("body_only" if e.get("body_chars") else "missing"),
                     "provider_needed": bool(e.get("missing_requires_provider")), "excluded_reason": lab["excluded_reason"], "clean": lab["clean"]})
    return {"built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "require_actual_fees": require_actual_fees, "capacity_inputs": len(cap),
            "rows": rows, "summary": L.summarise(rows), "no_alpha_test": True, "no_return_computed": True}


def write_reports(d: Dict[str, Any], out: Optional[Path] = None) -> Dict[str, Any]:
    out = Path(out) if out else OUT; out.mkdir(parents=True, exist_ok=True)
    s, a = d["summary"], d["summary"]["answers"]
    _write_atomic(out / "H2_CAUSAL_POPULATIONS.json", json.dumps({k: v for k, v in d.items() if k != "rows"}, indent=1, ensure_ascii=False, default=str) + "\n")
    _write_atomic(out / "H2_CAUSAL_POPULATION_MATRIX.json", json.dumps(d, indent=1, ensure_ascii=False, default=str) + "\n")
    with open(out / "H2_CAUSAL_POPULATION_MATRIX.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS); w.writeheader()
        for r in d["rows"]:
            w.writerow({**{k: r.get(k) for k in COLUMNS if k != "blockers"}, "blockers": ";".join(r["blockers"])})
    md = [f"# H2 CAUSAL POPULATIONS — P11 ({d['built_at_utc'][:19]} UTC)", "",
          "H2 is not one population. It is the union of markets born in different circumstances that regard seq 8 averaged "
          "into one number. This report splits it by **who priced the asset first** and by **what still blocks a test**. "
          "Two axes, never confused. No return, no verdict, no budget.", "",
          "## Populations (market structure)", "", "| population | events | share |", "|---|---|---|"]
    for p, n in s["by_population"].items():
        md.append(f"| `{p}` | {n} | {100 * n / s['n']:.0f} % |")
    md += ["", "## Lead time of the first external listing over the Binance perpetual", "", "| bucket | events |", "|---|---|"]
    for b in L.LEAD_BUCKETS:
        if s["by_lead_bucket"].get(b):
            md.append(f"| {b} | {s['by_lead_bucket'][b]} |")
    md += ["", "## The seven answers", "",
           f"1. True first listings (the Binance perpetual is the first market anywhere collected): **{a['1_true_first_listings']}**.",
           f"2. MEXC-first: **{a['2_mexc_first']}**.",
           f"3. Other-venue-first excluding MEXC (OKX, Bybit, KuCoin, other): **{a['3_other_venue_first_excluding_mexc']}**.",
           f"4. Bad timestamps: **{a['4_bad_timestamps']}**.",
           f"5. Blocked only by execution cost: **{a['5_blocked_only_by_cost']}**.",
           f"6. Blocked only by capacity: **{a['6_blocked_only_by_capacity']}**; blocked by cost and capacity together and nothing else: {a['6b_blocked_by_cost_and_capacity_only']}.",
           f"7. Clean per population if cost and capacity are lifted: {a['7_clean_per_population_if_cost_and_capacity_lifted']}.", "",
           f"Clean now: {s['clean_now']}. Capacity inputs available: {d['capacity_inputs']} events.", "",
           "## What this means", "",
           "A test on `H2_POOLED` would mix a MEXC migration effect, an OKX / Bybit cross-listing effect and a handful of true "
           "births. Any future preregistration must name one population. The count of true first listings is the ceiling of "
           "what a first-listing hypothesis can ever use from history; it is not increased by any backfill.", ""]
    _write_atomic(out / "H2_CAUSAL_POPULATIONS.md", "\n".join(md) + "\n")
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--accept-published-fees", action="store_true"); a = ap.parse_args()
    d = build(require_actual_fees=not a.accept_published_fees); print(json.dumps(write_reports(d), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
