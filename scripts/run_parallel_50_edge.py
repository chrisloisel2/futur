#!/usr/bin/env python3
"""
scripts/run_parallel_50_edge.py
─────────────────────────────────────────────────────────────────────────────
PARALLEL_50 + asset edge gate (causal) sur la meilleure variante (RANKED7).
On ne trade un alt que s'il a prouvé un edge net de frais positif AVANT (folds < Y).

  • RANKED7_EDGE        : edge net moyen prior > 0,    min 20 signaux prior
  • RANKED7_EDGE_STRICT : edge net moyen prior > 0,1%, min 30 signaux prior

Réf : BASELINE_9 +18.2% · RANKED7 +10.6%. Données réelles, 100K, 2022-11→2026-06.
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


def cfg(**over):
    d = dict(
        initial_capital=CAPITAL, carry_fraction=0.50, max_open_longs=7, long_fraction=0.043,
        enable_long=True, enable_asset_regime_gate=True,
        enable_regime_flip_exit=True, enable_intra_position_governor=True,
        enable_carry=True, carry_gate_v2=False, enable_hedge=True,
        enable_ranker=True, ranker_max_per_bucket=2, ranker_max_meme=1, ranker_max_alt=5,
        enable_asset_edge_gate=True,
    )
    d.update(over)
    return MultiLegConfig(**d)


def run(label, pullback, c):
    longs = [Cached(build_engine("TRM_TREND_INST")),
             Cached(PullbackLongEngine(assets=pullback)),
             Cached(build_engine("LIQUIDATION_REBOUND"))]
    bt = MultiLegBacktester(longs, c, carry_assets=["BTCUSDT", "ETHUSDT"])
    t0 = time.time(); res = bt.run(START, END); m = res.metrics
    eq1 = float(res.equity.iloc[-1])
    out = {"label": label, "equity_end": eq1, "gain_usd": eq1 - CAPITAL,
           "roi_total": eq1 / CAPITAL - 1, "pf": m.get("pf"),
           "max_drawdown": m.get("max_drawdown"), "n_legs": m.get("n_legs"),
           "pnl_by_type": res.pnl_by_type, "runtime_s": round(time.time() - t0, 1)}
    print(f"\n[{label}] {res.summary()}")
    print(f"    100K → {eq1:,.0f}  ({(eq1/CAPITAL-1)*100:+.1f}%)  legs={m.get('n_legs')}  "
          f"runtime {out['runtime_s']}s", flush=True)
    return out


def main():
    U = yaml.safe_load((ROOT/"configs/portfolio_v1_1_parallel_50.yaml").read_text())["universe"]
    qual = assess_universe(U)
    tradable = [s for s in U if qual[s].status != Q.BLOCK]
    print(f"Univers tradable: {len(tradable)}/{len(U)} | ranker+edge_gate ON", flush=True)

    results = {}
    results["RANKED7_EDGE"] = run("RANKED7_EDGE", tradable,
                                  cfg(asset_edge_min_net=0.0, asset_edge_min_signals=20))
    results["RANKED7_EDGE_STRICT"] = run("RANKED7_EDGE_STRICT", tradable,
                                         cfg(asset_edge_min_net=0.001, asset_edge_min_signals=30))

    (ROOT/"reports/parallel_50/parallel_50_edge.json").write_text(json.dumps(results, indent=2, default=float))

    ref = json.loads((ROOT/"reports/parallel_50/parallel_50_backtest.json").read_text())
    rnk = json.loads((ROOT/"reports/parallel_50/parallel_50_ranked.json").read_text())
    print("\n" + "="*72)
    print(f"{'config':22}{'gain 100K':>13}{'ROI':>9}{'PF':>7}{'maxDD':>9}{'legs':>9}")
    def row(lbl, d, legs):
        print(f"{lbl:22}{d['gain_usd']:>+12,.0f}${d['roi_total']*100:>8.1f}%"
              f"{d['pf']:>7.2f}{d['max_drawdown']*100:>8.1f}%{str(legs):>9}")
    row("BASELINE_9", ref["BASELINE_9"], "-")
    row("RANKED7", rnk["RANKED7"], rnk["RANKED7"]["n_legs"])
    for k in ("RANKED7_EDGE", "RANKED7_EDGE_STRICT"):
        row(k, results[k], results[k]["n_legs"])
    print("="*72)


if __name__ == "__main__":
    main()
