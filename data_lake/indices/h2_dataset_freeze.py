#!/usr/bin/env python3
"""
h2_dataset_freeze.py -- geler ce qui est etabli, classer chaque evenement, et decider s'il y a matiere a
rouvrir un budget de test. Ce module ne lance aucun test, ne calcule aucun rendement, n'ecrit aucun verdict alpha.

La porte de decision est explicite et refuse par defaut :
    >= MIN_CLEAN evenements propres ET cout d'execution connu  -> preparer un preenregistrement (qui reste a signer)
    sinon                                                      -> pas de test, et la raison exacte
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_lake.indices import h2_clean_dataset_builder as B
from data_lake.indices import h2_event_classifier as K

ROOT = Path(__file__).resolve().parents[2]
OUT_ACQ = ROOT / "reports" / "data_acquisition"
OUT_PRE = ROOT / "reports" / "prereg"
FROZEN = ROOT / "data" / "h2_clean_dataset"
MIN_CLEAN = 80


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def _sha_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def projection(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Si les deux manques levables etaient levés (frais reels par cle, capacite derivee des archives de
    profondeur deja sur disque), combien d'evenements seraient propres, et de quelle structure ?"""
    lifted = [dict(e, actual_fee_known=True, capacity_known=True) for e in events]
    cl = [K.classify(e) for e in lifted]
    return {"if_fee_and_capacity_resolved": K.summarise(cl), "structural_classes": dict(Counter(c["classification"] for c in cl if not c["blocking"]))}


def decide(summary: Dict[str, Any], execution: Dict[str, Any], proj: Dict[str, Any]) -> Dict[str, Any]:
    clean = summary["eligible"]
    blockers: List[str] = []
    if clean < MIN_CLEAN:
        blockers.append("only %d clean events (threshold %d)" % (clean, MIN_CLEAN))
    if not execution.get("actual_fee_known"):
        blockers.append("execution cost is unknown: no read-only API key, so the fee actually charged on this account was never read")
    dom = summary.get("dominant_blocker")
    if dom == K.UNKNOWN_PRECEDENCE:
        blockers.append("venue precedence is the dominant blocker: improve the P9 venue clients before testing")
    return {"decision": "NO_TEST" if blockers else "PREPARE_PREREGISTRATION", "clean_events": clean, "threshold": MIN_CLEAN,
            "blockers": blockers, "dominant_blocker": dom,
            "would_be_clean_if_unblocked": proj["if_fee_and_capacity_resolved"]["eligible"],
            "budget_reopen_requested": not blockers}


def freeze(require_actual_fees: bool = True) -> Dict[str, Any]:
    d = B.build()
    classified = [K.classify(e, require_actual_fees=require_actual_fees) for e in d["events"]]
    by_id = {e["event_id"]: e for e in d["events"]}
    summary = K.summarise(classified)
    proj = projection(d["events"])
    dec = decide(summary, d["execution"], proj)
    eligible = [c for c in classified if not c["blocking"]]
    excluded = [c for c in classified if c["blocking"] and c["classification"] != K.PROVIDER_NEEDED]
    provider = [c for c in classified if c["classification"] == K.PROVIDER_NEEDED]
    payload = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_commit": git_commit(),
               "min_clean_threshold": MIN_CLEAN, "require_actual_fees": require_actual_fees,
               "inputs": d["inputs"], "execution": d["execution"], "summary": summary, "projection": proj, "decision": dec,
               "eligible_event_ids": [c["event_id"] for c in eligible],
               "excluded": [{"event_id": c["event_id"], "symbol": c["symbol"], "classification": c["classification"], "reasons": c["reasons"]} for c in excluded],
               "provider_needed": [{"event_id": c["event_id"], "symbol": c["symbol"], "missing": by_id[c["event_id"]]["missing_datasets"]} for c in provider],
               "source_manifests": {c["event_id"]: {"vision_manifest_sha256": by_id[c["event_id"]]["vision_manifest_sha256"],
                                                    "body_raw_hash": by_id[c["event_id"]]["body_raw_hash"]} for c in classified},
               "classified": classified, "no_alpha_test": True, "no_return_computed": True, "capital_deployable": False}
    blob = json.dumps({k: v for k, v in payload.items() if k != "frozen_at_utc"}, sort_keys=True, default=str)
    payload["freeze_sha256"] = _sha_text(blob)
    FROZEN.mkdir(parents=True, exist_ok=True)
    _write_atomic(FROZEN / ("freeze_%s.json" % payload["freeze_sha256"][:12]), json.dumps(payload, indent=1, ensure_ascii=False, default=str))
    return payload


def write_reports(fz: Dict[str, Any], out_acq: Optional[Path] = None, out_pre: Optional[Path] = None) -> Dict[str, Any]:
    out_acq = Path(out_acq) if out_acq else OUT_ACQ; out_pre = Path(out_pre) if out_pre else OUT_PRE
    out_acq.mkdir(parents=True, exist_ok=True); out_pre.mkdir(parents=True, exist_ok=True)
    s, dec, proj, ex = fz["summary"], fz["decision"], fz["projection"], fz["execution"]
    _write_atomic(out_acq / "H2_CLEAN_DATASET_FREEZE.json", json.dumps(fz, indent=1, ensure_ascii=False, default=str) + "\n")

    md = [f"# H2 CLEAN DATASET FREEZE — P10 ({fz['frozen_at_utc'][:19]} UTC)", "",
          f"Freeze `{fz['freeze_sha256'][:16]}…` at commit `{fz['git_commit'][:12]}`. Assembled from what P6–P9 wrote; "
          "no download, no price joined, no return computed, no alpha verdict, no budget consumed. "
          "`capital_deployable` remains **false**.", "",
          "## Decision", "",
          f"**{dec['decision']}** — {dec['clean_events']} clean events against a threshold of {dec['threshold']}.", ""]
    if dec["blockers"]:
        md += ["Blockers, in the order they must be cleared:", ""] + [f"{i}. {b}" for i, b in enumerate(dec["blockers"], 1)] + [""]
    md += [f"If the two liftable gaps were closed, **{dec['would_be_clean_if_unblocked']}** events would be clean, "
           f"distributed as {proj['structural_classes']}.", "",
           "## Inputs", "", "| input | state |", "|---|---|",
           f"| Vision window manifests (P6) | {fz['inputs']['vision_manifests']} |",
           f"| announcement bodies (P7) | {fz['inputs']['bodies']} |",
           f"| execution report (P8) | {'present' if fz['inputs']['execution_report'] else 'absent'}; actual fee known: **{ex['actual_fee_known']}** |",
           f"| cross-venue precedence (P9) | {fz['inputs']['precedence']} assets |", "",
           "## Classification", "", "| class | events | kind |", "|---|---|---|"]
    for k, v in s["by_class"].items():
        md.append(f"| `{k}` | {v} | {'blocking' if k in K.BLOCKING else 'structural'} |")
    md += ["", f"Eligible: **{s['eligible']}**. Blocked: {s['blocked']}. Dominant blocker: `{s['dominant_blocker']}`.", "",
           "## What is frozen", "",
           f"- eligible event list ({len(fz['eligible_event_ids'])} ids)",
           f"- excluded list with a reason per event ({len(fz['excluded'])})",
           f"- provider-needed list ({len(fz['provider_needed'])})",
           "- per event: the sha256 of its Vision window manifest and of its announcement body payload",
           "- `no_alpha_test: true`, `no_return_computed: true`, `capital_deployable: false`", ""]
    _write_atomic(out_acq / "H2_CLEAN_DATASET_FREEZE.md", "\n".join(md) + "\n")

    cls_md = [f"# H2 EVENT CLASSIFICATION — P10 ({fz['frozen_at_utc'][:19]} UTC)", "",
              "Eight classes. The first five are impediments — the data is missing or doubtful — and are tested first; "
              "the last three describe the market and are only reached by an event with no impediment. A clean event is "
              "therefore one whose class describes a market rather than a hole.", "",
              "| class | events | meaning |", "|---|---|---|"]
    MEANING = {K.BAD_TIMESTAMP: "the event has no reliable time", K.INSUFFICIENT_MARKET_STATE: "state around the event is incomplete",
               K.PROVIDER_NEEDED: "what is missing is not published free", K.INSUFFICIENT_EXECUTION_DATA: "the real cost of executing is unknown",
               K.UNKNOWN_PRECEDENCE: "we do not know whether the token was already priced elsewhere",
               K.OTHER_VENUE_FIRST: "it was", K.BINANCE_SPOT_FIRST: "Binance spot traded before the perpetual",
               K.TRUE_BINANCE_PERP_FIRST: "the Binance perpetual is the first market anywhere"}
    for k in K.ALL_CLASSES:
        n = s["by_class"].get(k, 0)
        if n:
            cls_md.append(f"| `{k}` | {n} | {MEANING[k]} |")
    cls_md += ["", "## Why each blocked event is blocked", "", "| symbol | class | reason |", "|---|---|---|"]
    for c in fz["classified"]:
        if c["blocking"]:
            cls_md.append(f"| {c['symbol']} | `{c['classification']}` | {'; '.join(c['reasons'])} |")
    _write_atomic(out_acq / "H2_EVENT_CLASSIFICATION.md", "\n".join(cls_md[:60] + (["", f"(first {min(50, s['blocked'])} of {s['blocked']} blocked events)"] if s["blocked"] > 50 else [])) + "\n")

    cols = ["event_id", "symbol", "asset", "launch_ts", "classification", "blocking", "coverage_score", "venue_precedence", "timestamp_gap_min", "reasons"]
    with open(out_acq / "H2_FINAL_COVERAGE_MATRIX.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for c in fz["classified"]:
            w.writerow({**{k: c.get(k) for k in cols if k != "reasons"}, "reasons": "; ".join(c["reasons"])})
    _write_atomic(out_acq / "H2_FINAL_COVERAGE_MATRIX.json", json.dumps({"generated_at_utc": fz["frozen_at_utc"], "freeze_sha256": fz["freeze_sha256"],
                  "summary": s, "rows": fz["classified"], "no_alpha_test": True}, indent=1, ensure_ascii=False, default=str) + "\n")
    return {"decision": dec, "summary": s}
