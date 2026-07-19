#!/usr/bin/env python3
"""
scripts/backtest_carry_basis.py
─────────────────────────────────────────────────────────────────────────────
CARRY V2 — backtest delta-neutral (Phase 36). PORTAGE, pas directionnel.

Position : long spot + short perp (delta-neutral). On encaisse le funding quand
funding > 0 (les longs paient les shorts). Aucun short nu, aucun pari de prix.

    PnL_période = funding_received − borrow_cost            (en position)
    PnL_entrée/sortie = − roundtrip_cost (2 jambes)
    (basis convergence omis : pas de feed basis dans enriched — documenté)

Garde-fous : SHORT_DIRECTIONAL_ENABLED=False, NAKED_SHORT_ALLOWED=False.
Gate : return_month > 0.3% · DD < 1.5% · net positif après coûts · funding-flip stress.

Usage :
    python3 scripts/backtest_carry_basis.py --assets BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT \
        --start 2022-01-01 --end 2026-06-20 --stress
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.legacy_bridge import load_enriched

SHORT_DIRECTIONAL_ENABLED = False
NAKED_SHORT_ALLOWED = False
FUNDING_HOURS = (0, 8, 16)  # cadence funding Binance


def carry_backtest(
    funding: pd.Series,
    *,
    mode: str = "always_on",         # "always_on" (hold) | "threshold" (churn)
    entry_funding: float = 5e-5,
    exit_funding: float = -5e-5,      # hystérésis large pour limiter le churn
    roundtrip_cost: float = 0.0006,  # 2 jambes maker (~3bps×2) aller-retour
    borrow_per_period: float = 0.9e-5,  # emprunt spot ~1%/an / (1095 périodes)
    funding_shift: float = 0.0,      # stress : décale tout le funding (flip)
) -> dict:
    """Simule la récolte de funding delta-neutral. Retourne métriques."""
    f = funding.dropna().copy() + funding_shift
    f = f[f.index.hour.isin(FUNDING_HOURS)]
    if len(f) < 50:
        return {"n_periods": len(f)}

    in_pos = (mode == "always_on")
    rets = []
    n_trades = 1 if in_pos else 0
    for i, (ts, fr) in enumerate(f.items()):
        period_pnl = 0.0
        if mode == "always_on":
            if i == 0:
                period_pnl -= roundtrip_cost / 2.0           # entrée unique
            period_pnl += fr - borrow_per_period             # funding − borrow
            if i == len(f) - 1:
                period_pnl -= roundtrip_cost / 2.0           # sortie unique
        else:  # threshold (avec hystérésis)
            if not in_pos and fr > entry_funding:
                in_pos = True; n_trades += 1
                period_pnl -= roundtrip_cost / 2.0
            if in_pos:
                period_pnl += fr - borrow_per_period
                if fr <= exit_funding:
                    in_pos = False
                    period_pnl -= roundtrip_cost / 2.0
        rets.append(period_pnl)

    rets = pd.Series(rets, index=f.index)
    eq = (1 + rets).cumprod()
    peak = eq.cummax(); dd = float(((eq - peak) / peak).min())
    monthly = eq.resample("M").last().pct_change().dropna()
    pos = rets[rets > 0].sum(); neg = -rets[rets < 0].sum()
    pf = float(pos / neg) if neg > 1e-12 else (float("inf") if pos > 0 else 0.0)
    return {
        "n_periods": int(len(f)), "n_trades": n_trades,
        "total_return": float(eq.iloc[-1] - 1),
        "return_month_median": float(monthly.median()) if len(monthly) else 0.0,
        "return_month_mean": float(monthly.mean()) if len(monthly) else 0.0,
        "max_dd": dd, "pf": pf,
        "pct_in_market": float((rets != 0).mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-06-20")
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--out", default="reports/carry_v2_backtest.json")
    args = ap.parse_args()
    assert not (SHORT_DIRECTIONAL_ENABLED or NAKED_SHORT_ALLOWED)

    assets = [a.strip() for a in args.assets.split(",")]
    report = {"window": [args.start, args.end], "assets": {}}
    print(f"\n{'Asset':<10}{'ret_tot':>9}{'ret/mo med':>12}{'maxDD':>8}{'PF':>7}{'%mkt':>7}{'gate':>6}")
    print("─" * 62)
    for a in assets:
        df = load_enriched(a, required_cols=["funding_rate"], start=args.start, end=args.end)
        if df is None or "funding_rate" not in df.columns:
            print(f"{a:<10}  pas de funding_rate"); continue
        fr = df.set_index("datetime")["funding_rate"]
        base = carry_backtest(fr)
        gate = (base.get("return_month_median", 0) > 0.003 and abs(base.get("max_dd", 1)) < 0.015
                and base.get("total_return", -1) > 0)
        base["gate_pass"] = bool(gate)
        if args.stress:
            # flip stress : funding −1 std permanent
            base["stress_funding_flip"] = carry_backtest(fr, funding_shift=-float(fr.std()))
        report["assets"][a] = base
        print(f"{a:<10}{base.get('total_return',0)*100:>8.1f}%{base.get('return_month_median',0)*100:>11.2f}%"
              f"{base.get('max_dd',0)*100:>7.1f}%{base.get('pf',0):>7.2f}{base.get('pct_in_market',0)*100:>6.0f}%"
              f"{'PASS' if gate else 'FAIL':>6}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
