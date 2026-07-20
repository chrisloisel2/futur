#!/usr/bin/env python3
"""
scripts/report_alpha20_daily.py
─────────────────────────────────────────────────────────────────────────────
TABLEAU QUOTIDIEN OFFICIEL ALPHA_20 (ordre R0, 2026-07-20). Une ligne par
jour, chaque colonne vient d'une source auditée — jamais d'estimation cachée :

  PnL brut (carry+basis+longs) · fees · slippage(∈fees, taker) · borrow ·
  provision fiscale MTD · PnL net · ES99 1 j · marge utilisée (proxy IM 10 %) ·
  delta net · tracking label/exécution (3 erreurs séparées) · résidu de
  réconciliation (gate forward) + intervalles consécutifs verts.

Les deltas _1d se calculent contre le fichier de la VEILLE (append-only) ;
premier run = cumuls depuis l'ouverture. ES99 : empirique sur les rendements
1 h de l'historique ×√24 (méthode affichée, null si < 100 points).

Sorties : reports/alpha20/daily/DAILY_<date>.json + ligne dans
reports/alpha20/DAILY_TABLE.md. Timer : futur-alpha20-daily (07:50 machine).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alpha20.accounting.tax_engine import provision_for_month  # noqa: E402

OUT = ROOT / "reports" / "alpha20" / "daily"
TABLE = ROOT / "reports" / "alpha20" / "DAILY_TABLE.md"
MH_DIR = ROOT / "reports" / "paper_live" / "mh_events"
GATE = ROOT / "reports" / "alpha20" / "gate_state.json"


def _mongo_doc():
    from pymongo import MongoClient
    return MongoClient("mongodb://localhost:27017",
                       serverSelectionTimeoutMS=5000
                       ).futur_ui.paper_portfolio.find_one({"_id": "main"})


def _es99_1d(history) -> dict:
    h = pd.DataFrame(history)
    if len(h) < 100:
        return {"value": None, "n_obs": int(len(h)), "method": "insuffisant"}
    s = pd.Series(h["v"].values,
                  index=pd.to_datetime(h["t"], utc=True)).sort_index()
    r1h = s.resample("1h").last().dropna().pct_change().dropna()
    if len(r1h) < 100:
        return {"value": None, "n_obs": int(len(r1h)), "method": "insuffisant"}
    var = np.quantile(r1h, 0.01)
    tail = r1h[r1h <= var]
    es_1h = float(-tail.mean()) if len(tail) else float(-var)
    return {"value": round(es_1h * np.sqrt(24), 5), "n_obs": int(len(r1h)),
            "method": "empirique 1h × sqrt(24)"}


def _json(path: Path):
    return json.loads(path.read_text()) if path.exists() else {}


def build_row() -> dict:
    doc = _mongo_doc()
    led, fx = doc["ledger"], doc["eur_usdt_at_init"]
    hist = doc.get("history", [])
    equity = float(hist[-1]["v"]) if hist else doc["capital_eur"]
    gross_usdt = (sum(c["notional"] for c in doc.get("carry", []))
                  + sum(b["notional"] for b in doc.get("basis", [])
                        if not b.get("delivered")))
    longs_active = sum(l["notional"] for l in doc.get("longs", [])
                       if l.get("active"))
    pnl_gross_eur = (led["carry_accrued"] + led["basis_accrued"]
                     + led["longs_realized"]) / fx
    fees_eur = led["fees"] / fx
    borrow_eur = led["borrow_accrued"] / fx
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    net_pretax = pnl_gross_eur + fees_eur + borrow_eur
    prov = provision_for_month(max(net_pretax, 0.0) * fx, month)
    mh = _json(MH_DIR / "state.json")
    mhx = _json(MH_DIR / "exec_state.json")
    gate = _json(GATE).get("gate", {})
    last_iv = gate.get("intervals", [])[-1] if gate.get("intervals") else {}
    row = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "equity_eur": round(equity, 2),
        "pnl_gross_cum_eur": round(pnl_gross_eur, 2),
        "fees_cum_eur": round(fees_eur, 2),           # slippage inclus (tout taker)
        "borrow_cum_eur": round(borrow_eur, 6),
        "tax_provision_mtd_eur": round(prov["provision_usdt"] / fx, 2),
        "pnl_net_cum_eur": round(net_pretax - prov["provision_usdt"] / fx, 2),
        "es99_1d": _es99_1d(hist),
        "margin_used_proxy": round(gross_usdt * 0.10 / (equity * fx), 4),
        "net_delta_frac": round(longs_active / (equity * fx), 4),
        "mh_tracking": {
            "labels": {"n": mh.get("n_labeled"), "pf": mh.get("profit_factor")},
            "errors": mhx.get("errors"),
        },
        "reconciliation": {
            "status": gate.get("status"),
            "consecutive_ok": gate.get("consecutive_ok"),
            "last_gap_fees_usdt": last_iv.get("gap_fees_usdt"),
            "last_gap_carry_usdt": last_iv.get("gap_carry_usdt"),
        },
    }
    prev_files = sorted(OUT.glob("DAILY_*.json"))
    if prev_files:
        prev = json.loads(prev_files[-1].read_text())
        for k in ("pnl_gross_cum_eur", "fees_cum_eur", "pnl_net_cum_eur"):
            if k in prev:
                row[k.replace("_cum_", "_1d_")] = round(row[k] - prev[k], 2)
    return row


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    row = build_row()
    (OUT / f"DAILY_{row['date']}.json").write_text(
        json.dumps(row, indent=2, default=str))
    if not TABLE.exists():
        TABLE.write_text(
            "# ALPHA_20 — tableau quotidien officiel\n\n"
            "| date | equity € | PnL brut cum | fees cum | borrow | prov. fisc. MTD "
            "| PnL net cum | ES99 1j | marge | Δ net | réconciliation |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|\n")
    rec = row["reconciliation"]
    with open(TABLE, "a") as f:
        f.write(f"| {row['date']} | {row['equity_eur']:,.0f} "
                f"| {row['pnl_gross_cum_eur']:+,.1f} | {row['fees_cum_eur']:+,.1f} "
                f"| {row['borrow_cum_eur']:+,.2f} | {row['tax_provision_mtd_eur']:,.1f} "
                f"| {row['pnl_net_cum_eur']:+,.1f} | {row['es99_1d']['value']} "
                f"| {row['margin_used_proxy']:.1%} | {row['net_delta_frac']:.1%} "
                f"| {rec['status']}/{rec['consecutive_ok']} "
                f"gap {rec['last_gap_fees_usdt']} |\n")
    print(f"[alpha20 daily] {row['date']} equity {row['equity_eur']:,.0f} € "
          f"net cum {row['pnl_net_cum_eur']:+,.1f} € "
          f"réconciliation {rec['status']}/{rec['consecutive_ok']}", flush=True)


if __name__ == "__main__":
    main()
