#!/usr/bin/env python3
"""
alpha_zone_readiness.py -- ou en est chaque zone : prete en donnees, prete en execution, prete en capacite,
mecanisme defini, preenregistrement possible, test autorise. Lit les sorties de P6 a P11, ne calcule aucun
retour, n'ecrit aucun verdict alpha. La decision par defaut est NO_ALPHA_TEST tant que les frais reels et la
capacite ne sont pas confirmes ; ce module ne peut pas produire autre chose sans que ces deux faits changent.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
ACQ = ROOT / "reports" / "data_acquisition"; EXE = ROOT / "reports" / "execution"; OUT = ROOT / "reports" / "alpha_zone"
DECISIONS = ("NO_ALPHA_TEST", "READY_FOR_PREREG_ONLY", "READY_FOR_BUDGET_REOPEN_REQUEST", "READY_FOR_FORWARD_ONLY_COLLECTION")
BLOCKER_KINDS = ("free_blocker", "credential_blocker", "provider_blocker", "forward_only_blocker", "conceptual_blocker")
ZONES = ("H2_POOLED", "TRUE_FIRST_LISTING", "OTHER_VENUE_FIRST", "MEXC_TO_BINANCE", "H3_DELISTING", "FORCED_FLOW_PUBLIC", "PRE_LIQUIDATION_PRESSURE")


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def gather() -> Dict[str, Any]:
    pop = _load(ACQ / "H2_CAUSAL_POPULATIONS.json", {}) or {}
    cap = _load(ACQ / "H2_DEPTH_CAPACITY_FEATURES.json", {}) or {}
    exe = _load(EXE / "ACCOUNT_EXECUTION_REALITY_COLLECTED.json", {}) or {}
    mexc = _load(ACQ / "MEXC_PRE_BINANCE_FEATURES.json", {}) or {}
    fz = _load(ACQ / "H2_CLEAN_DATASET_FREEZE.json", {}) or {}
    prov = _load(ACQ / "H2_PROVIDER_REQUEST_WINDOWS.csv", None)
    s = (pop.get("summary") or {}); a = s.get("answers") or {}; cs = cap.get("summary") or {}
    fee_known = exe.get("actual_futures_taker_fee_bps") is not None
    return {"populations": s.get("by_population") or {}, "answers": a, "lead_buckets": s.get("by_lead_bucket") or {},
            "capacity": {"measured": cs.get("measured", 0), "n": cs.get("n", 174), "ok_any_window": cs.get("capacity_ok_at_any_window", 0), "ok_at_1h": cs.get("capacity_ok_at_1h", 0),
                         "status_t0": cs.get("status_at_t0") or {}, "resolution": cs.get("resolution_bps") or {}, "spread_median_bps": ((cs.get("effective_spread_t0_bps") or {}).get("median"))},
            "execution": {"mode": exe.get("mode", "no_credentials"), "fee_known": fee_known, "cost_chain": exe.get("cost_chain_status") or {"H2": "unknown", "H3": "unknown"}},
            "mexc": {"n": mexc.get("n", 0), "coverage": mexc.get("coverage") or {}},
            "p10": {"decision": (fz.get("decision") or {}).get("decision"), "clean": (fz.get("decision") or {}).get("clean_events"), "would_be": (fz.get("decision") or {}).get("would_be_clean_if_unblocked")},
            "mechanisms": {"other_venue_first": "other_venue_first_binance_perp_effect_v1", "mexc": "mexc_to_binance_migration_effect_v1", "true_first": "true_first_listing_forward_only_v1"}}


def zones(g: Dict[str, Any]) -> List[Dict[str, Any]]:
    P, A, C, E = g["populations"], g["answers"], g["capacity"], g["execution"]
    other = sum(P.get(k, 0) for k in ("MEXC_FIRST", "OKX_FIRST", "BYBIT_FIRST", "KUCOIN_FIRST", "OTHER_VENUE_FIRST"))
    cap_ready = C["measured"] >= 0.8 * C["n"]; exec_ready = E["fee_known"]
    rows = [
        {"zone": "H2_POOLED", "event_count": C["n"], "data_ready": True, "execution_ready": exec_ready, "capacity_ready": cap_ready, "mechanism_ready": False, "prereg_ready": False, "alpha_test_allowed": False,
         "verdict": "dead / invalid population", "reason": "one number averaged over %d MEXC-first, %d other-venue-first and %d true-first events; retired as a population (regard seq 8 burned)" % (P.get("MEXC_FIRST", 0), other - P.get("MEXC_FIRST", 0), P.get("TRUE_BINANCE_PERP_FIRST", 0))},
        {"zone": "TRUE_FIRST_LISTING", "event_count": P.get("TRUE_BINANCE_PERP_FIRST", 0), "data_ready": False, "execution_ready": exec_ready, "capacity_ready": False, "mechanism_ready": True, "prereg_ready": False, "alpha_test_allowed": False,
         "verdict": "forward-only / too few historical events", "reason": "%d historical events; ~14 400 needed at the observed dispersion; the P4 tape must capture certified births live" % P.get("TRUE_BINANCE_PERP_FIRST", 0)},
        {"zone": "OTHER_VENUE_FIRST", "event_count": other, "data_ready": True, "execution_ready": exec_ready, "capacity_ready": cap_ready, "mechanism_ready": True, "prereg_ready": exec_ready and cap_ready, "alpha_test_allowed": False,
         "verdict": "closest research zone", "reason": "%d dated events, external reference before t0, capacity measured for %d/%d, mechanism defined; blocked by the actual fee (credential)" % (other, C["measured"], C["n"])},
        {"zone": "MEXC_TO_BINANCE", "event_count": P.get("MEXC_FIRST", 0), "data_ready": g["mexc"]["coverage"].get("full", 0) + g["mexc"]["coverage"].get("daily_only", 0) >= 0.9 * max(1, g["mexc"]["n"]), "execution_ready": exec_ready, "capacity_ready": cap_ready, "mechanism_ready": True, "prereg_ready": exec_ready and cap_ready, "alpha_test_allowed": False,
         "verdict": "highest mechanistic interest", "reason": "%d MEXC-first events with a pre-Binance tape for %d; conditioning state exists; same credential blocker" % (P.get("MEXC_FIRST", 0), g["mexc"]["coverage"].get("full", 0) + g["mexc"]["coverage"].get("daily_only", 0))},
        {"zone": "H3_DELISTING", "event_count": 56, "data_ready": True, "execution_ready": exec_ready, "capacity_ready": True, "mechanism_ready": True, "prereg_ready": False, "alpha_test_allowed": False,
         "verdict": "forward sealed / needs future events", "reason": "sealed 2026-09-11 -> 2028-09-11; only announcements after the seal count; measured cost required at the look"},
        {"zone": "FORCED_FLOW_PUBLIC", "event_count": 368, "data_ready": True, "execution_ready": exec_ready, "capacity_ready": True, "mechanism_ready": True, "prereg_ready": False, "alpha_test_allowed": False,
         "verdict": "rejected as direct signal", "reason": "regard seq 9: -7 bps continuation, +13.8 bps reversal under a 43 bps wall; the public message is the corpse, not the signal"},
        {"zone": "PRE_LIQUIDATION_PRESSURE", "event_count": 0, "data_ready": False, "execution_ready": exec_ready, "capacity_ready": False, "mechanism_ready": False, "prereg_ready": False, "alpha_test_allowed": False,
         "verdict": "possible future family, not built here", "reason": "would need OI build-up, mark/index divergence and thin depth BEFORE a liquidation; the P4 tape records the state but no mechanism is defined"},
    ]
    return rows


def blockers(g: Dict[str, Any]) -> List[Dict[str, Any]]:
    C, E, A, P = g["capacity"], g["execution"], g["answers"], g["populations"]
    out = [
        {"kind": "credential_blocker", "blocker": "actual account fees unknown (no read-only API key)", "affects": ["OTHER_VENUE_FIRST", "MEXC_TO_BINANCE", "H3_DELISTING", "TRUE_FIRST_LISTING"], "events": A.get("5_blocked_only_by_cost", 0), "route": "set BINANCE_READONLY_API_KEY / _SECRET to a key with no trading, withdrawal or transfer permission; re-run P8 --collect", "cost": "free", "cleared": E["fee_known"]},
        {"kind": "free_blocker", "blocker": "capacity at 20 bps not observable for pre-2026 launches (archive generation)", "affects": ["OTHER_VENUE_FIRST", "MEXC_TO_BINANCE"], "events": C["resolution"].get("100", C["resolution"].get(100, 0)), "route": "structural for history; the P4 live tape records 20 bps and tick L2 for every future launch; a preregistration may accept 1 % capacity as UNKNOWN-but-fills", "cost": "free", "cleared": False},
        {"kind": "free_blocker", "blocker": "spread and slippage in the cost chains are still declared numbers", "affects": ["OTHER_VENUE_FIRST", "MEXC_TO_BINANCE"], "events": C["measured"], "route": "the depth features now carry an effective-spread proxy and slippage bounds per window; a preregistration must name which window it uses", "cost": "free", "cleared": False},
        {"kind": "free_blocker", "blocker": "venue precedence unknown", "affects": ["OTHER_VENUE_FIRST"], "events": P.get("UNKNOWN_PRECEDENCE", 0) + P.get("GATE_FIRST_UNKNOWN_DATE", 0), "route": "Gate first-candle per pair; OKX/Bybit announcement archives via the P7 body archiver", "cost": "free", "cleared": False},
        {"kind": "free_blocker", "blocker": "announced opening time and first traded bar disagree by more than 15 min", "affects": ["OTHER_VENUE_FIRST", "MEXC_TO_BINANCE"], "events": A.get("4_bad_timestamps", 0), "route": "human decision per event on which timestamp is the event; excluded by rule until then", "cost": "free", "cleared": False},
        {"kind": "provider_blocker", "blocker": "no free depth or index reference on the launch day", "affects": ["OTHER_VENUE_FIRST"], "events": 7, "route": "H2_PROVIDER_REQUEST_WINDOWS.csv, P0 rows only (targeted windows, never a subscription)", "cost": "paid, small", "cleared": False},
        {"kind": "forward_only_blocker", "blocker": "too few true first listings in history", "affects": ["TRUE_FIRST_LISTING"], "events": P.get("TRUE_BINANCE_PERP_FIRST", 0), "route": "P4 market_state_tape captures certified births live; no backfill can create more", "cost": "time", "cleared": False},
        {"kind": "forward_only_blocker", "blocker": "H3 seal counts only announcements after 2026-09-11", "affects": ["H3_DELISTING"], "events": 0, "route": "wait; ~18-20 months at the 2025 rate", "cost": "time", "cleared": False},
        {"kind": "conceptual_blocker", "blocker": "MEXC pre-Binance volume may be wash-traded; pump and volume features inherit it", "affects": ["MEXC_TO_BINANCE"], "events": P.get("MEXC_FIRST", 0), "route": "compare MEXC volume to OKX/Bybit-first events; treat volume features as suspect until then", "cost": "free", "cleared": False},
        {"kind": "conceptual_blocker", "blocker": "the public liquidation message arrives after the move", "affects": ["FORCED_FLOW_PUBLIC"], "events": 368, "route": "none: structural; the family is closed as a direct signal", "cost": "none", "cleared": False},
    ]
    return out


def decide(g: Dict[str, Any], z: List[Dict[str, Any]]) -> Dict[str, Any]:
    fee, cap = g["execution"]["fee_known"], g["capacity"]["measured"] >= 0.8 * g["capacity"]["n"]
    if not fee:
        d = "NO_ALPHA_TEST"; why = "actual fees unknown (no read-only key); the published fee may reject, never promote"
    elif not cap:
        d = "NO_ALPHA_TEST"; why = "capacity not measured for enough events"
    else:
        d = "READY_FOR_PREREG_ONLY"; why = "fees and capacity confirmed; a preregistration may be written, no budget is reopened by this report"
    return {"decision": d, "why": why, "fees_confirmed": fee, "capacity_confirmed": cap, "capital_deployable": False, "budget": 0, "alpha_test_launched_by_this_branch": False}


def answers(g: Dict[str, Any], z: List[Dict[str, Any]], b: List[Dict[str, Any]], d: Dict[str, Any]) -> Dict[str, str]:
    P = g["populations"]
    return {
        "1_closest_alpha_zone": "OTHER_VENUE_FIRST (%d dated events; mechanism other_venue_first_binance_perp_effect_v1); its MEXC_TO_BINANCE sub-zone (%d events) is where the mechanism is most specific" % (z[2]["event_count"], P.get("MEXC_FIRST", 0)),
        "2_dead_zone": "H2_POOLED (invalid as a single population) and FORCED_FLOW_PUBLIC as a direct signal",
        "3_forward_only_zone": "TRUE_FIRST_LISTING (%d historical events) and H3_DELISTING (sealed, future announcements only)" % P.get("TRUE_BINANCE_PERP_FIRST", 0),
        "4_data_ready_not_execution_ready": "OTHER_VENUE_FIRST and MEXC_TO_BINANCE: reference price, capacity, pre-Binance tape and announcement times exist; the fee actually charged does not",
        "5_free_blockers": "; ".join(x["blocker"] for x in b if x["kind"] == "free_blocker"),
        "6_provider_blockers": "; ".join("%s (%d events)" % (x["blocker"], x["events"]) for x in b if x["kind"] == "provider_blocker"),
        "7_hypothesis_worth_a_future_prereg": "mexc_to_binance_migration_effect_v1 with ONE pre-Binance conditioning named in advance, or other_venue_first_binance_perp_effect_v1 on the full dated population with the first venue's price as reference",
        "8_hypothesis_not_to_test": "H2_POOLED again at any horizon; TRUE_FIRST_LISTING on history; any hypothesis whose reference is BTC instead of the first venue; anything conditioned after a look",
        "9_next_concrete_act": "configure a read-only key (no trading / withdrawal / transfer) and re-run P8; then write a preregistration for one named population with the first venue's price as reference — not a bot, not a test",
        "10_capital_deployable_remains_false": "yes; budget 0; no test launched by this branch; decision %s" % d["decision"],
    }


def build() -> Dict[str, Any]:
    g = gather(); z = zones(g); b = blockers(g); d = decide(g, z); ans = answers(g, z, b, d)
    return {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "inputs": g, "executive_verdict": {r["zone"]: r["verdict"] for r in z},
            "readiness": z, "blockers": b, "final_decision": d, "answers": ans, "no_alpha_test": True, "no_return_computed": True, "capital_deployable": False}


def write_reports(doc: Dict[str, Any], out: Optional[Path] = None) -> Dict[str, Path]:
    out = Path(out) if out else OUT; out.mkdir(parents=True, exist_ok=True)
    now = doc["generated_at_utc"]; z, b, d, ans, g = doc["readiness"], doc["blockers"], doc["final_decision"], doc["answers"], doc["inputs"]
    _write_atomic(out / "ALPHA_ZONE_READINESS.json", json.dumps(doc, indent=1, ensure_ascii=False, default=str) + "\n")
    yn = lambda v: "yes" if v else "no"
    md = [f"# ALPHA ZONE READINESS — P11 ({now[:19]} UTC)", "",
          "What is ready and what is not, zone by zone, from what P6–P11 measured. No return computed, no verdict, no budget consumed, "
          "no test launched. `capital_deployable` remains **false**.", "",
          "## Executive verdict", ""] + [f"- `{zn}`: {v}" for zn, v in doc["executive_verdict"].items()] + ["",
          "## Readiness table", "", "| zone | event_count | data_ready | execution_ready | capacity_ready | mechanism_ready | prereg_ready | alpha_test_allowed | reason |", "|---|---|---|---|---|---|---|---|---|"]
    for r in z:
        md.append(f"| `{r['zone']}` | {r['event_count']} | {yn(r['data_ready'])} | {yn(r['execution_ready'])} | {yn(r['capacity_ready'])} | {yn(r['mechanism_ready'])} | {yn(r['prereg_ready'])} | **{yn(r['alpha_test_allowed'])}** | {r['reason']} |")
    md += ["", "## Blockers", "", "| kind | blocker | affects | events | route | cost | cleared |", "|---|---|---|---|---|---|---|"]
    for x in b:
        md.append(f"| `{x['kind']}` | {x['blocker']} | {', '.join(x['affects'])} | {x['events']} | {x['route']} | {x['cost']} | {yn(x['cleared'])} |")
    md += ["", "## The ten answers", ""] + [f"{k.split('_', 1)[0]}. **{k.split('_', 1)[1].replace('_', ' ')}** — {v}" for k, v in ans.items()] + ["",
           "## Final decision", "", f"**{d['decision']}** — {d['why']}.", "",
           f"Fees confirmed: {yn(d['fees_confirmed'])}. Capacity confirmed: {yn(d['capacity_confirmed'])} ({g['capacity']['measured']} of {g['capacity']['n']} measured; "
           f"`CAPACITY_OK` at some window in the first hour for {g['capacity']['ok_any_window']}; median effective-spread proxy at t0 {g['capacity']['spread_median_bps']} bps). "
           f"Budget: {d['budget']}. capital_deployable: **{str(d['capital_deployable']).lower()}**. Alpha test launched by this branch: {yn(d['alpha_test_launched_by_this_branch'])}.", "",
           "## What this branch established", "",
           f"- H2 is {g['populations']} — not one population. Regard seq 8 averaged them; it is retired, not re-read.",
           f"- MEXC before Binance: {g['mexc']['n']} events, tape for {g['mexc']['coverage'].get('full', 0) + g['mexc']['coverage'].get('daily_only', 0)}; median 7-day return before the launch is descriptive material for a conditioning, not a signal.",
           f"- Capacity: measured for {g['capacity']['measured']} launches; the free archive resolves 20 bps only from 2026 ({g['capacity']['resolution']}); the P4 tape closes that for the future.",
           f"- Execution: mode `{g['execution']['mode']}`; cost chains {g['execution']['cost_chain']}. A credential, not a dataset, is what separates the closest zone from a preregistration.", ""]
    _write_atomic(out / "ALPHA_ZONE_READINESS.md", "\n".join(md) + "\n")
    bl = [f"# ALPHA ZONE BLOCKERS ({now[:19]} UTC)", ""]
    for kind in BLOCKER_KINDS:
        items = [x for x in b if x["kind"] == kind]
        if not items:
            continue
        bl += [f"## {kind}", ""] + [f"- **{x['blocker']}** — affects {', '.join(x['affects'])}; {x['events']} events; route: {x['route']}; cost: {x['cost']}; cleared: {yn(x['cleared'])}" for x in items] + [""]
    bl += ["## Order of clearing", "", "1. credential_blocker (one key, no permission) — unlocks execution_ready for every zone at once.",
           "2. free_blockers on the closest zone (spread/slippage window named in the prereg; precedence for the Gate-only assets; the 8 timestamp disagreements decided by a human).",
           "3. provider_blocker only for the 7 windows, only if a preregistration needs them.", "4. forward_only_blockers are cleared by time, not by work.", ""]
    _write_atomic(out / "ALPHA_ZONE_BLOCKERS.md", "\n".join(bl) + "\n")
    nd = [f"# ALPHA ZONE — next decision ({now[:19]} UTC)", "", f"**{d['decision']}**", "", d["why"] + ".", "",
          "## The next allowed act", "", ans["9_next_concrete_act"] + ".", "",
          "## What is not allowed after this branch", "", "- launching a bot, an order, a live or paper strategy;", "- re-testing H2_POOLED at any horizon;",
          "- testing TRUE_FIRST_LISTING on its %d historical events;" % g["populations"].get("TRUE_BINANCE_PERP_FIRST", 0),
          "- using the published fee to promote anything;", "- reopening budget: enrichment credits no episodes, and this report requests none.", "",
          "## What the next branch may be", "", "Either a preregistration for one named population (OTHER_VENUE_FIRST or MEXC_FIRST) with the first venue's price as reference, "
          "written after the read-only key exists — or forward-only collection (P4 keeps running). Nothing else.", ""]
    _write_atomic(out / "ALPHA_ZONE_NEXT_DECISION.md", "\n".join(nd) + "\n")
    rows = []
    data_classes = [("announcement_ts", "P2 tape", "have"), ("announced_opening_ts", "P7 body", "have"), ("first_traded_bar", "P6 Vision", "have"), ("mark_index_premium_1m", "P6 Vision", "have"),
                    ("trades_tick", "P6 Vision aggTrades", "have"), ("depth_1min_bands", "P6 Vision bookDepth", "have (20 bps only from 2026)"), ("open_interest_5m", "P6 Vision metrics", "have"),
                    ("funding", "P6 Vision", "have (3 pending)"), ("capacity_features", "P11", "measured for %d" % g["capacity"]["measured"]), ("venue_precedence", "P9", "%d settled" % (174 - g["populations"].get("UNKNOWN_PRECEDENCE", 0) - g["populations"].get("GATE_FIRST_UNKNOWN_DATE", 0))),
                    ("pre_binance_tape", "P11 MEXC", "%d of %d" % (g["mexc"]["coverage"].get("full", 0) + g["mexc"]["coverage"].get("daily_only", 0), g["mexc"]["n"])), ("actual_fees", "P8", g["execution"]["mode"]),
                    ("leverage_brackets", "P8", g["execution"]["mode"]), ("borrowability", "P8", g["execution"]["mode"]), ("tick_l2_history", "provider", "not held; 7 windows P0"), ("live_market_state", "P4 tape", "running; no true birth captured yet")]
    for r in z:
        for name, src, st in data_classes:
            rows.append({"zone": r["zone"], "data_class": name, "source": src, "status": st, "zone_verdict": r["verdict"]})
    with open(out / "ALPHA_ZONE_DATA_MAP.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["zone", "data_class", "source", "status", "zone_verdict"]); w.writeheader(); w.writerows(rows)
    _write_atomic(out / "ALPHA_ZONE_DATA_MAP.json", json.dumps({"generated_at_utc": now, "rows": rows, "no_alpha_test": True}, indent=1, ensure_ascii=False) + "\n")
    return {"readiness": out / "ALPHA_ZONE_READINESS.md"}


def main():
    doc = build(); write_reports(doc); print(json.dumps({"decision": doc["final_decision"], "verdict": doc["executive_verdict"]}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
