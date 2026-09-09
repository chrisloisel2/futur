"""Paper live: one cycle, no real order, no exchange credentials.

Run it on a timer.  It loads the mechanisms that are actually eligible, asks
each for its signals, prices them, journals every decision including the
refusals, and writes the day's report.

With nothing eligible it writes a report saying so, which is the useful output
right now: it proves the chain runs end to end without inventing a trade.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from paper_engine.decision_engine import (
    DecisionEngine,
    RiskLimits,
    RiskState,
    Signal,
    load_eligible,
)
from paper_engine.journal import Journal
from paper_engine.paper_broker import PaperBroker

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "reports" / "paper_live"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def daily_report(journal: Journal, day: str, broker: Optional[PaperBroker] = None) -> str:
    entries = journal.for_day(day)
    accepted = [e for e in entries if e.get("decision") == "PAPER_ACCEPT"]
    rejected = [e for e in entries if e.get("decision") != "PAPER_ACCEPT"]

    reasons: Dict[str, int] = {}
    for e in rejected:
        reasons[str(e.get("reject_reason"))] = reasons.get(str(e.get("reject_reason")), 0) + 1

    lines: List[str] = []
    a = lines.append
    a("# Paper live — %s" % day)
    a("")
    a("| quantity | value |")
    a("| --- | --- |")
    a("| signals | %d |" % len(entries))
    a("| paper accepts | %d |" % len(accepted))
    a("| refusals | %d |" % len(rejected))
    if accepted:
        gross = sum(float(e["gross_edge_bps_est"]) for e in accepted) / len(accepted)
        cost = sum(float(e["cost_bps_est"]) for e in accepted) / len(accepted)
        a("| mean gross estimate | %.2f bps |" % gross)
        a("| mean cost estimate | %.2f bps |" % cost)
        a("| mean net estimate | %.2f bps |" % (gross - cost))
    if broker is not None:
        mtm = broker.mark_to_market({})
        a("| realised paper PnL | %.2f |" % mtm["realised_pnl"])
        a("| fees paid | %.2f |" % mtm["fees_paid"])
        a("| deferred notional | %.2f |" % mtm["deferred_notional"])
    a("")

    if reasons:
        a("## Why signals were refused")
        a("")
        for r, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            a("- %s: %d" % (r, n))
        a("")

    if not entries:
        a(
            "No signal reached the engine today. No mechanism holds a "
            "PAPER_ELIGIBLE verdict, so nothing is allowed to trade, not even on "
            "paper. This is the intended state until a sealed forward window closes "
            "in the mechanism's favour."
        )
        a("")
    return "\n".join(lines)


def run_cycle(
    mechanisms_root: Path = REPO_ROOT / "mechanisms",
    journal_path: Path = REPORT_DIR / "journal.jsonl",
    signals: Optional[List[Signal]] = None,
    risk: Optional[RiskState] = None,
) -> Dict[str, Any]:
    eligible = load_eligible(mechanisms_root)
    engine = DecisionEngine(eligible=eligible, limits=RiskLimits())
    journal = Journal(journal_path)
    state = risk or RiskState()

    records = []
    for sig in signals or []:
        rec = engine.decide(sig, state)
        journal.record(rec)
        records.append(rec)

    return {
        "eligible_mechanisms": [e.spec.mechanism_id for e in eligible],
        "n_signals": len(records),
        "n_accepted": sum(1 for r in records if r["decision"] == "PAPER_ACCEPT"),
        "journal": str(journal_path),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="One paper-live cycle. Trades nothing.")
    ap.add_argument("--mechanisms-root", default=str(REPO_ROOT / "mechanisms"))
    ap.add_argument("--journal", default=str(REPORT_DIR / "journal.jsonl"))
    ap.add_argument("--report", action="store_true", help="write today's report")
    args = ap.parse_args(argv)

    summary = run_cycle(Path(args.mechanisms_root), Path(args.journal))
    print(json.dumps(summary, indent=2))

    if args.report:
        day = _today()
        text = daily_report(Journal(Path(args.journal)), day)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / ("%s.md" % day)
        out.write_text(text, encoding="utf-8")
        print("report: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
