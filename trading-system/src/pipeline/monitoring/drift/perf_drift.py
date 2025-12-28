from __future__ import annotations

import pandas as pd

from domain.monitoring.drift import PerformanceDriftReport


class PerformanceDriftDetector:
    def __init__(self, config: dict):
        self.config = config

    def compute(self, pnl_window: pd.DataFrame, fills_window: pd.DataFrame, costs_window: pd.DataFrame, baseline: pd.DataFrame) -> PerformanceDriftReport:
        by_symbol = {}
        pnl_by_sym = pnl_window.groupby("symbol")["pnl_usd"].sum() if "pnl_usd" in pnl_window else {}
        slippage = costs_window.explode("by_order")
        for symbol, pnl_val in pnl_by_sym.items():
            slip = slippage[slippage.get("symbol") == symbol]["realized_slippage_bps"].mean() if isinstance(slippage, pd.DataFrame) and not slippage.empty else 0.0
            by_symbol[str(symbol)] = {"pnl_usd": float(pnl_val), "slippage_bps": float(slip), "severity": "OK"}
        return PerformanceDriftReport(window=self.config.get("window", ""), by_symbol=by_symbol, severity="OK")
