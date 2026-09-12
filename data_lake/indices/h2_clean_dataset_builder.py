#!/usr/bin/env python3
"""
h2_clean_dataset_builder.py -- rassembler ce que P6 a P9 ont etabli, evenement par evenement.

Lit uniquement des SORTIES DEJA ECRITES (manifestes Vision, corps d'annonces archives, rapport d'execution,
precedence cross-venue) et les compose en une ligne par evenement H2. Ne telecharge rien, ne calcule aucun
rendement, ne produit aucun signal.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
VISION_MANIFESTS = ROOT / "data" / "vision_backfill" / "manifests"
BODIES = ROOT / "data" / "announcement_bodies"
EXEC_REPORT = ROOT / "reports" / "execution" / "ACCOUNT_EXECUTION_REALITY.json"
PRECEDENCE = ROOT / "data" / "cross_venue" / "precedence.json"
AFTER_CROSS = ROOT / "reports" / "data_acquisition" / "H2_AFTER_CROSS_VENUE_COVERAGE_MATRIX.json"
TAPE = ROOT / "data_lake" / "events" / "official_event_tape.jsonl"


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def vision_index() -> Dict[str, Dict[str, Any]]:
    out = {}
    if not VISION_MANIFESTS.exists():
        return out
    for p in sorted(VISION_MANIFESTS.glob("*.json")):
        if p.name.count(".") != 1:
            continue
        m = _load(p)
        if m:
            out[m["event_id"]] = m
    return out


def body_index() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not BODIES.exists():
        return out
    for p in sorted(BODIES.rglob("*.json")):
        if p.stem.count(".") >= 1:
            continue
        r = _load(p)
        if not r:
            continue
        ex = r.get("extracted") or {}
        for eid in r.get("event_ids", []):
            out[eid] = {"url": r.get("url"), "raw_hash": r.get("raw_hash"), "body_chars": r.get("body_chars", 0),
                        "trading_start_ts": ex.get("trading_start_ts"), "has_explicit_time": (ex.get("has_explicit_time") or {}).get("trading_start_ts")}
    return out


def precedence_index() -> Dict[str, Dict[str, Any]]:
    d = _load(PRECEDENCE) or _load(AFTER_CROSS)
    if not d:
        return {}
    if "decisions" in d:
        return {x["asset"]: x for x in d["decisions"]}
    return {r["asset"]: {"classification": r.get("venue_precedence"), "first_elsewhere_venue": r.get("first_elsewhere_venue"), "lead_days": r.get("lead_days")}
            for r in d.get("rows", []) if r.get("asset")}


def coverage_index() -> Dict[str, Dict[str, Any]]:
    d = _load(AFTER_CROSS)
    return {r["event_id"]: r for r in (d or {}).get("rows", [])}


def execution_state() -> Dict[str, Any]:
    d = _load(EXEC_REPORT) or {}
    a = d.get("answers") or {}
    return {"actual_taker_fee_bps": a.get("1_actual_futures_taker_fee_bps"), "actual_maker_fee_bps": a.get("2_actual_futures_maker_fee_bps"),
            "actual_fee_known": a.get("1_actual_futures_taker_fee_bps") is not None,
            "cost_assumptions": a.get("5_h2_h3_cost_assumptions") or {}, "remaining_theoretical": a.get("6_what_remains_theoretical") or [],
            "report_present": bool(d)}


def build() -> Dict[str, Any]:
    uni = _load(UNIVERSE) or {"events": []}
    vis, bod, prec, cov = vision_index(), body_index(), precedence_index(), coverage_index()
    ex = execution_state()
    rows: List[Dict[str, Any]] = []
    for e in uni["events"]:
        eid = e["event_id"]; m = vis.get(eid) or {}; b = bod.get(eid) or {}; p = prec.get(e["asset"]) or {}; c = cov.get(eid) or {}
        cvg = (m.get("coverage") or {})
        complete = cvg.get("complete") or {}
        needs_provider = not complete.get("bookDepth", False) or not cvg.get("index_reference_complete", False)
        rows.append({"event_id": eid, "symbol": e["symbol"], "asset": e["asset"],
                     "first_bar_ts": e["tradable_start_ts"], "announcement_ts": e["publication_ts"], "announced_start_ts": b.get("trading_start_ts"),
                     "announced_has_explicit_time": b.get("has_explicit_time"), "body_raw_hash": b.get("raw_hash"), "body_chars": b.get("body_chars", 0),
                     "market_state_core_complete": bool(cvg.get("core_complete")), "market_state_score": cvg.get("score"),
                     "vision_manifest_sha256": m.get("manifest_sha256"), "vision_bytes_on_disk": m.get("bytes_on_disk"),
                     "missing_requires_provider": bool(needs_provider), "missing_datasets": sorted(k for k, v in complete.items() if not v),
                     "venue_precedence": p.get("classification"), "first_elsewhere_venue": p.get("first_elsewhere_venue"), "lead_days": p.get("lead_days"),
                     "binance_spot_existed_before": False,     # regle de l'univers : aucun spot Binance avant le perpetuel
                     "actual_fee_known": ex["actual_fee_known"], "capacity_known": bool(c.get("capacity_present")),
                     "coverage_score": c.get("coverage_score")})
    return {"built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "events": rows, "execution": ex,
            "inputs": {"universe": str(UNIVERSE.relative_to(ROOT)), "vision_manifests": len(vis), "bodies": len(bod),
                       "precedence": len(prec), "coverage_rows": len(cov), "execution_report": ex["report_present"]},
            "no_alpha_test": True, "no_return_computed": True}
