#!/usr/bin/env python3
"""
provider_request_windows.py -- le CSV a envoyer a un fournisseur (Tardis / Kaiko / CoinMetrics) pour un
devis ultra cible : une fenetre par lancement H2, lancement - 30 min -> lancement + 6 h, avec ce qui est
deja gratuit sur Binance Vision et ce qui ne l'est pas. Aucun achat n'est fait ici.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "data_acquisition"
MATRIX = OUT / "H2_LAUNCH_COVERAGE_MATRIX.json"
COLUMNS = ["symbol", "venue", "start_ts", "end_ts", "required_data_types", "reason", "priority", "can_backfill_free", "needs_paid_provider", "free_sources_available", "event_id", "launch_ts"]
PRE_S, POST_S = 30 * 60, 6 * 3600


def window(launch_ts: str) -> (str, str):
    t = datetime.fromisoformat(launch_ts.replace("Z", "+00:00"))
    return (t - timedelta(seconds=PRE_S)).isoformat(timespec="seconds"), (t + timedelta(seconds=POST_S)).isoformat(timespec="seconds")


def rows_from_matrix(rows: List[dict]) -> List[dict]:
    out = []
    for r in rows:
        s, e = window(r["launch_ts"]); vis = set((r.get("vision_available") or "").split(";")) - {""}
        free_l2 = "bookDepth" in vis; free_trades = "aggTrades" in vis; free_ref = ("indexPriceKlines" in vis or "premiumIndexKlines" in vis) and "markPriceKlines" in vis; free_oi = "metrics" in vis
        reasons = []
        if not free_l2:
            reasons.append("missing_l2")
        if not free_trades:
            reasons.append("missing_trades")
        if not free_ref:
            reasons.append("missing_mark_index")
        if not free_oi:
            reasons.append("missing_oi")
        needed = ["trades", "L2", "bookTicker", "mark", "index", "funding", "OI"]
        if reasons:
            prio = "P0"; needs_paid = True
        else:
            prio = "P1"; needs_paid = False; reasons = ["tick_l2_upgrade_optional (Vision bookDepth is 1-min snapshots; bookTicker absent for new symbols)"]
        out.append({"symbol": r["symbol"], "venue": "binance-futures", "start_ts": s, "end_ts": e, "required_data_types": ",".join(needed), "reason": ";".join(reasons), "priority": prio,
                    "can_backfill_free": (not needs_paid), "needs_paid_provider": needs_paid, "free_sources_available": ";".join(sorted(vis)), "event_id": r["event_id"], "launch_ts": r["launch_ts"]})
    return out


def write(rows: List[dict], out: Path = OUT) -> Dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "H2_PROVIDER_REQUEST_WINDOWS.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS); w.writeheader()
        for r in rows:
            w.writerow(r)
    p0 = [r for r in rows if r["priority"] == "P0"]
    return {"windows": len(rows), "P0_needs_paid": len(p0), "P1_optional_upgrade": len(rows) - len(p0), "P0_symbols": [r["symbol"] for r in p0],
            "hours_requested_P0": round(len(p0) * (PRE_S + POST_S) / 3600, 1), "hours_requested_all": round(len(rows) * (PRE_S + POST_S) / 3600, 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--matrix", default=str(MATRIX)); a = ap.parse_args()
    m = json.loads(Path(a.matrix).read_text()); rows = rows_from_matrix(m["rows"]); print(json.dumps(write(rows), indent=1))


if __name__ == "__main__":
    main()
