#!/usr/bin/env python3
"""
scripts/run_parallel_50_edge_validation.py
─────────────────────────────────────────────────────────────────────────────
Validation de robustesse de RANKED7_EDGE (le candidat non-tuné, min_net=0)
avant toute promotion — les 3 checks listés dans PARALLEL_50_EDGE_GATE_WIN.md :

  1. EDGE_BASE     : re-run référence + ventilation PAR ANNÉE (régimes 2022-2026)
  2. EDGE_COSTX2   : tous les coûts doublés (taker 10bps, slippage 4bps, maker 2bps)
  3. EDGE_SIZE150 / EDGE_SIZE200 : frontière sizing — long_fraction ×1.5 / ×2
     (carry inchangé : 50%×2 actifs sature déjà le notional). Gate : maxDD ≤ 3%.

Aucun tuning : le gate edge reste min_net=0.0 / min_signals=20 partout.
Sortie : reports/parallel_50/parallel_50_edge_validation.json
"""
from __future__ import annotations
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
import logging; logging.basicConfig(level=logging.ERROR)
import yaml
import pandas as pd

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
        enable_asset_edge_gate=True, asset_edge_min_net=0.0, asset_edge_min_signals=20,
    )
    d.update(over)
    return MultiLegConfig(**d)


def yearly_breakdown(eq: pd.Series) -> dict:
    """ROI et maxDD par année civile, chaîné sur la dernière equity de l'année précédente."""
    out, prev = {}, None
    for y, g in eq.groupby(eq.index.year):
        base = prev if prev is not None else g.iloc[0]
        seg = pd.concat([pd.Series([base]), g]) if prev is not None else g
        out[int(y)] = {"ret": float(g.iloc[-1] / base - 1),
                       "maxdd": float((seg / seg.cummax() - 1).min())}
        prev = g.iloc[-1]
    return out


def run(label, pullback, c):
    longs = [Cached(build_engine("TRM_TREND_INST")),
             Cached(PullbackLongEngine(assets=pullback)),
             Cached(build_engine("LIQUIDATION_REBOUND"))]
    bt = MultiLegBacktester(longs, c, carry_assets=["BTCUSDT", "ETHUSDT"])
    t0 = time.time(); res = bt.run(START, END); m = res.metrics
    eq = res.equity; eq1 = float(eq.iloc[-1])
    out = {"label": label, "equity_end": eq1, "gain_usd": eq1 - CAPITAL,
           "roi_total": eq1 / CAPITAL - 1, "pf": m.get("pf"),
           "max_drawdown": m.get("max_drawdown"), "n_legs": m.get("n_legs"),
           "roi_month_median": m.get("roi_month_median"),
           "pnl_by_type": res.pnl_by_type, "yearly": yearly_breakdown(eq),
           "runtime_s": round(time.time() - t0, 1)}
    print(f"\n[{label}] {res.summary()}")
    print(f"    100K → {eq1:,.0f}  ({(eq1/CAPITAL-1)*100:+.1f}%)  "
          f"maxDD {m.get('max_drawdown',0)*100:.1f}%  legs={m.get('n_legs')}  "
          f"runtime {out['runtime_s']}s", flush=True)
    for y, d in out["yearly"].items():
        print(f"      {y}: ret {d['ret']*100:+6.2f}%   maxDD {d['maxdd']*100:6.2f}%", flush=True)
    return out


def main():
    U = yaml.safe_load((ROOT/"configs/portfolio_v1_1_parallel_50.yaml").read_text())["universe"]
    qual = assess_universe(U)
    tradable = [s for s in U if qual[s].status != Q.BLOCK]
    print(f"Univers tradable: {len(tradable)}/{len(U)} | validation RANKED7_EDGE (non-tuné)", flush=True)

    results = {}
    results["EDGE_BASE"] = run("EDGE_BASE", tradable, cfg())
    results["EDGE_COSTX2"] = run("EDGE_COSTX2", tradable,
                                 cfg(taker_fee_bps=10.0, slippage_bps=4.0, maker_fee_bps=2.0))
    results["EDGE_SIZE150"] = run("EDGE_SIZE150", tradable, cfg(long_fraction=0.043 * 1.5))
    results["EDGE_SIZE200"] = run("EDGE_SIZE200", tradable, cfg(long_fraction=0.043 * 2.0))

    (ROOT/"reports/parallel_50/parallel_50_edge_validation.json").write_text(
        json.dumps(results, indent=2, default=float))

    print("\n" + "=" * 78)
    print(f"{'config':16}{'gain 100K':>13}{'ROI':>9}{'PF':>7}{'maxDD':>9}{'legs':>7}  verdict")
    for k, d in results.items():
        dd_ok = d["max_drawdown"] >= -0.03
        pos = d["roi_total"] > 0
        verdict = "PASS" if (dd_ok and pos) else ("DD>3%" if pos else "NEG")
        print(f"{k:16}{d['gain_usd']:>+12,.0f}${d['roi_total']*100:>8.1f}%"
              f"{d['pf']:>7.2f}{d['max_drawdown']*100:>8.1f}%{str(d['n_legs']):>7}  {verdict}")
    print("=" * 78)


if __name__ == "__main__":
    main()
