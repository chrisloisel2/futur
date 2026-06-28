#!/usr/bin/env python3
"""
scripts/tune_portfolio_v1.py
─────────────────────────────────────────────────────────────────────────────
Bloc A — épaissir le candidat Portfolio V1 (Phase 2-3) :
  - réparation long book : filtre 3×fees + univers BTC/ETH/SOL
  - carry sizing ladder : 20% / 35% / 50%
Mesure ROI annualisé / PF / DD pour voir si on lève PF→≥1.10 et ROI→palier 2,
SANS casser DD≤3%.

Usage : python3 scripts/tune_portfolio_v1.py --start 2022-01-01 --end 2026-06-20
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.base import AlphaEngine
from src.institutional.engines.registry import build_engine
from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig

logging.basicConfig(level=logging.ERROR)
LONG_ENGINES = ["TRM_TREND_INST", "PULLBACK_LONG", "LIQUIDATION_REBOUND"]
CARRY_ASSETS = ["BTCUSDT", "ETHUSDT"]
YEARS = 4.47  # 2022-01 → 2026-06


class Cached(AlphaEngine):
    def __init__(self, inner): self.inner = inner; self.config = inner.config; self._c = {}
    def generate(self, a, s, e):
        k = (a, s, e)
        if k not in self._c: self._c[k] = self.inner.generate(a, s, e)
        return self._c[k]
    def thresholds_for(self, a): return self.inner.thresholds_for(a)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-06-20")
    ap.add_argument("--out", default="reports/tune_portfolio_v1.json")
    args = ap.parse_args()
    longs = [Cached(build_engine(e)) for e in LONG_ENGINES]

    def base(**kw):
        d = dict(enable_long=True, enable_asset_regime_gate=True, enable_regime_flip_exit=True,
                 enable_intra_position_governor=True, enable_carry=True, enable_hedge=True)
        d.update(kw)
        return MultiLegConfig(**d)

    runs = {
        "V1_base":                 base(carry_fraction=0.20),
        "V1_repaired_3xfees_univ": base(carry_fraction=0.20, long_min_er_cost_mult=3.0,
                                        long_universe=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
        "V1_rep_carry35":          base(carry_fraction=0.35, long_min_er_cost_mult=3.0,
                                        long_universe=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
        "V1_rep_carry50":          base(carry_fraction=0.50, long_min_er_cost_mult=3.0,
                                        long_universe=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
    }

    report = {"window": [args.start, args.end], "runs": {}}
    print(f"\n{'Config':<26}{'ROI':>8}{'ann.':>8}{'PF':>7}{'maxDD':>8}  PnL[dir/carry/hedge/fees]")
    print("─" * 86)
    for name, cfg in runs.items():
        res = MultiLegBacktester(longs, cfg, carry_assets=CARRY_ASSETS).run(args.start, args.end)
        m = res.metrics; p = res.pnl_by_type
        ann = (1 + m.get("total_return", 0)) ** (1 / YEARS) - 1
        carry_share = abs(p["carry_funding"]) / max(sum(abs(v) for v in [p["directional"], p["carry_funding"], p["hedge"]]), 1e-9)
        print(f"{name:<26}{m.get('total_return',0)*100:>7.1f}%{ann*100:>7.1f}%{m.get('pf',0):>7.2f}"
              f"{m.get('max_drawdown',0)*100:>7.1f}%  [{p['directional']:.0f}/{p['carry_funding']:.0f}/{p['hedge']:.0f}/{p['fees']:.0f}]")
        report["runs"][name] = {"metrics": m, "pnl_by_type": p, "annualized": ann,
                                "carry_share": carry_share}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
