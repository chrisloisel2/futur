#!/usr/bin/env python3
"""
scripts/measure_v12_plus_stack_overlay.py
─────────────────────────────────────────────────────────────────────────────
Mesure de l'OVERLAY : V1.2 (socle carry+longs gatés, multileg horaire) + stack
événementiel 3-moteurs (unité vague, 2 %/trade) sur le MÊME capital.

Combinaison honnête au niveau des PnL QUOTIDIENS :
  equity_combinée(t) = equity_V12(t) × equity_stack(t)
(les deux stratégies partagent le capital ; l'overlay événementiel utilise du
margin déjà disponible dans le budget gross V1.2 — voir réserves).

Mesures : ROI/an, maxDD, corrélation quotidienne des rendements, contribution.
Fenêtre : 2023-01 → dernière barre commune (2022 exclu : folds sans train).
Sortie : reports/liq_cascade/V12_PLUS_STACK_OVERLAY.{json,md}
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import logging; logging.basicConfig(level=logging.ERROR)
import yaml

DIR = ROOT / "reports" / "liq_cascade"
V12_EQ = DIR / "v12_equity_daily.parquet"
HOLD_H = {"LIQ_CASCADE": 4, "CROWDING_REVERSAL": 24, "PREMIUM_DISLOCATION": 4}
# Tapes multi-horizon (consensus + term-structure exit) : sorties fwd_8h/8h/24h
# (cf. MULTIHORIZON_ENGINE.json) → occupation des slots plus longue.
HOLD_H_MH = {"LIQ_CASCADE": 8, "CROWDING_REVERSAL": 24, "PREMIUM_DISLOCATION": 8}
SIZE = 0.02
WAVE_GAP = pd.Timedelta(minutes=30)
TOP_K = 3
START = "2023-01-01"


def v12_equity() -> pd.Series:
    """Equity quotidienne V1.2 (cachée — run multileg ~4 min sinon)."""
    if V12_EQ.exists():
        s = pd.read_parquet(V12_EQ)["equity"]
        s.index = pd.to_datetime(pd.read_parquet(V12_EQ)["date"], utc=True)
        return s
    from src.institutional.engines.base import AlphaEngine
    from src.institutional.engines.registry import build_engine
    from src.institutional.engines.pullback_long.infer import PullbackLongEngine
    from src.institutional.backtest.multileg_backtester import (
        MultiLegBacktester, MultiLegConfig)
    from src.institutional.universe.asset_quality_filter import (
        assess_universe, AssetQualityStatus as Q)

    class Cached(AlphaEngine):
        def __init__(s2, i): s2.inner = i; s2.config = i.config; s2._c = {}
        def generate(s2, a, st, e):
            k = (a, st, e)
            if k not in s2._c: s2._c[k] = s2.inner.generate(a, st, e)
            return s2._c[k]
        def thresholds_for(s2, a): return s2.inner.thresholds_for(a)

    spec = yaml.safe_load((ROOT / "configs/portfolio_v1_2_candidate.yaml").read_text())
    U = yaml.safe_load((ROOT / spec["universe_from"]).read_text())["universe"]
    qual = assess_universe(U)
    tradable = [s for s in U if qual[s].status != Q.BLOCK]
    longs = [Cached(build_engine("TRM_TREND_INST")),
             Cached(PullbackLongEngine(assets=tradable)),
             Cached(build_engine("LIQUIDATION_REBOUND"))]
    cfg = MultiLegConfig(**{**spec["config"], "initial_capital": spec["capital"]})
    from src.institutional.engines.legacy_bridge import load_enriched
    end = spec["window"]["end"] or str(
        load_enriched("BTCUSDT", required_cols=["close"])["datetime"].max().date())
    t0 = time.time()
    res = MultiLegBacktester(longs, cfg, carry_assets=spec["carry_assets"]).run(
        spec["window"]["start"], end)
    print(f"V1.2 multileg : {time.time()-t0:.0f}s", flush=True)
    eq = res.equity.resample("D").last().dropna()
    eq = eq / eq.iloc[0]
    pd.DataFrame({"date": eq.index, "equity": eq.values}).to_parquet(V12_EQ, index=False)
    return eq


def stack_equity_daily(tapes: str = "legacy") -> pd.Series:
    """Equity quotidienne du stack wave-unitisé à 2 %/trade (tapes OOS)."""
    hold = HOLD_H if tapes == "legacy" else HOLD_H_MH
    suffix = "_trades.parquet" if tapes == "legacy" else "_MH_trades.parquet"
    frames = []
    for name in hold:
        p = DIR / f"{name}{suffix}"
        t = pd.read_parquet(p)
        t["event_time"] = pd.to_datetime(t["event_time"], utc=True)
        t["engine"] = name
        frames.append(t)
    allt = pd.concat(frames, ignore_index=True).sort_values("event_time")
    allt = allt[np.isfinite(allt["net"])]
    allt["year"] = allt["event_time"].dt.year
    allt["rank"] = allt.groupby(["engine", "year"])["score"].rank(pct=True)
    times = allt["event_time"].values
    w, wid = np.zeros(len(allt), dtype=int), 0
    for i in range(1, len(allt)):
        if (times[i] - times[i - 1]) > WAVE_GAP.to_timedelta64():
            wid += 1
        w[i] = wid
    allt["wave"] = w
    allt = (allt.sort_values(["wave", "rank"], ascending=[True, False])
                .drop_duplicates(subset=["wave", "symbol"], keep="first"))
    allt["k"] = allt.groupby("wave").cumcount()
    allt = allt[allt["k"] < TOP_K].sort_values("event_time")

    equity, curve, open_pos = 1.0, [], {}
    for _, r in allt.iterrows():
        t = r["event_time"]
        open_pos = {s: u for s, u in open_pos.items() if u > t}
        if r["symbol"] in open_pos:
            continue
        equity *= (1 + r["net"] * SIZE)
        curve.append((t, equity))
        open_pos[r["symbol"]] = t + pd.Timedelta(hours=hold[r["engine"]])
    s = pd.Series([c[1] for c in curve],
                  index=pd.DatetimeIndex([c[0] for c in curve]))
    return s.resample("D").last().ffill()


def stats(eq: pd.Series, label: str) -> dict:
    r = eq.pct_change().dropna()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"label": label, "roi_total": round(float(eq.iloc[-1] / eq.iloc[0] - 1), 4),
            "roi_ann": round(float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1), 4),
            "maxdd": round(dd, 4),
            "sharpe": round(float(r.mean() / max(r.std(), 1e-12) * np.sqrt(365)), 2)}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", choices=["legacy", "mh"], default="legacy",
                    help="mh = tapes multi-horizon (config production shadow)")
    args = ap.parse_args()
    v12 = v12_equity()
    stk = stack_equity_daily(args.tapes)
    stack_label = ("STACK 3 moteurs (2%/trade)" if args.tapes == "legacy"
                   else "STACK 3 moteurs MH (2%/trade)")
    legs = {"V1.2 (socle)": v12, stack_label: stk}
    basis_pq = DIR / "basis_term_equity_daily.parquet"
    if basis_pq.exists():
        b = pd.read_parquet(basis_pq)
        bs = pd.Series(b["equity"].values,
                       index=pd.to_datetime(b["date"], utc=True))
        legs["BASIS_TERM (quarterly, 50%)"] = bs

    start = max([pd.Timestamp(START, tz="UTC")] + [s.index[0] for s in legs.values()])
    end = min(s.index[-1] for s in legs.values())
    idx = pd.date_range(start, end, freq="D", tz="UTC")
    norm = {}
    for k, s in legs.items():
        full = s.sort_index().resample("D").last().ffill()   # grille quotidienne
        x = full.reindex(idx).ffill()
        norm[k] = x / x.iloc[0]
    combo = None
    for x in norm.values():
        combo = x if combo is None else combo * x

    rows = [stats(x, k) for k, x in norm.items()]
    rows.append(stats(combo, f"COMBINÉ ({len(norm)} jambes)"))
    rets = pd.DataFrame({k: x.pct_change() for k, x in norm.items()}).dropna()
    corr = rets.corr().round(3)
    stk_r = stk.pct_change().dropna()
    stack_by_year = {int(y): round(float(v), 4) for y, v in
                     ((1 + stk_r).groupby(stk_r.index.year).prod() - 1).items()}
    out = {"window": [str(start.date()), str(end.date())],
           "tapes": args.tapes, "stack_by_year": stack_by_year,
           "daily_corr": corr.to_dict(), "stats": rows}
    L = [f"# Overlay mesuré — {len(norm)} jambes (tapes {args.tapes})\n",
         f"Fenêtre {start.date()} → {end.date()}\n",
         "## Corrélations quotidiennes\n", corr.to_markdown(),
         "\n## Statistiques\n", pd.DataFrame(rows).to_markdown(index=False),
         "\n## Stack — ROI par année\n",
         " · ".join(f"{y} {v:+.2%}" for y, v in stack_by_year.items()),
         "\n\n## Réserves\n",
         "- Overlay = produit des equity (capital partagé) ; gross combiné à",
         "  vérifier en intégration multileg (portfolio margin requis).",
         "- Stack = tapes OOS (prix 5-min implicite, slippage cascade à valider) ;",
         "  2022 exclu du stack (folds sans entraînement).",
         "- BASIS_TERM : S≈perp close (approx documentée), liquidité quarterly",
         "  moindre que perp, MTM réel via klines 1d.",
         "- Promotion : SHADOW forward ≥30 j requis (timer déployé)."]
    stem = ("V12_PLUS_STACK_OVERLAY" if args.tapes == "legacy"
            else "V12_PLUS_STACK_OVERLAY_MH")
    (DIR / f"{stem}.json").write_text(json.dumps(out, indent=2, default=str))
    (DIR / f"{stem}.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
