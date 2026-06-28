#!/usr/bin/env python3
"""
scripts/run_portfolio_walk_forward.py
─────────────────────────────────────────────────────────────────────────────
Backtest PORTEFEUILLE + comparaison "1 position / cooldown global" (legacy) vs
"multi-position / cooldown local" (portfolio). Chiffre ce que coûtent la
contrainte une-seule-position et le cooldown global.

Usage :
    python3 scripts/run_portfolio_walk_forward.py \
        --engines TRM_TREND_INST --start 2022-01-01 --end 2025-12-31
    python3 scripts/run_portfolio_walk_forward.py \
        --engines TRM_TREND_LONG,PULLBACK_LONG,LIQUIDATION_REBOUND --start 2026-01-01 --end 2026-06-20
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.registry import build_engine
from src.institutional.engines.exit_engine import ExitEngineV1
from src.institutional.risk.governor import RiskGovernor
from src.institutional.portfolio.meta_allocator import UtilityMetaAllocator
from src.institutional.backtest.portfolio_backtester import (
    PortfolioBacktester, PortfolioBacktestConfig,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
OUT = Path("artifacts/institutional/backtests/portfolio")


def _build(engine_ids):
    engines = []
    for eid in engine_ids:
        try:
            engines.append(build_engine(eid))
        except Exception as e:
            print(f"  ! moteur {eid} indisponible: {e}")
    return engines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="TRM_TREND_INST")
    ap.add_argument("--assets", default=None)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--full-stack", action="store_true",
                    help="ajoute allocateur utility + governor + exit engine")
    args = ap.parse_args()

    engine_ids = [e.strip() for e in args.engines.split(",") if e.strip()]
    assets = [a.strip() for a in args.assets.split(",")] if args.assets else None
    engines = _build(engine_ids)
    if not engines:
        print("Aucun moteur disponible."); return

    configs = {
        "legacy_single_global": PortfolioBacktestConfig(
            max_open_positions=1, cooldown_mode="global", global_cooldown_hours=24),
        "portfolio_multi_local": PortfolioBacktestConfig(
            max_open_positions=4, cooldown_mode="local"),
    }

    if args.full_stack:
        all_assets = sorted({a for e in engines for a in e.assets})
        ex = ExitEngineV1(assets=all_assets); ex.preload(args.start, args.end)
        configs["full_stack"] = PortfolioBacktestConfig(
            max_open_positions=4, cooldown_mode="local",
            allocator_hook=UtilityMetaAllocator().as_hook(),
            governor_hook=RiskGovernor().as_hook(),
            exit_hook=ex.as_hook() if ex.available else None,
        )

    report = {"engines": engine_ids, "start": args.start, "end": args.end, "runs": {}}
    for name, cfg in configs.items():
        bt = PortfolioBacktester(engines, cfg)
        res = bt.run(assets, args.start, args.end)
        print(f"\n=== {name} ===")
        print(res.summary())
        report["runs"][name] = {
            "final_equity": float(res.equity.iloc[-1]),
            "metrics": res.metrics,
            "per_engine_pnl": res.per_engine_pnl,
            "gate": res.gate,
        }

    # coût des contraintes legacy
    a = report["runs"]["legacy_single_global"]["metrics"]
    b = report["runs"]["portfolio_multi_local"]["metrics"]
    delta = {
        "trades_delta": b.get("n_trades", 0) - a.get("n_trades", 0),
        "roi_month_median_delta": b.get("roi_month_median", 0) - a.get("roi_month_median", 0),
        "pf_delta": b.get("pf", 0) - a.get("pf", 0),
    }
    report["constraint_cost"] = delta
    print(f"\n=== COÛT CONTRAINTES (multi - single) ===\n{json.dumps(delta, indent=2)}")

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "wf_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n→ rapport écrit: {out_path}")


if __name__ == "__main__":
    main()
