#!/usr/bin/env python3
"""
h2_capacity_matrix.py -- la capacite mesuree pour les 174 lancements H2, et ce qu'elle change a la matrice.

Lit les archives P6 (gitignorees), ecrit les features et les rapports (versionnes). Aucun retour, aucun
verdict, aucun budget. "Capacite mesuree" veut dire : le carnet a ete lu et decrit ; cela ne dit rien
sur l'existence d'un edge.
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

from data_lake.indices import depth_capacity_features as DC

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "data_acquisition"
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
FREEZE = ROOT / "reports" / "data_acquisition" / "H2_CLEAN_DATASET_FREEZE.json"


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def compute_all(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    u = json.loads(UNIVERSE.read_text())["events"]
    out = []
    for i, e in enumerate(u[:limit] if limit else u):
        r = DC.compute_event(e["symbol"], e["event_id"], e["tradable_start_ts"]); r["asset"] = e["asset"]; out.append(r)
        if (i + 1) % 25 == 0:
            print("  %d / %d" % (i + 1, len(u)), flush=True)
    return out


def summarise(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    st = Counter(r["capacity_status_t0"] for r in results)
    res = Counter(r.get("depth_resolution_bps") for r in results)
    by_window = {}
    for m in DC.WINDOWS_MIN:
        by_window[str(m)] = dict(Counter(r["windows"][DC.WINDOWS_MIN.index(m)]["capacity_status"] for r in results if r["windows"]))
    supports = {str(n): sum(1 for r in results if r.get("supports_usd", {}).get(str(n))) for n in DC.NOTIONALS_USD}
    ok_any = sum(1 for r in results if any(w["capacity_status"] == "CAPACITY_OK" for w in r["windows"]))
    ok_1h = sum(1 for r in results if r["windows"] and r["windows"][-1]["capacity_status"] == "CAPACITY_OK")
    spreads = [r["windows"][0]["effective_spread_bps"] for r in results if r["windows"] and r["windows"][0].get("effective_spread_bps") is not None]
    spreads.sort()
    return {"n": len(results), "measured": sum(1 for r in results if r["capacity_measured"]), "status_at_t0": dict(st), "resolution_bps": dict(res),
            "status_by_window": by_window, "capacity_ok_at_any_window": ok_any, "capacity_ok_at_1h": ok_1h,
            "fills_within_observed_book_at_t0": supports,
            "effective_spread_t0_bps": {"n": len(spreads), "p25": spreads[len(spreads) // 4] if spreads else None, "median": spreads[len(spreads) // 2] if spreads else None, "p75": spreads[3 * len(spreads) // 4] if spreads else None} if spreads else None}


def write_reports(results: List[Dict[str, Any]], out: Optional[Path] = None) -> Dict[str, Any]:
    from data_lake.indices import h2_launch_coverage_matrix as M
    out = Path(out) if out else OUT; out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds"); s = summarise(results)
    _write_atomic(out / "H2_DEPTH_CAPACITY_FEATURES.json", json.dumps({"generated_at_utc": now, "windows_min": list(DC.WINDOWS_MIN), "notionals_usd": list(DC.NOTIONALS_USD),
                  "thresholds": {"spread_too_wide_bps": DC.SPREAD_TOO_WIDE_BPS, "depth_too_thin_usd": DC.DEPTH_TOO_THIN_USD}, "summary": s, "events": results,
                  "no_return_computed": True, "no_alpha_test": True}, indent=1, ensure_ascii=False, default=str) + "\n")
    # matrice apres capacite : "mesuree" = statut hors NO_DEPTH / BAD_BOOK
    cap = {r["event_id"]: bool(r["capacity_measured"]) for r in results}
    ev = M.load_events(); vis = json.loads((out / "_vision_probe_cache.json").read_text()) if (out / "_vision_probe_cache.json").exists() else {}
    kw: Dict[str, Any] = {"capacity": cap}
    try:
        from data_lake.collectors.announcement_body_archive import bodies_by_event, _vision_downloaded
        kw["bodies"] = bodies_by_event(); kw["downloaded"] = _vision_downloaded()
    except Exception:
        pass
    try:
        from data_lake.collectors.cross_venue_lifecycle import STORE as CV
        pj = json.loads((CV / "precedence.json").read_text()); dec = {d["asset"]: d for d in pj["decisions"]}
        prec = {e["asset"]: (dec[e["asset"]]["evidence"] if e["asset"] in dec and dec[e["asset"]]["classification"] != "UNKNOWN_PRECEDENCE" else None) for e in ev}
    except Exception:
        prec = M.other_venue_precedence(ev)
    rows = M.build_rows(ev, vis, prec, **kw)
    by_id = {r["event_id"]: r for r in results}
    for r in rows:
        c = by_id.get(r["event_id"])
        if c:
            r["capacity_status_t0"] = c["capacity_status_t0"]; r["capacity_score_t0"] = c["capacity_score_t0"]; r["depth_resolution_bps"] = c.get("depth_resolution_bps")
    cols = M.COLUMNS + ["capacity_status_t0", "capacity_score_t0", "depth_resolution_bps"]
    with open(out / "H2_AFTER_CAPACITY_COVERAGE_MATRIX.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})
    ms = M.summarize(rows)
    _write_atomic(out / "H2_AFTER_CAPACITY_COVERAGE_MATRIX.json", json.dumps({"generated_at_utc": now, "capacity_summary": s, "summary": ms, "rows": rows, "no_alpha_test": True}, indent=1, ensure_ascii=False, default=str) + "\n")

    # les 129 "potentiellement propres" de P10 : combien passent le filtre capacite ?
    fz = json.loads(FREEZE.read_text()) if FREEZE.exists() else {}
    proj_ids = None
    if fz:
        blocked_hard = {c["event_id"] for c in fz.get("classified", []) if c["classification"] in ("BAD_TIMESTAMP", "PROVIDER_NEEDED")}
        proj_ids = [r["event_id"] for r in results if r["event_id"] not in blocked_hard]
    ok_in_proj = [r for r in results if proj_ids is not None and r["event_id"] in proj_ids and r["capacity_status_t0"] == "CAPACITY_OK"]
    ok_any_in_proj = [r for r in results if proj_ids is not None and r["event_id"] in proj_ids and any(w["capacity_status"] == "CAPACITY_OK" for w in r["windows"])]
    paper_only = [r for r in results if r["capacity_measured"] and r["capacity_status_t0"] in ("DEPTH_TOO_THIN", "SPREAD_TOO_WIDE") and not any(w["capacity_status"] == "CAPACITY_OK" for w in r["windows"])]

    md = [f"# H2 DEPTH CAPACITY FEATURES — P11 ({now[:19]} UTC)", "",
          "Executable capacity around each of the 174 H2 launches, derived from the Vision archives already on disk "
          "(`bookDepth` for the book, `aggTrades` for a mid and an effective-spread proxy). No return computed, no verdict, "
          "no budget. \"Measured\" means the book was read and described; it says nothing about an edge.", "",
          "## What the free archive can and cannot say", "",
          "- `bookDepth` gives **cumulative notional at ±1 %, ±2 % … ±5 %** of the mid, one snapshot every ~30 s. Archives from 2026 "
          "add a ±0.2 % level. There is **no best bid, no best ask, no mid** in the file.",
          f"- Resolution across the 174 events: {s['resolution_bps']} (bps; `None` = no book at all).",
          "- Depth within 10 bps is below the resolution of every archive → always `None`. Depth within 25 / 50 bps is reported "
          "as the 20-bps band **as a lower bound**, and only where that band exists.",
          "- Slippage for a 100 / 500 / 1 000 USDT market order: an **upper bound** (the band that contains the order) and a "
          "linear estimate assuming uniform fills inside the band — both reported, the method is named.",
          "- The mid is the median trade price in the minute; the effective spread is (mean aggressive-buy price − mean "
          "aggressive-sell price) / mid from `is_buyer_maker`. Both are proxies and labelled as such.", "",
          "## The seven answers", "",
          f"1. Events with a measured capacity (a readable book at t0): **{s['measured']} / {s['n']}**.",
          f"2. Depth too thin at t0 (thinner side < {DC.DEPTH_TOO_THIN_USD:.0f} USDT within the finest band): **{s['status_at_t0'].get('DEPTH_TOO_THIN', 0)}**.",
          f"3. Spread too wide at t0 (proxy > {DC.SPREAD_TOO_WIDE_BPS:.0f} bps): **{s['status_at_t0'].get('SPREAD_TOO_WIDE', 0)}**.",
          f"4. No depth at all: **{s['status_at_t0'].get('NO_DEPTH', 0)}** (no `bookDepth` archive for the launch day); bad book: {s['status_at_t0'].get('BAD_BOOK', 0)}.",
          f"5. Of the {len(proj_ids) if proj_ids is not None else '—'} events P10 called potentially clean, **{len(ok_in_proj)}** are `CAPACITY_OK` at t0 and "
          f"**{len(ok_any_in_proj)}** at some window within the first hour. `UNKNOWN` at t0: {s['status_at_t0'].get('UNKNOWN', 0)} — the book fills a 1 000 USDT order "
          "within 1 % and the spread is fine, but the archive generation has no 20-bps level, so sub-1 % capacity is not observable.",
          f"6. Orders that fill inside the observed book at t0 (both sides): 100 USDT {s['fills_within_observed_book_at_t0']['100']}, "
          f"500 USDT {s['fills_within_observed_book_at_t0']['500']}, 1 000 USDT {s['fills_within_observed_book_at_t0']['1000']} events. "
          f"Effective spread proxy at t0: median {s['effective_spread_t0_bps']['median'] if s['effective_spread_t0_bps'] else '—'} bps "
          f"(p25 {s['effective_spread_t0_bps']['p25'] if s['effective_spread_t0_bps'] else '—'}, p75 {s['effective_spread_t0_bps']['p75'] if s['effective_spread_t0_bps'] else '—'}).",
          f"7. Paper-only events (book measured, but too thin or too wide at t0 and never `CAPACITY_OK` in the first hour): **{len(paper_only)}** — "
          + (", ".join(r["symbol"] for r in paper_only[:15]) + (" …" if len(paper_only) > 15 else "")) + ".", "",
          "## Status by window (minutes after the first traded bar)", "", "| window | " + " | ".join(DC.STATUSES) + " |", "|---|" + "---|" * len(DC.STATUSES)]
    for m in DC.WINDOWS_MIN:
        bw = s["status_by_window"][str(m)]; md.append(f"| +{m} min | " + " | ".join(str(bw.get(k, 0)) for k in DC.STATUSES) + " |")
    md += ["", f"`CAPACITY_OK` at the 1-hour mark: {s['capacity_ok_at_1h']} events; at any window: {s['capacity_ok_at_any_window']}.", "",
           "## What this changes in the coverage matrix", "",
           f"`capacity_present` is now credited for the {s['measured']} measured events. Matrix after capacity: {ms['buckets_now']}, "
           f"scores min {ms['score_now']['min']} / median {ms['score_now']['median']} / max {ms['score_now']['max']}.", ""]
    _write_atomic(out / "H2_DEPTH_CAPACITY_FEATURES.md", "\n".join(md) + "\n")

    bl = [f"# H2 CAPACITY BLOCKERS — P11 ({now[:19]} UTC)", "",
          "What stops a launch from being executable at a realistic size, event by event. Descriptive; no strategy claim.", "",
          "| blocker | events | route |", "|---|---|---|",
          f"| no `bookDepth` archive on the launch day | {s['status_at_t0'].get('NO_DEPTH', 0)} | provider window (see `H2_PROVIDER_REQUEST_WINDOWS.csv`) or accept the event as capacity-unknown |",
          f"| archive generation without the 20-bps level | {s['resolution_bps'].get(100, 0)} | structural for pre-2026 launches: sub-1 % capacity cannot be observed free; the P4 live tape records 20 bps and tick L2 for every future launch |",
          f"| book too thin at t0 | {s['status_at_t0'].get('DEPTH_TOO_THIN', 0)} | not a data gap: the market really was empty at open; a test that enters at t0 + 15 min must use the +15 min book, which the features carry |",
          f"| spread proxy too wide at t0 | {s['status_at_t0'].get('SPREAD_TOO_WIDE', 0)} | same: a fact about the first minute, re-evaluated at each window |",
          f"| no trades in the first minute (spread unknown) | {sum(1 for r in results if r['windows'] and r['windows'][0].get('effective_spread_bps') is None and r['capacity_measured'])} | the +1 / +5 min windows usually have trades |", "",
          "## Paper-only launches", "",
          "Measured, never `CAPACITY_OK` within the first hour at 1 000 USDT within the finest band:", "",
          "| symbol | launch | resolution | t0 status | best window status | thinner side at t0 (USDT) |", "|---|---|---|---|---|---|"]
    for r in paper_only[:40]:
        w0 = r["windows"][0]; best = max(r["windows"], key=lambda w: w["capacity_score"])
        key = "20bps" if r.get("depth_resolution_bps") == 20 else "100bps"
        thin = min(w0.get("bid_depth_%s_native_usd" % key) or 0, w0.get("ask_depth_%s_native_usd" % key) or 0)
        bl.append(f"| {r['symbol']} | {r['t0'][:16]} | {r.get('depth_resolution_bps')} | {w0['capacity_status']} | {best['capacity_status']} (+{best['window_min']} min) | {thin:.0f} |")
    _write_atomic(out / "H2_CAPACITY_BLOCKERS.md", "\n".join(bl) + "\n")
    return {"capacity": s, "matrix": ms, "paper_only": len(paper_only), "ok_in_projection": len(ok_in_proj), "ok_any_in_projection": len(ok_any_in_proj)}


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--limit", type=int); a = ap.parse_args()
    res = compute_all(a.limit); print(json.dumps(write_reports(res), indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
