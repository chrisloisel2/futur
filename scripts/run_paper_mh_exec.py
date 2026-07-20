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

DÉCOMPOSITION OBLIGATOIRE (ordre R0 2026-07-20) — trois erreurs SÉPARÉES,
sinon la close horaire hostile serait faussement attribuée à l'exécution ou
au modèle :
  • sampling_error_1h    = net_grid − net_label   (même coût forfaitaire que
    le shadow, seule la grille 1 h + le délai d'arrivée changent) ;
  • execution_shortfall  = net_exec − net_grid    (écart de coûts réels :
    registry + spread — s'enrichira de l'impact mesuré par la TCA) ;
  • model_tracking_error = niveau SLEEVE : PF réalisé des labels vs PF
    pré-enregistré de la stack (référence configs/alpha20.yaml) — l'écart
    modèle se juge sur les labels, jamais sur les frictions d'exécution.

Sorties : reports/paper_live/mh_events/exec_ledger.parquet + exec_state.json.
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
LABEL_COST_RT_BP = 14.0         # = COST_RT du shadow (run_event_shadow_daily)


def _closes(symbol: str) -> pd.Series:
    p = ENRICHED / f"{symbol}_1h_enriched.parquet"
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(p, columns=["datetime", "close"])
    return pd.Series(df["close"].values,
                     index=pd.to_datetime(df["datetime"], utc=True)).sort_index()


def replay_decision(row, closes: pd.Series, cost_bp: float):
    """(net_exec, entry_ts, exit_ts, gross) ou None si données manquantes."""
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
    return gross - cost_bp / 1e4, entry_ts, exit_ts, gross


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
        gross = rep[3] if rep else np.nan
        net_exec = rep[0] if rep else np.nan
        net_grid = gross - LABEL_COST_RT_BP / 1e4 if rep else np.nan
        net_label = float(r["net_labeled"]) if np.isfinite(
            pd.to_numeric(r["net_labeled"], errors="coerce")) else np.nan
        rows.append({
            "event_time": r["event_time"], "symbol": r["symbol"],
            "engine": r["engine"], "score": r["score"],
            "notional": r["notional"], "net_label": net_label,
            "net_grid": net_grid, "net_exec": net_exec,
            "sampling_error_1h": net_grid - net_label,
            "execution_shortfall": net_exec - net_grid,
            "entry_ts": rep[1] if rep else None,
            "exit_ts": rep[2] if rep else None,
        })
    led = pd.DataFrame(rows)
    if len(led):
        led["pnl_exec"] = led["notional"] * led["net_exec"]
        led.to_parquet(OUT / "exec_ledger.parquet", index=False)

    both = led.dropna(subset=["net_label", "net_exec"]) if len(led) else led
    # model_tracking_error : les LABELS réalisés vs la référence pré-enregistrée
    # de la stack — jamais mélangé aux frictions d'exécution
    from src.alpha20 import load_config
    ref_pf = float(load_config().get("references", {}).get(
        "mh_stack_pf_preregistered", 1.435))
    lab = led["net_label"].dropna() if len(led) else pd.Series(dtype=float)
    pf_real = (float(lab[lab > 0].sum() / max(abs(lab[lab < 0].sum()), 1e-9))
               if len(lab) >= 10 else None)     # < 10 labels : pas jugeable
    pnl = float(led["pnl_exec"].sum(skipna=True)) if len(led) else 0.0
    state.update({
        "status": "active",
        "cost_source": snap.source, "cost_bp_roundtrip": round(cost_bp, 2),
        "equity_exec": round(args.capital + pnl, 2),
        "n_decisions": int(len(led)),
        "n_replayed": int(led["net_exec"].notna().sum()) if len(led) else 0,
        "n_labeled_both": int(len(both)),
        "errors": {
            "sampling_error_1h_bps_mean": round(float(
                both["sampling_error_1h"].mean() * 1e4), 1) if len(both) else None,
            "execution_shortfall_bps_mean": round(float(
                led["execution_shortfall"].mean() * 1e4), 1)
                if len(led) and led["execution_shortfall"].notna().any() else None,
            "model_tracking": {"reference_pf": ref_pf, "realized_pf_labels": pf_real,
                               "n_labels": int(len(lab)),
                               "gap": round(pf_real - ref_pf, 3)
                               if pf_real is not None else None},
        },
        "mean_exec_bps": round(float(led["net_exec"].mean() * 1e4), 1)
                          if len(led) and led["net_exec"].notna().any() else None,
    })
    (OUT / "exec_state.json").write_text(json.dumps(state, indent=2, default=str))
    e = state["errors"]
    print(f"[paper MH exec] equity {state['equity_exec']:.0f}  "
          f"décisions {state['n_decisions']} (rejouées {state['n_replayed']})  "
          f"sampling {e['sampling_error_1h_bps_mean']} bps | "
          f"shortfall {e['execution_shortfall_bps_mean']} bps | "
          f"PF labels {e['model_tracking']['realized_pf_labels']}"
          f"/{e['model_tracking']['reference_pf']}", flush=True)
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=10000)
    ap.add_argument("--paper-start", default="2026-07-19")
    args = ap.parse_args()
    run_once(args)


if __name__ == "__main__":
    main()
