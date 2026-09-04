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

from src.institutional.live_alpha_lab.eligibility import (
    NO_CAPITAL_SCIENTIFIC_STATUSES, EligibilityReason, is_forward_eligible,
    load_validation_index)
from src.institutional.live_alpha_lab.gate import active_screen_symbols
from src.institutional.live_alpha_lab.intents import NOT_A_POSITION_ALPHA, build_intents
from src.institutional.live_alpha_lab.overlay import vol_overlay_multiplier
from src.institutional.live_alpha_lab.portfolio import aggregate, load_state, step
from src.institutional.live_alpha_lab.portfolio_config import ALL_PORTFOLIOS
from src.institutional.live_alpha_lab.provenance import spec_provenance

REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
LAB_DIR = ROOT / "reports" / "live_alpha_lab"

# ⚠ item P0.1 (audit forward 2026-09-04) : la porte de capital vivait ICI, sous
# forme d'un unique test sur `scientific_status` lu dans le registre des ALPHAS.
# Elle ne consultait jamais le VALIDATION_REGISTRY -- d'où
# SHORT_COVERING_CONTINUATION_V1 (validated_for_forward: false) portant 100 % du
# capital des 5 portefeuilles. La décision est désormais prise par une fonction
# centrale et testable, `eligibility.is_forward_eligible()`, qui confronte les
# DEUX registres et échoue fermé. `NO_CAPITAL_SCIENTIFIC_STATUSES` y a déménagé
# et reste ré-exporté ici (import en tête) pour les appelants existants.


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

    validation_index = load_validation_index()
    all_intents = []
    eligibility_log = []
    gate_stats = []
    for alpha_id, entry in by_id.items():
        verdict = is_forward_eligible(
            entry, validation_index,
            position_alpha=alpha_id not in NOT_A_POSITION_ALPHA)
        eligibility_log.append(verdict.as_dict())
        if not verdict.eligible:
            # La collecte forward n'est JAMAIS coupée -- seul le capital l'est.
            print(f"[portfolio] {alpha_id}: PAS DE CAPITAL FORWARD "
                  f"[{verdict.reason.value}] {verdict.detail}", flush=True)
            continue
        if verdict.reason is EligibilityReason.NOT_A_POSITION_ALPHA:
            continue   # gate/overlay : traité plus bas, ne produit pas d'intent
        fwd = load_forward_only(alpha_id)
        stats: dict = {}
        try:
            intents = build_intents(alpha_id, entry, fwd, stats=stats)
        except KeyError as e:
            print(f"[portfolio] {e}", flush=True)
            continue
        all_intents.extend(intents)
        if stats:
            gate_stats.append(stats)
        blocked = stats.get("n_blocked_negative_ev", 0)
        suffix = (f" | {blocked} décision(s) refusée(s) REJECT_NEGATIVE_EXPECTED_VALUE"
                  if blocked else "")
        print(f"[portfolio] {alpha_id}: {len(fwd)} forward decisions -> "
              f"{len(intents)} intents{suffix}", flush=True)

    screen_df = load_forward_only("WHALE_LSR_SCREEN_V1")
    screened = active_screen_symbols(screen_df, as_of) if not screen_df.empty else set()
    if screened:
        print(f"[portfolio] symboles sous screen actif : {sorted(screened)}", flush=True)

    overlay_mult = vol_overlay_multiplier(as_of)
    print(f"[portfolio] vol_overlay_multiplier (as_of={as_of.isoformat()}) = {overlay_mult:.4f}", flush=True)

    summary = {}
    for name, config in ALL_PORTFOLIOS.items():
        # item P0.3 : le dénominateur de budget à cliquet est un ÉTAT du
        # portefeuille (il ne redescend pas quand un intent sort). Il est donc
        # relu depuis l'état persisté avant l'agrégation, puis réécrit par
        # step(). Sans cette relecture, chaque cycle repartirait d'un cliquet
        # vide et la redistribution mécanique reviendrait.
        prior = load_state(name, config.capital_eur)
        agg = aggregate(all_intents, config, screened, vol_overlay_multiplier=overlay_mult,
                        as_of=as_of,
                        denominator_high_water=dict(prior.alpha_denominator_high_water))
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
        # item P0.1/P0.2 : les deux portes sont AUDITABLES depuis le résumé --
        # qui a reçu du capital, qui n'en a pas reçu et pourquoi, combien de
        # décisions ont été refusées pour espérance nette négative.
        "forward_capital_eligibility": eligibility_log,
        "negative_expected_value_gate": gate_stats,
        "portfolios": summary,
    }, indent=2, default=str))
    print(f"[portfolio] résumé -> {LAB_DIR / 'portfolios' / 'SUMMARY.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
