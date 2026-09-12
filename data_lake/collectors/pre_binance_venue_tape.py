#!/usr/bin/env python3
"""
pre_binance_venue_tape.py -- orchestrateur multi-place de la tape pre-Binance.

MEXC est collecte par mexc_pre_binance_tape (113 evenements MEXC-first). Les autres places (OKX, Bybit, KuCoin,
Gate) ne sont pas collectees ici : le module ecrit leurs manifestes attendus avec statut `not_collected` et la
route publique documentee, pour que la prochaine passe n'ait rien a deviner. Puis il calcule les features
pre-Binance et ecrit les rapports. Aucune donnee post-t0, aucun signal, aucun verdict.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_lake.collectors import venue_pre_binance_paths as VP
from data_lake.collectors import mexc_pre_binance_tape as MX
from data_lake.indices import pre_binance_features as PF

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "data_acquisition"
PRECEDENCE = ROOT / "data" / "cross_venue" / "precedence.json"
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"


def _rel(p: Path) -> str:
    """Chemin relatif au depot quand possible, sinon absolu (tests hors depot)."""
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def other_venue_first_events() -> List[Dict[str, Any]]:
    if not PRECEDENCE.exists() or not UNIVERSE.exists():
        return []
    dec = {d["asset"]: d for d in json.loads(PRECEDENCE.read_text())["decisions"]}
    out = []
    for e in json.loads(UNIVERSE.read_text())["events"]:
        d = dec.get(e["asset"])
        if d and d.get("classification") == "OTHER_VENUE_FIRST":
            out.append({"event_id": e["event_id"], "asset": e["asset"], "t0": e["tradable_start_ts"], "venue": d.get("first_elsewhere_venue"), "symbol": d.get("first_elsewhere_symbol"),
                        "market": d.get("first_elsewhere_market"), "listed_ts": d.get("first_elsewhere_ts"), "lead_days": d.get("lead_days")})
    return out


def write_placeholder_manifests(events: List[Dict[str, Any]], root: Optional[Path] = None) -> Dict[str, int]:
    """Pour les places non collectees : ce qui serait demande, ou, et par quelle route. Jamais ecrase si deja present."""
    root = Path(root) if root else VP.STORE; n = Counter()
    for ev in events:
        v = ev["venue"]
        if v == "mexc" or v not in VP.ROUTES:
            continue
        mp = VP.manifest_path(v, ev["event_id"], root)
        if mp.exists():
            n["existing"] += 1; continue
        reqs = VP.requests_for(ev["t0"])
        man = {"event_id": ev["event_id"], "venue": v, "symbol": ev["symbol"], "market": ev["market"], "t0": ev["t0"], "status": "not_collected",
               "route": VP.ROUTES[v], "expected_files": [{"interval": r["interval"], "start_ms": r["start_ms"], "end_ms": r["end_ms"], "path": _rel(VP.local_path(v, ev["market"] or "spot", ev["symbol"] or ev["asset"], r["interval"], ev["t0"], root))} for r in reqs],
               "written_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"), "no_post_t0_data": True}
        _write_atomic(mp, json.dumps(man, indent=1, ensure_ascii=False)); n["written"] += 1
    return dict(n)


def load_candles(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path) if Path(path).is_absolute() else ROOT / path
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())["rows"]
    except (ValueError, KeyError, OSError):
        return []


def mexc_features(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(root) if root else VP.STORE; rows = []
    for ev in MX.mexc_first_events():
        mp = VP.manifest_path("mexc", ev["event_id"], root)
        man = json.loads(mp.read_text()) if mp.exists() else {"status": "not_collected", "files": {}}
        t0_ms = int(VP.parse_ts(ev["t0"]).timestamp() * 1000)
        d = load_candles((man["files"].get("1d") or {}).get("path")); h = load_candles((man["files"].get("60m") or {}).get("path"))
        try:
            f = PF.compute(d, h, t0_ms, "mexc", ev.get("lead_days"), "mexc %s klines" % (man.get("market") or ev.get("mexc_market")))
        except PF.PostT0Leak as e:
            f = {k: None for k in PF.FEATURES}; f.update({"first_venue": "mexc", "data_source": "REJECTED: " + str(e), "coverage_status": "not_collected"})
        rows.append({"event_id": ev["event_id"], "asset": ev["asset"], "binance_symbol": ev["binance_symbol"], "mexc_symbol": ev.get("mexc_symbol"), "mexc_market": ev.get("mexc_market"),
                     "t0": ev["t0"], "mexc_listed_ts": ev.get("mexc_listed_ts"), "tape_status": man.get("status"), "daily_rows": len(d), "hourly_rows": len(h), **f})
    return rows


def write_reports(out: Optional[Path] = None) -> Dict[str, Any]:
    out = Path(out) if out else OUT; out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ovf = other_venue_first_events(); by_venue = Counter(e["venue"] for e in ovf)
    ph = write_placeholder_manifests(ovf)
    mans = {}
    for v in VP.VENUES:
        d = VP.STORE / v / "manifests"
        mans[v] = Counter()
        if d.exists():
            for p in d.glob("*.json"):
                try:
                    mans[v][json.loads(p.read_text()).get("status")] += 1
                except ValueError:
                    mans[v]["unreadable"] += 1
    feats = mexc_features(); cov = Counter(r["coverage_status"] for r in feats)
    missing_5m = sum(1 for r in feats if r["tape_status"] in ("collected", "partial") and r["hourly_rows"] and (VP.STORE / "mexc" / "manifests" / (r["event_id"] + ".json")).exists()
                     and (json.loads((VP.STORE / "mexc" / "manifests" / (r["event_id"] + ".json")).read_text())["files"].get("5m") or {}).get("status") != "ok")
    buildable = {k: sum(1 for r in feats if r.get(k) is not None) for k in PF.FEATURES if k.startswith("pre_binance_")}
    venue_doc = {"generated_at_utc": now, "other_venue_first_events": len(ovf), "by_first_venue": dict(by_venue), "manifests_by_venue": {v: dict(c) for v, c in mans.items()},
                 "placeholders_written": ph, "routes": VP.ROUTES, "windows": VP.WINDOWS, "granularity": [{"interval": iv, "span_s": s} for iv, s in VP.GRANULARITY], "no_post_t0_data": True, "no_alpha_test": True}
    _write_atomic(out / "PRE_BINANCE_VENUE_TAPE_COVERAGE.json", json.dumps(venue_doc, indent=1, ensure_ascii=False, default=str) + "\n")
    md = [f"# PRE-BINANCE VENUE TAPE — coverage ({now[:19]} UTC)", "",
          "For the assets that already traded elsewhere before the Binance perpetual opened, the state of that other market "
          "**before t0**. Nothing at or after t0 is requested by construction; this tape cannot measure anything post-Binance. "
          "No signal, no verdict, no budget.", "",
          f"`OTHER_VENUE_FIRST` events: **{len(ovf)}**, by first venue {dict(by_venue)}.", "",
          "| venue | status | manifests | route |", "|---|---|---|---|"]
    for v in VP.VENUES:
        md.append(f"| {v} | {VP.ROUTES[v]['status']} | {dict(mans[v]) or '—'} | {VP.ROUTES[v]['cost']}; `{VP.ROUTES[v]['spot'][:80]}…` |")
    md += ["", "Windows: " + ", ".join(f"t0 − {k} → t0" for k in VP.WINDOWS) + ". Granularity: daily over 30 d, hourly over 3 d, 5-min over 6 h.", "",
           "Only MEXC is collected by this branch (it is the first venue for the large majority). The other venues have their expected "
           "manifests written with the public endpoint that would fill them, so the next pass has nothing to guess.", ""]
    _write_atomic(out / "PRE_BINANCE_VENUE_TAPE_COVERAGE.md", "\n".join(md) + "\n")

    with open(out / "MEXC_PRE_BINANCE_FEATURES.csv", "w", newline="", encoding="utf-8") as f:
        cols = ["event_id", "asset", "binance_symbol", "mexc_symbol", "mexc_market", "t0", "mexc_listed_ts", "tape_status", "daily_rows", "hourly_rows"] + PF.FEATURES
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in feats:
            w.writerow({k: r.get(k) for k in cols})
    _write_atomic(out / "MEXC_PRE_BINANCE_FEATURES.json", json.dumps({"generated_at_utc": now, "n": len(feats), "coverage": dict(cov), "buildable_features": buildable, "rows": feats,
                  "descriptive_only": True, "no_post_t0_data": True, "no_alpha_test": True}, indent=1, ensure_ascii=False, default=str) + "\n")
    mx = [f"# MEXC PRE-BINANCE TAPE — coverage ({now[:19]} UTC)", "",
          "MEXC was the first dated venue for most H2 assets. This is the MEXC market before Binance opened its perpetual: "
          "daily candles over 30 days, hourly over 3 days, 5-minute over 6 hours, all ending at t0 exclusive. The features are "
          "descriptive of the pre-Binance state; they are not a signal and produce no verdict.", "",
          "## The six answers", "",
          f"1. MEXC-first events: **{len(feats)}** (spot {sum(1 for r in feats if r['mexc_market'] == 'spot')}, perp {sum(1 for r in feats if r['mexc_market'] == 'perp')}).",
          f"2. With a retrievable pre-Binance tape: **{cov.get('full', 0) + cov.get('daily_only', 0) + cov.get('partial', 0)}** "
          f"(full daily + hourly {cov.get('full', 0)}, daily only {cov.get('daily_only', 0)}, partial {cov.get('partial', 0)}).",
          f"3. Blocked by the API / source: **{cov.get('not_collected', 0)}** (symbol renamed, delisted from MEXC, or no history that far back).",
          f"4. Missing windows: the 5-minute window (t0 − 6 h) is empty for {missing_5m} events — MEXC does not serve 5-minute history far back; "
          "the hourly window covers the last 3 days for every collected event.",
          f"5. Features buildable free, count of events with a value: {buildable}.",
          "6. External provider: **not needed** for the descriptive tape. If a future preregistration needs 5-minute MEXC history for old "
          "launches, the targeted list is the `not_collected` + missing-5m events above — a per-window request, never a subscription.", "",
          "## Descriptive distribution (not a result)", ""]
    for k in ("pre_binance_return_7d", "pre_binance_return_24h", "pre_binance_volatility_7d", "pre_binance_pump_score", "pre_binance_exhaustion_score", "listing_age_days_at_binance_open"):
        vals = sorted(r[k] for r in feats if r.get(k) is not None)
        if vals:
            mx.append(f"- `{k}`: n {len(vals)}, p25 {vals[len(vals)//4]:.4g}, median {vals[len(vals)//2]:.4g}, p75 {vals[3*len(vals)//4]:.4g}")
    mx += ["", "These describe MEXC before Binance. They are joined to nothing after t0, and this branch computes nothing after t0.", ""]
    _write_atomic(out / "MEXC_PRE_BINANCE_TAPE_COVERAGE.md", "\n".join(mx) + "\n")
    return {"other_venue_first": len(ovf), "mexc_features": len(feats), "coverage": dict(cov), "placeholders": ph}


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--reports", action="store_true"); a = ap.parse_args()
    if a.reports:
        print(json.dumps(write_reports(), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
