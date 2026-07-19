#!/usr/bin/env python3
"""
scripts/run_paper_mh_events.py
─────────────────────────────────────────────────────────────────────────────
PAPER-LIVE stack MH (moteurs événementiels multi-horizon) — sleeve SÉPARÉ
(décision humaine 2026-07-19, AVANT le verdict shadow J+30).

Le shadow (run_event_shadow_daily.py) reste le déverrouilleur officiel : il
n'est PAS modifié, ses décisions restent gelées, son verdict J+30 garde toute
son autorité. Ce runner est une couche de LECTURE SEULE au-dessus du ledger
shadow qui donne un capital paper aux décisions du book officiel :

  • sélection : tier == "book" (consensus MH ≥ 0.70), horizon MH_consensus,
    event_time > --paper-start ;
  • sizing déterministe : w = 20 % du capital sleeve par décision, exposition
    max 100 % (5 positions) — au-delà, décisions écartées par score décroissant
    (skipped_capacity, tracées) ;
  • durée de position = horizon de trade du moteur (cascade 4 h, crowding 24 h,
    premium 4 h — SPECS du shadow) ;
  • PnL = notional × net_labeled (labels du shadow, coûts déjà déduits) ;
    décisions non labellisées = pending, aucune anticipation.

Le paper 200k (Portfolio V1.1) n'est pas touché : capital et ledgers propres.
Recompute déterministe à chaque cycle (style maison), aucune écriture dans le
ledger shadow.

Sorties :
  reports/paper_live/mh_events/ledger.parquet
  reports/paper_live/mh_events/state.json
Service : deploy/systemd/futur-paper-mh.{service,timer} (quotidien, après le
shadow).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SHADOW_LEDGER = ROOT / "reports" / "liq_cascade" / "shadow" / "decisions.parquet"
SHADOW_STATE = ROOT / "reports" / "liq_cascade" / "shadow" / "state.json"
OUT = ROOT / "reports" / "paper_live" / "mh_events"

WEIGHT = 0.20                    # fraction du capital par décision
MAX_OPEN = 5                     # exposition max 100 % du sleeve
TRADE_HOURS = {"LIQ_CASCADE": 4, "CROWDING_REVERSAL": 24,
               "PREMIUM_DISLOCATION": 4}   # horizons de trade des SPECS shadow


def select_book(led: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    led = led.copy()
    led["event_time"] = pd.to_datetime(led["event_time"], utc=True)
    tier = led.get("tier", pd.Series("book", index=led.index)).fillna("book")
    horizon = led.get("horizon", pd.Series("", index=led.index)).fillna("")
    m = ((tier == "book")
         & horizon.str.startswith("MH_consensus")
         & (led["event_time"] > start))
    return led[m].sort_values(["event_time", "score"],
                              ascending=[True, False]).reset_index(drop=True)


def allocate(book: pd.DataFrame, capital: float) -> pd.DataFrame:
    """Capacité déterministe : MAX_OPEN positions simultanées, score d'abord."""
    notional = capital * WEIGHT
    open_until: list = []
    taken, close_at = [], []
    for _, row in book.iterrows():
        t = row["event_time"]
        open_until = [u for u in open_until if u > t]
        hours = TRADE_HOURS.get(row["engine"], 4)
        end = t + pd.Timedelta(hours=hours)
        if len(open_until) < MAX_OPEN:
            open_until.append(end)
            taken.append(True)
        else:
            taken.append(False)
        close_at.append(end)
    book = book.copy()
    book["taken"] = taken
    book["close_at"] = close_at
    book["notional"] = np.where(book["taken"], notional, 0.0)
    lab = pd.to_numeric(book["net_labeled"], errors="coerce")
    book["pnl"] = np.where(book["taken"] & np.isfinite(lab),
                           book["notional"] * lab.fillna(0.0), np.nan)
    return book


def run_once(args) -> dict:
    start = pd.Timestamp(args.paper_start, tz="UTC")
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    shadow_days = None
    if SHADOW_STATE.exists():
        st = json.loads(SHADOW_STATE.read_text())
        shadow_days = (pd.Timestamp(now) - pd.Timestamp(st["shadow_started"])).days

    state = {
        "sleeve": "MH_EVENTS_PAPER_V0",
        "note": ("Sleeve paper SÉPARÉ (décision humaine 2026-07-19) au-dessus du "
                 "ledger shadow, qui reste le déverrouilleur officiel J+30 — "
                 "shadow et paper 200k intouchés."),
        "updated_at": now.isoformat(),
        "paper_start": args.paper_start, "capital": args.capital,
        "weight_per_decision": WEIGHT, "max_open": MAX_OPEN,
        "shadow_days": shadow_days,
        "shadow_verdict_due": bool(shadow_days is not None and shadow_days >= 30),
    }

    if not SHADOW_LEDGER.exists():
        state.update({"status": "no_shadow_ledger", "equity": args.capital,
                      "n_decisions": 0})
        (OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))
        print("[paper MH] pas de ledger shadow — sleeve à vide", flush=True)
        return state

    book = select_book(pd.read_parquet(SHADOW_LEDGER), start)
    book = allocate(book, args.capital)
    closed = book[np.isfinite(book["pnl"])]
    pnl_total = float(closed["pnl"].sum())
    net = closed["pnl"].values / max(args.capital * WEIGHT, 1e-9)
    pf = (float(net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9))
          if len(net) else None)

    book.to_parquet(OUT / "ledger.parquet", index=False)
    per_engine = {
        eng: {"n": int(len(g)), "labeled": int(np.isfinite(g["pnl"]).sum()),
              "pnl": round(float(g["pnl"].sum(skipna=True)), 2)}
        for eng, g in book[book["taken"]].groupby("engine")}
    state.update({
        "status": "active",
        "equity": round(args.capital + pnl_total, 2),
        "pnl_total": round(pnl_total, 2),
        "n_decisions": int(len(book)),
        "n_taken": int(book["taken"].sum()),
        "n_skipped_capacity": int((~book["taken"]).sum()),
        "n_labeled": int(len(closed)),
        "n_pending": int(book["taken"].sum() - len(closed)),
        "profit_factor": round(pf, 2) if pf is not None else None,
        "mean_net_bps": round(float(net.mean() * 1e4), 1) if len(net) else None,
        "per_engine": per_engine,
    })
    (OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))
    print(f"[paper MH] equity {state['equity']:.0f}  décisions {len(book)} "
          f"(prises {state['n_taken']}, labellisées {state['n_labeled']}, "
          f"pending {state['n_pending']})  shadow J{shadow_days}", flush=True)
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=10000)
    ap.add_argument("--paper-start", default="2026-07-19")
    args = ap.parse_args()
    run_once(args)


if __name__ == "__main__":
    main()
