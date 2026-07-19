#!/usr/bin/env python3
"""
scripts/run_parallel_50_backtest.py
─────────────────────────────────────────────────────────────────────────────
Premier test PARALLEL_50 sur 100K avec données déjà récoltées.

Compare, MÊME config (asset_regime_gate + flip_exit + intra_gov + carry BTC/ETH +
hedge, carry_fraction 0.50), capital 100K, fenêtre baseline :
  • BASELINE_9   : PULLBACK sur son univers par défaut (BTC/ETH/SOL/BNB)
  • PARALLEL_50  : PULLBACK sur les 49 actifs valides (univers élargi)

TRM_TREND_INST (BTC/ETH only — modèles) et LIQUIDATION_REBOUND (data-gated)
identiques dans les deux runs → on isole l'effet "élargir l'univers PULLBACK".
Honnête : aucun backtest sur actif sans données ; liquidations toujours forward-only.
"""
from __future__ import annotations
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
import logging; logging.basicConfig(level=logging.ERROR)
import yaml

from src.institutional.engines.base import AlphaEngine
from src.institutional.engines.registry import build_engine
from src.institutional.engines.pullback_long.infer import PullbackLongEngine
from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig
from src.institutional.universe.asset_quality_filter import assess_universe, AssetQualityStatus as Q

ROOT = Path(__file__).parents[1]
START, END = "2022-11-03", "2026-06-28"
CAPITAL = 100_000.0


class Cached(AlphaEngine):
    def __init__(self, inner): self.inner = inner; self.config = inner.config; self._c = {}
    def generate(self, a, s, e):
        k = (a, s, e)
        if k not in self._c: self._c[k] = self.inner.generate(a, s, e)
        return self._c[k]
    def thresholds_for(self, a): return self.inner.thresholds_for(a)


def cfg():
    return MultiLegConfig(
        initial_capital=CAPITAL, carry_fraction=0.50,
        enable_long=True, enable_asset_regime_gate=True,
        enable_regime_flip_exit=True, enable_intra_position_governor=True,
        enable_carry=True, carry_gate_v2=False, enable_hedge=True)


def run(label, pullback_assets):
    longs = [
        Cached(build_engine("TRM_TREND_INST")),                 # BTC/ETH (modèles)
        Cached(PullbackLongEngine(assets=pullback_assets)),     # univers variable
        Cached(build_engine("LIQUIDATION_REBOUND")),            # data-gated → ~0
    ]
    bt = MultiLegBacktester(longs, cfg(), carry_assets=["BTCUSDT", "ETHUSDT"])
    t0 = time.time()
    res = bt.run(START, END)
    m = res.metrics
    eq0, eq1 = float(res.equity.iloc[0]), float(res.equity.iloc[-1])
    out = {
        "label": label, "n_pullback_assets": len(pullback_assets),
        "equity_start": eq0, "equity_end": eq1,
        "gain_usd": eq1 - eq0, "roi_total": eq1 / eq0 - 1,
        "annualized": m.get("annualized"), "pf": m.get("pf"),
        "max_drawdown": m.get("max_drawdown"),
        "roi_month_median": m.get("roi_month_median"),
        "n_trades": m.get("n_trades") or m.get("trades"),
        "pnl_by_type": res.pnl_by_type, "runtime_s": round(time.time() - t0, 1),
    }
    print(f"\n[{label}] {res.summary()}")
    print(f"    100K → {eq1:,.0f}  (gain {eq1-eq0:+,.0f} USD, {(eq1/eq0-1)*100:+.1f}%)  runtime {out['runtime_s']}s")
    return out


def main():
    U = yaml.safe_load((ROOT/"configs/portfolio_v1_1_parallel_50.yaml").read_text())["universe"]
    qual = assess_universe(U)
    tradable = [s for s in U if qual[s].status != Q.BLOCK]
    print(f"Univers tradable PARALLEL_50: {len(tradable)}/{len(U)}", flush=True)

    results = {}
    results["BASELINE_9"]  = run("BASELINE_9",  ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
    results["PARALLEL_50"] = run("PARALLEL_50", tradable)

    (ROOT/"reports/parallel_50").mkdir(parents=True, exist_ok=True)
    (ROOT/"reports/parallel_50/parallel_50_backtest.json").write_text(json.dumps(results, indent=2, default=float))
    b, p = results["BASELINE_9"], results["PARALLEL_50"]
    print("\n" + "="*64)
    print(f"{'':20} {'BASELINE_9':>14} {'PARALLEL_50':>14}")
    print(f"{'gain sur 100K':20} {b['gain_usd']:>+13,.0f}$ {p['gain_usd']:>+13,.0f}$")
    print(f"{'ROI total':20} {b['roi_total']*100:>13.1f}% {p['roi_total']*100:>13.1f}%")
    print(f"{'annualisé':20} {(b['annualized'] or 0)*100:>13.1f}% {(p['annualized'] or 0)*100:>13.1f}%")
    print(f"{'PF':20} {b['pf']:>14.2f} {p['pf']:>14.2f}")
    print(f"{'maxDD':20} {b['max_drawdown']*100:>13.1f}% {p['max_drawdown']*100:>13.1f}%")
    print(f"{'trades':20} {str(b['n_trades']):>14} {str(p['n_trades']):>14}")
    print("="*64)


if __name__ == "__main__":
    main()
