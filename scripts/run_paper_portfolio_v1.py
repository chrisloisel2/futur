#!/usr/bin/env python3
"""
scripts/run_paper_portfolio_v1.py
─────────────────────────────────────────────────────────────────────────────
Paper-live Portfolio V1.1 (Timeline A) — socle défensif :
    carry 50% delta-neutral + longs asset-gated + hedge governor.

But : prouver que le backtest se reproduit en conditions live (pas devenir riche).

Implémentation : re-run déterministe du backtester multi-jambes de paper_start →
dernière barre enrichie disponible (les fichiers enriched sont mis à jour en live
par le scheduler futur-api). On snapshote equity + 4 ledgers + state à chaque
cycle dans reports/paper_live/. --loop-interval pour tourner en continu (systemd).

    python3 scripts/run_paper_portfolio_v1.py --capital 100000 --carry-size 0.50 \
        --enable-carry-delta-neutral --enable-asset-regime-gate --enable-hedge-governor-v1 --mode paper
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.registry import build_engine
from src.institutional.engines.legacy_bridge import load_enriched
from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
OUT = Path("reports/paper_live")
LONG_ENGINES = ["PULLBACK_LONG", "LIQUIDATION_REBOUND"]   # 2026 OOS via model_2025
CARRY_ASSETS = ["BTCUSDT", "ETHUSDT"]


def _latest_enriched_ts() -> str:
    df = load_enriched("BTCUSDT", required_cols=["close"])
    return str(df["datetime"].max().date()) if df is not None and len(df) else "2026-06-20"


def run_once(args) -> dict:
    end = _latest_enriched_ts()
    longs = [build_engine(e) for e in LONG_ENGINES]
    cfg = MultiLegConfig(
        initial_capital=args.capital,
        enable_long=True,
        enable_asset_regime_gate=args.enable_asset_regime_gate,
        enable_regime_flip_exit=True, enable_intra_position_governor=True,
        enable_carry=args.enable_carry_delta_neutral, carry_fraction=args.carry_size,
        enable_hedge=args.enable_hedge_governor_v1,
    )
    res = MultiLegBacktester(longs, cfg, carry_assets=CARRY_ASSETS).run(args.start, end)

    OUT.mkdir(parents=True, exist_ok=True)
    res.portfolio_ledger.to_parquet(OUT / "portfolio_ledger.parquet", index=False)
    res.leg_ledger.to_parquet(OUT / "leg_ledger.parquet", index=False)
    eq = res.equity
    state = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode, "paper_start": args.start, "data_end": end,
        "capital": args.capital, "carry_size": args.carry_size,
        "equity": float(eq.iloc[-1]) if len(eq) else args.capital,
        "ret_total": float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) else 0.0,
        "metrics": res.metrics, "pnl_by_type": res.pnl_by_type,
        "n_legs": int(len(res.leg_ledger)),
        "open_legs": int((res.leg_ledger["exit_time"].isna()).sum()) if len(res.leg_ledger) else 0,
    }
    (OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))
    print(f"[paper V1.1] {args.start}→{end}  equity={state['equity']:.0f} "
          f"({state['ret_total']*100:+.2f}%)  DD={res.metrics.get('max_drawdown',0)*100:.1f}%  "
          f"PnL{res.pnl_by_type}")
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--carry-size", type=float, default=0.50)
    ap.add_argument("--enable-carry-delta-neutral", action="store_true")
    ap.add_argument("--enable-asset-regime-gate", action="store_true")
    ap.add_argument("--enable-hedge-governor-v1", action="store_true")
    ap.add_argument("--mode", default="paper")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--loop-interval", type=int, default=0, help="secondes entre cycles (0=once)")
    args = ap.parse_args()

    while True:
        try:
            run_once(args)
        except Exception as e:
            logging.error("paper cycle échec: %s", e)
        if args.loop_interval <= 0:
            break
        time.sleep(args.loop_interval)


if __name__ == "__main__":
    main()
