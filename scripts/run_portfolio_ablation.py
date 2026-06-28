#!/usr/bin/env python3
"""
scripts/run_portfolio_ablation.py
─────────────────────────────────────────────────────────────────────────────
Matrice d'ablation (Phase 18) — isole la contribution de CHAQUE brique.

Répond à : le portefeuille perd-il à cause de l'alpha, des exits, de
l'allocator, des coûts ou du governor ?

Runs A-J (cf. brief). Réutilise PortfolioBacktester ; les opportunités de chaque
moteur sont mises en cache (générées une seule fois) pour la vitesse.

Usage :
    python3 scripts/run_portfolio_ablation.py --start 2026-01-01 --end 2026-06-20
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.base import AlphaEngine
from src.institutional.engines.registry import build_engine
from src.institutional.engines.exit_engine import ExitEngineV1
from src.institutional.risk.governor import RiskGovernor
from src.institutional.portfolio.meta_allocator import UtilityMetaAllocator
from src.institutional.backtest.portfolio_backtester import (
    PortfolioBacktester, PortfolioBacktestConfig,
)

logging.basicConfig(level=logging.ERROR)


class CachedEngine(AlphaEngine):
    """Wrappe un moteur et met en cache generate() (même fenêtre → 1 seule génération)."""
    def __init__(self, inner: AlphaEngine):
        self.inner = inner
        self.config = inner.config
        self._cache = {}

    def generate(self, asset, start, end):
        key = (asset, start, end)
        if key not in self._cache:
            self._cache[key] = self.inner.generate(asset, start, end)
        return self._cache[key]

    def thresholds_for(self, asset):
        return self.inner.thresholds_for(asset)


def make_config(*, allocator=False, exit_hook=None, governor=False, legacy=False) -> PortfolioBacktestConfig:
    cfg = PortfolioBacktestConfig(
        max_open_positions=1 if legacy else 4,
        cooldown_mode="global" if legacy else "local",
        global_cooldown_hours=24,
    )
    if allocator:
        cfg.allocator_hook = UtilityMetaAllocator().as_hook()
    if exit_hook is not None:
        cfg.exit_hook = exit_hook
    if governor:
        cfg.governor_hook = RiskGovernor().as_hook()
    return cfg


def run(engines: List[AlphaEngine], assets, start, end, cfg) -> dict:
    res = PortfolioBacktester(engines, cfg).run(assets, start, end)
    m = res.metrics
    return {
        "final_equity": round(float(res.equity.iloc[-1]), 1),
        "roi": round(float(res.equity.iloc[-1] / res.equity.iloc[0] - 1), 4),
        "n_trades": m.get("n_trades", 0),
        "pf": round(m.get("pf", 0), 3),
        "sharpe": round(m.get("sharpe", 0), 2),
        "max_dd": round(m.get("max_drawdown", 0), 4),
        "cvar95": round(m.get("cvar_95", 0), 5),
        "roi_month_med": round(m.get("roi_month_median", 0), 4),
        "trades_month": round(m.get("trades_per_month", 0), 1),
        "per_engine_pnl": res.per_engine_pnl,
        "gate": res.gate.get("verdict"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-06-20")
    ap.add_argument("--out", default="reports/ablation_2026_oos.json")
    args = ap.parse_args()

    trm = CachedEngine(build_engine("TRM_TREND_LONG"))
    pull = CachedEngine(build_engine("PULLBACK_LONG"))
    allp = [trm, pull,
            CachedEngine(build_engine("LIQUIDATION_REBOUND")),
            CachedEngine(build_engine("CARRY_BASIS")),
            CachedEngine(build_engine("CROSS_SECTIONAL_LONG"))]

    all_assets = sorted({a for e in allp for a in e.assets})
    ex = ExitEngineV1(assets=all_assets)
    ex.preload(args.start, args.end)
    exit_hook = ex.as_hook() if ex.available else None

    matrix = {
        "A_trm_legacy":       (lambda: run([trm], None, args.start, args.end, make_config(legacy=True))),
        "B_trm_alloc":        (lambda: run([trm], None, args.start, args.end, make_config(allocator=True))),
        "C_trm_alloc_exit":   (lambda: run([trm], None, args.start, args.end, make_config(allocator=True, exit_hook=exit_hook))),
        "D_trm_full":         (lambda: run([trm], None, args.start, args.end, make_config(allocator=True, exit_hook=exit_hook, governor=True))),
        "E_pullback_raw":     (lambda: run([pull], None, args.start, args.end, make_config())),
        "F_pullback_full":    (lambda: run([pull], None, args.start, args.end, make_config(allocator=True, exit_hook=exit_hook, governor=True))),
        "G_all_raw":          (lambda: run(allp, None, args.start, args.end, make_config())),
        "H_all_alloc":        (lambda: run(allp, None, args.start, args.end, make_config(allocator=True))),
        "I_all_alloc_exit":   (lambda: run(allp, None, args.start, args.end, make_config(allocator=True, exit_hook=exit_hook))),
        "J_all_full":         (lambda: run(allp, None, args.start, args.end, make_config(allocator=True, exit_hook=exit_hook, governor=True))),
    }

    results = {}
    print(f"\n{'Run':<20}{'ROI':>8}{'PF':>7}{'trades':>8}{'t/mo':>7}{'maxDD':>8}{'gate':>6}")
    print("─" * 70)
    for name, fn in matrix.items():
        r = fn()
        results[name] = r
        print(f"{name:<20}{r['roi']*100:>7.1f}%{r['pf']:>7.2f}{r['n_trades']:>8}"
              f"{r['trades_month']:>7.1f}{r['max_dd']*100:>7.1f}%{str(r['gate']):>6}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"window": [args.start, args.end], "runs": results}, indent=2))
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
