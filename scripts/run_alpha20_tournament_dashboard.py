#!/usr/bin/env python3
"""scripts/run_alpha20_tournament_dashboard.py — dashboard quotidien
machine-readable par runner. Voir src.alpha20.tournament.dashboard."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.alpha20.guard import assert_paper_only
from src.alpha20.tournament.dashboard import build_dashboard, write_daily

if __name__ == "__main__":
    assert_paper_only()
    rows = build_dashboard()
    path = write_daily(rows)
    for r in rows:
        print(f"{r['runner_id']}: nav={r['nav_usdt']} "
              f"net_after_tax={r['pnl_net_after_tax_usdt']} "
              f"dd={r['max_drawdown']:.2%} "
              f"reconciliation={r['reconciliation']['status']}")
    print(f"-> {path}")
