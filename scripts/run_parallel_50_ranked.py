#!/usr/bin/env python3
"""
scripts/run_parallel_50_ranked.py
─────────────────────────────────────────────────────────────────────────────
PARALLEL_50 AVEC ranker câblé. Compare (100K, 2022-11→2026-06, données réelles) :

  • RANKED3      : ranker (2/bucket, 1 meme, 5 alt) + max 3 longs   → MÊME risque que baseline
  • RANKED7      : ranker + max 7 longs, taille/nom réduite          → gross borné (~baseline)
  • RANKED3_FEE  : RANKED3 + filtre coût (ER ≥ 2× frais aller-retour) → coupe le churn

Référence (déjà mesurés, reports/parallel_50/parallel_50_backtest.json) :
  BASELINE_9 = 100K→118,186 (+18.2%) · PARALLEL_50_NAIVE = 100K→60,418 (-39.6%).
TRM (BTC/ETH) + LIQUIDATION (data-gated) identiques. Aucun backtest sur actif sans données.
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


def base_cfg(**over):
    d = dict(
        initial_capital=CAPITAL, carry_fraction=0.50,
        enable_long=True, enable_asset_regime_gate=True,
        enable_regime_flip_exit=True, enable_intra_position_governor=True,
        enable_carry=True, carry_gate_v2=False, enable_hedge=True,
        enable_ranker=True, ranker_max_per_bucket=2, ranker_max_meme=1, ranker_max_alt=5,
    )
    d.update(over)
    return MultiLegConfig(**d)


def run(label, pullback, cfg, n_trades_ref=None):
    longs = [
        Cached(build_engine("TRM_TREND_INST")),
        Cached(PullbackLongEngine(assets=pullback)),
        Cached(build_engine("LIQUIDATION_REBOUND")),
    ]
    bt = MultiLegBacktester(longs, cfg, carry_assets=["BTCUSDT", "ETHUSDT"])
    t0 = time.time()
    res = bt.run(START, END)
    m = res.metrics
    eq0, eq1 = float(res.equity.iloc[0]), float(res.equity.iloc[-1])
    out = {
        "label": label, "equity_end": eq1, "gain_usd": eq1 - eq0,
        "roi_total": eq1 / eq0 - 1, "pf": m.get("pf"),
        "max_drawdown": m.get("max_drawdown"), "n_legs": m.get("n_legs"),
        "roi_month_median": m.get("roi_month_median"),
        "pnl_by_type": res.pnl_by_type, "runtime_s": round(time.time() - t0, 1),
    }
    print(f"\n[{label}] {res.summary()}")
    print(f"    100K → {eq1:,.0f}  (gain {eq1-eq0:+,.0f} USD, {(eq1/eq0-1)*100:+.1f}%)  "
          f"legs={m.get('n_legs')}  runtime {out['runtime_s']}s", flush=True)
    return out


def main():
    U = yaml.safe_load((ROOT/"configs/portfolio_v1_1_parallel_50.yaml").read_text())["universe"]
    qual = assess_universe(U)
    tradable = [s for s in U if qual[s].status != Q.BLOCK]
    print(f"Univers tradable: {len(tradable)}/{len(U)} | ranker ON", flush=True)

    results = {}
    results["RANKED3"]     = run("RANKED3",     tradable, base_cfg(max_open_longs=3, long_fraction=0.10))
    results["RANKED7"]     = run("RANKED7",     tradable, base_cfg(max_open_longs=7, long_fraction=0.043))
    results["RANKED3_FEE"] = run("RANKED3_FEE", tradable, base_cfg(max_open_longs=3, long_fraction=0.10,
                                                                   long_min_er_cost_mult=2.0))

    (ROOT/"reports/parallel_50").mkdir(parents=True, exist_ok=True)
    (ROOT/"reports/parallel_50/parallel_50_ranked.json").write_text(json.dumps(results, indent=2, default=float))

    # table comparative complète (réf naïve/baseline depuis le run précédent)
    ref = json.loads((ROOT/"reports/parallel_50/parallel_50_backtest.json").read_text())
    print("\n" + "="*78)
    print(f"{'config':16}{'gain 100K':>14}{'ROI':>10}{'PF':>8}{'maxDD':>10}{'legs':>10}")
    def row(lbl, d, legs):
        print(f"{lbl:16}{d['gain_usd']:>+13,.0f}${d['roi_total']*100:>9.1f}%"
              f"{d['pf']:>8.2f}{d['max_drawdown']*100:>9.1f}%{str(legs):>10}")
    row("BASELINE_9", ref["BASELINE_9"], ref["BASELINE_9"].get("n_trades"))
    row("NAIVE_50", ref["PARALLEL_50"], ref["PARALLEL_50"].get("n_trades"))
    for k in ("RANKED3", "RANKED7", "RANKED3_FEE"):
        row(k, results[k], results[k]["n_legs"])
    print("="*78)


if __name__ == "__main__":
    main()
