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

    def cfg(long, macro_gate, asset_gate, flip, intra, carry, hedge):
        return MultiLegConfig(
            enable_long=long, enable_regime_gate=macro_gate,
            enable_asset_regime_gate=asset_gate,
            enable_regime_flip_exit=flip, enable_intra_position_governor=intra,
            enable_carry=carry, enable_hedge=hedge)

    # Matrice Phase 47 (asset regime gate vs macro gate)
    runs = {
        "M_macro_gate":        (longs, cfg(True, True,  False, False, False, False, False)),
        "A_asset_gate":        (longs, cfg(True, False, True,  False, False, False, False)),
        "A_asset_flip":        (longs, cfg(True, False, True,  True,  False, False, False)),
        "A_asset_flip_intra":  (longs, cfg(True, False, True,  True,  True,  False, False)),
        "A_asset_carry":       (longs, cfg(True, False, True,  True,  True,  True,  False)),
        "A_asset_full":        (longs, cfg(True, False, True,  True,  True,  True,  True)),
        "E_carry":             ([],    cfg(False, False, False, False, False, True, False)),
    }

    report = {"window": [args.start, args.end], "runs": {}}
    per_asset_ref = None
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
        if name == "A_asset_flip_intra" and len(res.leg_ledger):
            ll = res.leg_ledger
            longs_ll = ll[ll["leg_type"] == "LONG_SPOT"]
            per_asset_ref = longs_ll.groupby("asset").agg(
                n=("net_pnl", "size"), net_pnl=("net_pnl", "sum"),
                price_pnl=("price_pnl", "sum"), fees=("costs", "sum")).round(1)

    if per_asset_ref is not None:
        print(f"\n=== PnL LONG par actif (A_asset_flip_intra) — identifier les destructeurs ===")
        print(per_asset_ref.sort_values("net_pnl").to_string())
        report["per_asset_long_pnl"] = per_asset_ref.reset_index().to_dict("records")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
