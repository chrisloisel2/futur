#!/usr/bin/env python3
"""
scripts/run_multileg_ablation.py
─────────────────────────────────────────────────────────────────────────────
Phase 38 — ablations long / carry / hedge sur le backtester multi-jambes.

Runs (cf. brief) :
  A long · B long+carry · C long+hedge · D long+carry+hedge (final)
  E carry · F carry+hedge · G hedge seul (doit être neutre : 0 trade)

Usage :
    python3 scripts/run_multileg_ablation.py --start 2022-01-01 --end 2026-06-20
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
# cross_sectional exclu du book long : 43k signaux/churn + gate échouée (autopsy)
LONG_ENGINES = ["TRM_TREND_INST", "PULLBACK_LONG", "LIQUIDATION_REBOUND"]
CARRY_ASSETS = ["BTCUSDT", "ETHUSDT"]


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
    ap.add_argument("--out", default="reports/multileg_ablation_2022_2026.json")
    args = ap.parse_args()

    longs = [Cached(build_engine(e)) for e in LONG_ENGINES]

    def cfg(long, gate, carry, hedge):
        return MultiLegConfig(enable_long=long, enable_regime_gate=gate,
                              enable_carry=carry, enable_hedge=hedge)

    # Matrice Phase 40 (regime gate)
    runs = {
        "A_long_raw":          (longs, cfg(True, False, False, False)),
        "B_long_gated":        (longs, cfg(True, True, False, False)),
        "C_long_gated_carry":  (longs, cfg(True, True, True, False)),
        "D_final":             (longs, cfg(True, True, True, True)),
        "E_carry":             ([],    cfg(False, False, True, False)),
        "F_carry_hedge":       ([],    cfg(False, False, True, True)),
        "G_long_gated_hedge":  (longs, cfg(True, True, False, True)),
    }

    report = {"window": [args.start, args.end], "runs": {}}
    print(f"\n{'Run':<22}{'ROI':>8}{'PF':>7}{'ret/mo':>9}{'maxDD':>8}{'CVaR':>8}  PnL[dir/carry/hedge/fees]")
    print("─" * 96)
    for name, (engs, c) in runs.items():
        bt = MultiLegBacktester(engs, c, carry_assets=(CARRY_ASSETS if c.enable_carry else []))
        res = bt.run(args.start, args.end)
        m = res.metrics; p = res.pnl_by_type
        print(f"{name:<22}{m.get('total_return',0)*100:>7.1f}%{m.get('pf',0):>7.2f}"
              f"{m.get('roi_month_median',0)*100:>8.2f}%{m.get('max_drawdown',0)*100:>7.1f}%"
              f"{m.get('cvar_95',0)*100:>7.2f}%  "
              f"[{p['directional']:.0f}/{p['carry_funding']:.0f}/{p['hedge']:.0f}/{p['fees']:.0f}]")
        report["runs"][name] = {"metrics": m, "pnl_by_type": p}

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
