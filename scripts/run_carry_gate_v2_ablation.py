#!/usr/bin/env python3
"""
scripts/run_carry_gate_v2_ablation.py
─────────────────────────────────────────────────────────────────────────────
Impact portefeuille de CARRY_GATE_V2 (Phase 8) : V1.1 (gate funding mono-exchange)
vs V1.2 (gate cross-exchange Binance×Bybit), carry 50%/75%. Fenêtre = overlap
Bybit (2022-11 → dernière barre). Mesure ROI annualisé / PF / DD / part carry.

    python3 scripts/run_carry_gate_v2_ablation.py
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
from src.institutional.engines.legacy_bridge import load_enriched
from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig

logging.basicConfig(level=logging.ERROR)
LONG_ENGINES = ["TRM_TREND_INST", "PULLBACK_LONG", "LIQUIDATION_REBOUND"]
CARRY_ASSETS = ["BTCUSDT", "ETHUSDT"]


class Cached(AlphaEngine):
    def __init__(s, inner): s.inner = inner; s.config = inner.config; s._c = {}
    def generate(s, a, st, e):
        k = (a, st, e)
        if k not in s._c: s._c[k] = s.inner.generate(a, st, e)
        return s._c[k]
    def thresholds_for(s, a): return s.inner.thresholds_for(a)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-11-03")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()
    end = args.end or str(load_enriched("BTCUSDT", required_cols=["close"])["datetime"].max().date())
    longs = [Cached(build_engine(e)) for e in LONG_ENGINES]
    years = (pd.Timestamp(end) - pd.Timestamp(args.start)).days / 365.25

    def base(carry_size, gate_v2):
        return MultiLegConfig(
            enable_long=True, enable_asset_regime_gate=True, enable_regime_flip_exit=True,
            enable_intra_position_governor=True, enable_carry=True, carry_fraction=carry_size,
            carry_gate_v2=gate_v2, enable_hedge=True)

    runs = {
        "V1.1_carry50_oldgate":  base(0.50, False),
        "V1.2_carry50_gatev2":   base(0.50, True),
        "V1.2_carry75_gatev2":   base(0.75, True),
    }
    report = {"window": [args.start, end], "runs": {}}
    print(f"\nFenêtre {args.start} → {end}  ({years:.1f} ans)")
    print(f"{'Config':<26}{'ROI':>8}{'ann.':>8}{'PF':>7}{'maxDD':>8}  PnL[dir/carry/hedge/fees]")
    print("─" * 84)
    for name, cfg in runs.items():
        res = MultiLegBacktester(longs, cfg, carry_assets=CARRY_ASSETS).run(args.start, end)
        m, p = res.metrics, res.pnl_by_type
        ann = (1 + m.get("total_return", 0)) ** (1 / max(years, 0.1)) - 1
        print(f"{name:<26}{m.get('total_return',0)*100:>7.1f}%{ann*100:>7.1f}%{m.get('pf',0):>7.2f}"
              f"{m.get('max_drawdown',0)*100:>7.1f}%  [{p['directional']:.0f}/{p['carry_funding']:.0f}/{p['hedge']:.0f}/{p['fees']:.0f}]")
        report["runs"][name] = {"metrics": m, "pnl_by_type": p, "annualized": ann}
    Path("reports/CARRY_GATE_V2_PORTFOLIO_IMPACT.json").write_text(json.dumps(report, indent=2, default=str))
    print("\n→ reports/CARRY_GATE_V2_PORTFOLIO_IMPACT.json")


if __name__ == "__main__":
    import pandas as pd
    main()
