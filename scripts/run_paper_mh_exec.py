#!/usr/bin/env python3
"""
scripts/run_paper_mh_exec.py
─────────────────────────────────────────────────────────────────────────────
PAPER D'EXÉCUTION RÉELLE du sleeve MH (ordre ALPHA_20 n°6, 2026-07-19).

Le paper MH actuel (run_paper_mh_events.py) valorise les décisions avec les
LABELS du shadow (fwd du dataset − coût forfaitaire). Ici, replay parallèle
avec les conditions d'exécution :

  • prix d'ARRIVÉE : première close STRICTEMENT POSTÉRIEURE à event_time
    (grain 1 h enriched — délai d'arrivée subi jusqu'à 60 min, HOSTILE et
    assumé ; le replay tick/L2 arrive avec le pilote Tardis, étape 8) ;
  • sortie : première close ≥ entrée + horizon de trade du moteur
    (cascade 4 h, crowding 24 h, premium 4 h) ;
  • direction : LONG (fwd_* du dataset = log-ret brut du sous-jacent, les
    moteurs jouent le rebond) ;
  • coûts par TRADE depuis le fee_registry alpha20 (source obligatoire —
    assumed tant que commissionRate signé indisponible) : taker ×2 +
    slippage ×2 + demi-spread ×2.

Sorties : reports/paper_live/mh_events/exec_ledger.parquet + exec_state.json,
avec TRACKING ERROR vs les labels shadow (même décision, deux valorisations).
Le sizing 20 %/décision reste une expérience paper — jamais un sizing live.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from src.alpha20.costs.fee_registry import effective_costs  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "rmh", ROOT / "scripts" / "run_paper_mh_events.py")
rmh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rmh)

ENRICHED = ROOT / "data" / "enriched"
OUT = ROOT / "reports" / "paper_live" / "mh_events"
HALF_SPREAD_BP = 1.0            # majors : à remplacer par le spread L2 mesuré


def _closes(symbol: str) -> pd.Series:
    p = ENRICHED / f"{symbol}_1h_enriched.parquet"
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(p, columns=["datetime", "close"])
    return pd.Series(df["close"].values,
                     index=pd.to_datetime(df["datetime"], utc=True)).sort_index()


def replay_decision(row, closes: pd.Series, cost_bp: float):
    """(net_exec, entry_ts, exit_ts) ou None si données manquantes."""
    t = row["event_time"]
    hours = rmh.TRADE_HOURS.get(row["engine"], 4)
    after = closes[closes.index > t]
    if after.empty:
        return None
    entry_ts, entry_px = after.index[0], float(after.iloc[0])
    exit_after = closes[closes.index >= entry_ts + pd.Timedelta(hours=hours)]
    if exit_after.empty:
        return None
    exit_ts, exit_px = exit_after.index[0], float(exit_after.iloc[0])
    gross = exit_px / entry_px - 1.0
    return gross - cost_bp / 1e4, entry_ts, exit_ts


def run_once(args) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    state = {"sleeve": "MH_EVENTS_PAPER_EXEC_V0",
             "note": ("Replay d'exécution (closes 1h hostiles) en PARALLÈLE du "
                      "paper à labels — le shadow reste le déverrouilleur J+30."),
             "updated_at": now.isoformat(), "paper_start": args.paper_start,
             "capital": args.capital}
    if not rmh.SHADOW_LEDGER.exists():
        state.update({"status": "no_shadow_ledger", "n_decisions": 0,
                      "equity": args.capital})
        (OUT / "exec_state.json").write_text(json.dumps(state, indent=2))
        print("[paper MH exec] pas de ledger shadow", flush=True)
        return state

    book = rmh.select_book(pd.read_parquet(rmh.SHADOW_LEDGER),
                           pd.Timestamp(args.paper_start, tz="UTC"))
    book = rmh.allocate(book, args.capital)
    snap = effective_costs("binance_usdm", "PERP")
    cost_bp = 2 * (snap.taker_bp + (snap.slippage_bp or 0.0)) + 2 * HALF_SPREAD_BP

    rows = []
    for _, r in book[book["taken"]].iterrows():
        closes = _closes(r["symbol"])
        rep = replay_decision(r, closes, cost_bp)
        rows.append({
            "event_time": r["event_time"], "symbol": r["symbol"],
            "engine": r["engine"], "score": r["score"],
            "notional": r["notional"],
            "net_label": float(r["net_labeled"]) if np.isfinite(
                pd.to_numeric(r["net_labeled"], errors="coerce")) else np.nan,
            "net_exec": rep[0] if rep else np.nan,
            "entry_ts": rep[1] if rep else None,
            "exit_ts": rep[2] if rep else None,
        })
    led = pd.DataFrame(rows)
    if len(led):
        led["pnl_exec"] = led["notional"] * led["net_exec"]
        led.to_parquet(OUT / "exec_ledger.parquet", index=False)

    both = led.dropna(subset=["net_label", "net_exec"]) if len(led) else led
    te = None
    if len(both) >= 2:
        diff = both["net_exec"] - both["net_label"]
        denom = float(both["net_label"].abs().mean())
        te = float(diff.abs().mean() / denom) if denom > 0 else None
    pnl = float(led["pnl_exec"].sum(skipna=True)) if len(led) else 0.0
    state.update({
        "status": "active",
        "cost_source": snap.source, "cost_bp_roundtrip": round(cost_bp, 2),
        "equity_exec": round(args.capital + pnl, 2),
        "n_decisions": int(len(led)),
        "n_replayed": int(led["net_exec"].notna().sum()) if len(led) else 0,
        "n_labeled_both": int(len(both)),
        "tracking_error_vs_labels": round(te, 3) if te is not None else None,
        "mean_exec_bps": round(float(led["net_exec"].mean() * 1e4), 1)
                          if len(led) and led["net_exec"].notna().any() else None,
    })
    (OUT / "exec_state.json").write_text(json.dumps(state, indent=2, default=str))
    print(f"[paper MH exec] equity {state['equity_exec']:.0f}  "
          f"décisions {state['n_decisions']} (rejouées {state['n_replayed']})  "
          f"TE vs labels: {state['tracking_error_vs_labels']}", flush=True)
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=10000)
    ap.add_argument("--paper-start", default="2026-07-19")
    args = ap.parse_args()
    run_once(args)


if __name__ == "__main__":
    main()
