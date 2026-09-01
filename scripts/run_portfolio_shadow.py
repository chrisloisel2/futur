#!/usr/bin/env python3
"""
scripts/run_portfolio_shadow.py
─────────────────────────────────────────────────────────────────────────────
PORTFOLIO_SHADOW_LAYER runner — agrège les intents FORWARD_LIVE (jamais
REPLAY) de tous les alphas position-generating, applique le gate
WHALE_LSR_SCREEN_V1, calcule 5 portefeuilles (P1_EQUAL_RISK, P1_CONTROL,
P1_VOL_OVERLAY, P2_DIVERSIFIED, P3_ALL_CANDIDATES) avec un vrai
mark-to-market (positions, PnL réalisé/non-réalisé, funding), et persiste
l'état + l'intent_ledger (item 6, traçabilité collision d'alphas) de chacun.

AUCUN ordre réel.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.gate import active_screen_symbols
from src.institutional.live_alpha_lab.intents import build_intents
from src.institutional.live_alpha_lab.overlay import vol_overlay_multiplier
from src.institutional.live_alpha_lab.portfolio import aggregate, step
from src.institutional.live_alpha_lab.portfolio_config import ALL_PORTFOLIOS
from src.institutional.live_alpha_lab.provenance import spec_provenance

REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
LAB_DIR = ROOT / "reports" / "live_alpha_lab"


def load_forward_only(alpha_id: str) -> pd.DataFrame:
    p = LAB_DIR / alpha_id / "decisions.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if "provenance" not in df.columns:
        return pd.DataFrame()   # jamais traiter un ledger non-tagué comme forward -- fail closed
    return df[df["provenance"] == "FORWARD_LIVE"].copy()


def main() -> int:
    reg = yaml.safe_load(REGISTRY.read_text())
    by_id = {a["alpha_id"]: a for a in reg["alphas"]}
    as_of = pd.Timestamp(datetime.now(timezone.utc))

    all_intents = []
    for alpha_id, entry in by_id.items():
        if entry.get("operational_status") not in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
            continue
        fwd = load_forward_only(alpha_id)
        try:
            intents = build_intents(alpha_id, entry, fwd)
        except KeyError as e:
            print(f"[portfolio] {e}", flush=True)
            continue
        all_intents.extend(intents)
        print(f"[portfolio] {alpha_id}: {len(fwd)} forward decisions -> {len(intents)} intents", flush=True)

    screen_df = load_forward_only("WHALE_LSR_SCREEN_V1")
    screened = active_screen_symbols(screen_df, as_of) if not screen_df.empty else set()
    if screened:
        print(f"[portfolio] symboles sous screen actif : {sorted(screened)}", flush=True)

    overlay_mult = vol_overlay_multiplier(as_of)
    print(f"[portfolio] vol_overlay_multiplier (as_of={as_of.isoformat()}) = {overlay_mult:.4f}", flush=True)

    summary = {}
    for name, config in ALL_PORTFOLIOS.items():
        agg = aggregate(all_intents, config, screened, vol_overlay_multiplier=overlay_mult, as_of=as_of)
        state = step(name, config, agg, as_of)
        last = state.equity_curve[-1] if state.equity_curve else {}
        print(f"[portfolio] {name}: {last.get('n_positions', 0)} positions "
              f"status={last.get('status')} "
              f"gross={last.get('gross_exposure', 0):,.2f} net={last.get('net_exposure', 0):,.2f} "
              f"realized={last.get('realized_pnl', 0):,.2f} unrealized={last.get('unrealized_pnl', 0):,.2f} "
              f"fees={last.get('fees', 0):,.2f} funding={last.get('funding', 0):,.2f} "
              f"equity={last.get('equity', config.capital_eur):,.2f} "
              f"dd={last.get('drawdown', 0):.4%}", flush=True)
        if last.get("skipped_no_mark"):
            print(f"[portfolio] {name}: AUCUN mark dispo pour {last['skipped_no_mark']} -- non tradé ce step", flush=True)
        summary[name] = {
            "status": last.get("status"), "n_positions": last.get("n_positions", 0),
            "gross_exposure": last.get("gross_exposure", 0), "net_exposure": last.get("net_exposure", 0),
            "realized_pnl": last.get("realized_pnl", 0), "unrealized_pnl": last.get("unrealized_pnl", 0),
            "total_pnl": last.get("realized_pnl", 0) + last.get("unrealized_pnl", 0),
            "cumulative_fees_usd": state.cumulative_fees_usd,
            "cumulative_funding_usd": state.cumulative_funding_usd,
            "cumulative_turnover_usd": state.cumulative_turnover_usd,
            "cumulative_cost_by_alpha": state.cumulative_cost_by_alpha,
            "pnl_by_alpha": last.get("pnl_by_alpha", {}),
            "equity": last.get("equity", config.capital_eur),
            "drawdown": last.get("drawdown", 0),
            "n_equity_points": len(state.equity_curve),
        }

    (LAB_DIR / "portfolios" / "SUMMARY.json").write_text(json.dumps({
        "generated_at": as_of.isoformat(), "n_forward_intents_total": len(all_intents),
        "screened_symbols": sorted(screened), "vol_overlay_multiplier": overlay_mult,
        "portfolios": summary,
    }, indent=2, default=str))
    print(f"[portfolio] résumé -> {LAB_DIR / 'portfolios' / 'SUMMARY.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
