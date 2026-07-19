#!/usr/bin/env python3
"""
scripts/run_portfolio_walk_forward.py
─────────────────────────────────────────────────────────────────────────────
Backtest PORTEFEUILLE + comparaison "1 position / cooldown global" (legacy) vs
"multi-position / cooldown local" (portfolio). Chiffre ce que coûtent la
contrainte une-seule-position et le cooldown global.

Usage :
    python3 scripts/run_portfolio_walk_forward.py \
        --engines TRM_TREND_INST --start 2022-01-01 --end 2025-12-31
    python3 scripts/run_portfolio_walk_forward.py \
        --engines TRM_TREND_LONG,PULLBACK_LONG,LIQUIDATION_REBOUND --start 2026-01-01 --end 2026-06-20
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.registry import build_engine
from src.institutional.engines.exit_engine import ExitEngineV1
from src.institutional.risk.governor import RiskGovernor
from src.institutional.portfolio.meta_allocator import UtilityMetaAllocator
from src.institutional.backtest.portfolio_backtester import (
    PortfolioBacktester, PortfolioBacktestConfig,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
OUT = Path("artifacts/institutional/backtests/portfolio")


def _build(engine_ids):
    engines = []
    for eid in engine_ids:
        try:
            engines.append(build_engine(eid))
        except Exception as e:
            print(f"  ! moteur {eid} indisponible: {e}")
    return engines


REGIME_LABELS = {
    2022: "bear / high-vol", 2023: "recovery / risk-on", 2024: "bull / risk-on",
    2025: "mixed", 2026: "hostile long-only (YTD)",
}


def _regime_report(engine_ids, assets, start, end, governor) -> None:
    """Full-stack décomposé par année/régime — ne JAMAIS moyenner bear et bull."""
    import json as _json
    from src.institutional.risk.governor import RiskGovernor, CONSERVATIVE_V1
    engines = _build(engine_ids)
    if not engines:
        print("Aucun moteur disponible."); return
    # governor "none" : mesure l'ALPHA par régime (le governor ne fait que supprimer).
    # Le conservative_v1 est un ratchet monotone sur multi-année → inadapté à ce report.
    gov_hook = None
    if governor == "conservative_v1":
        gov_hook = RiskGovernor(config=CONSERVATIVE_V1).as_hook()
    elif governor == "default":
        gov_hook = RiskGovernor().as_hook()
    cfg = PortfolioBacktestConfig(
        max_open_positions=4, cooldown_mode="local",
        allocator_hook=UtilityMetaAllocator().as_hook(),
        governor_hook=gov_hook,  # exit engine EXCLU (ablation : churn destructeur)
    )
    res = PortfolioBacktester(engines, cfg).run(assets, start, end)
    eq, tr = res.equity, res.trades

    print(f"\n{'='*82}\nFULL-CYCLE REGIME REPORT  engines={engine_ids}  governor={governor}\n{'='*82}")
    print(f"{'Année':<6}{'Régime':<26}{'ROI':>8}{'PF':>7}{'trades':>8}{'t/mo':>7}{'maxDD':>8}{'verdict':>9}")
    print("─" * 82)
    rows = {}
    for year in sorted(REGIME_LABELS):
        ye = eq[eq.index.year == year]
        if len(ye) < 2:
            continue
        roi = float(ye.iloc[-1] / ye.iloc[0] - 1)
        peak = ye.cummax(); dd = float(((ye - peak) / peak).min())
        yt = tr[pd.to_datetime(tr["exit_time"]).dt.year == year] if len(tr) else tr
        pnl = yt["pnl_net"] if len(yt) else pd.Series(dtype=float)
        pf = float(pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum())) if (pnl < 0).any() else (float("inf") if (pnl > 0).any() else 0.0)
        n = int(len(yt)); tpm = n / 12.0
        verdict = "WIN" if roi > 0.01 else ("FLAT" if roi > -0.01 else "LOSS")
        rows[year] = {"regime": REGIME_LABELS[year], "roi": round(roi, 4), "pf": round(pf, 3),
                      "n_trades": n, "trades_month": round(tpm, 1), "max_dd": round(dd, 4), "verdict": verdict}
        print(f"{year:<6}{REGIME_LABELS[year]:<26}{roi*100:>7.1f}%{pf:>7.2f}{n:>8}{tpm:>7.1f}{dd*100:>7.1f}%{verdict:>9}")
    print("─" * 82)
    full_roi = float(eq.iloc[-1] / eq.iloc[0] - 1)
    print(f"FULL-CYCLE ROI {full_roi*100:+.1f}%  | per-engine PnL: {res.per_engine_pnl}")
    # interprétation
    wins = [y for y, r in rows.items() if r["verdict"] == "WIN"]
    losses = [y for y, r in rows.items() if r["verdict"] == "LOSS"]
    print("\nINTERPRÉTATION :")
    if wins and losses:
        print(f"  Long-only = RÉGIME-DÉPENDANT (gagne {wins}, perd {losses}) → hedge/carry obligatoires.")
    elif not wins:
        print("  Aucun régime gagnant → moteurs morts (pas juste 2026).")
    else:
        print("  Tous régimes gagnants → revérifier le leakage.")
    out = Path("reports/portfolio_full_cycle_after_datastore_recovery.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps({"engines": engine_ids, "governor": governor,
                                "full_cycle_roi": full_roi, "by_year": rows,
                                "per_engine_pnl": res.per_engine_pnl}, indent=2, default=str))
    print(f"\n→ {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="TRM_TREND_INST")
    ap.add_argument("--assets", default=None)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--full-stack", action="store_true",
                    help="ajoute allocateur utility + governor + exit engine")
    ap.add_argument("--regime-report", action="store_true",
                    help="décompose le full-stack par année/régime (2022-2026)")
    ap.add_argument("--governor", default="default", choices=["default", "conservative_v1", "none"])
    args = ap.parse_args()

    if args.regime_report:
        _regime_report(engine_ids=[e.strip() for e in args.engines.split(",") if e.strip()],
                       assets=[a.strip() for a in args.assets.split(",")] if args.assets else None,
                       start=args.start, end=args.end, governor=args.governor)
        return

    engine_ids = [e.strip() for e in args.engines.split(",") if e.strip()]
    assets = [a.strip() for a in args.assets.split(",")] if args.assets else None
    engines = _build(engine_ids)
    if not engines:
        print("Aucun moteur disponible."); return

    configs = {
        "legacy_single_global": PortfolioBacktestConfig(
            max_open_positions=1, cooldown_mode="global", global_cooldown_hours=24),
        "portfolio_multi_local": PortfolioBacktestConfig(
            max_open_positions=4, cooldown_mode="local"),
    }

    if args.full_stack:
        all_assets = sorted({a for e in engines for a in e.assets})
        ex = ExitEngineV1(assets=all_assets); ex.preload(args.start, args.end)
        configs["full_stack"] = PortfolioBacktestConfig(
            max_open_positions=4, cooldown_mode="local",
            allocator_hook=UtilityMetaAllocator().as_hook(),
            governor_hook=RiskGovernor().as_hook(),
            exit_hook=ex.as_hook() if ex.available else None,
        )

    report = {"engines": engine_ids, "start": args.start, "end": args.end, "runs": {}}
    for name, cfg in configs.items():
        bt = PortfolioBacktester(engines, cfg)
        res = bt.run(assets, args.start, args.end)
        print(f"\n=== {name} ===")
        print(res.summary())
        report["runs"][name] = {
            "final_equity": float(res.equity.iloc[-1]),
            "metrics": res.metrics,
            "per_engine_pnl": res.per_engine_pnl,
            "gate": res.gate,
        }

    # coût des contraintes legacy
    a = report["runs"]["legacy_single_global"]["metrics"]
    b = report["runs"]["portfolio_multi_local"]["metrics"]
    delta = {
        "trades_delta": b.get("n_trades", 0) - a.get("n_trades", 0),
        "roi_month_median_delta": b.get("roi_month_median", 0) - a.get("roi_month_median", 0),
        "pf_delta": b.get("pf", 0) - a.get("pf", 0),
    }
    report["constraint_cost"] = delta
    print(f"\n=== COÛT CONTRAINTES (multi - single) ===\n{json.dumps(delta, indent=2)}")

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "wf_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n→ rapport écrit: {out_path}")


if __name__ == "__main__":
    main()
