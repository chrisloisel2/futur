from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd
from pydantic import BaseModel

from common.logging.setup import get_logger

logger = get_logger(__name__)


class CostModelConfig(BaseModel):
    """
    Realistic cost model based on Binance spot/futures tiers.

    Defaults assume:
    - VIP 0 (retail): 10 bps taker, 2 bps maker
    - BTC/ETH major pairs: tight spreads (1-2 bps)
    - Altcoins: wider spreads (5-10 bps)
    - Slippage depends on order size vs book depth
    """
    # Binance VIP 0 tiers (bps)
    fee_taker_bps: float = 10.0  # 0.10% taker
    fee_maker_bps: float = 2.0   # 0.02% maker (rebate in reality but kept as cost)

    # Spread costs (half-spread crossing for taker)
    spread_bps_btc: float = 1.0   # BTC tight spread
    spread_bps_eth: float = 2.0   # ETH medium spread
    spread_bps_alts: float = 8.0  # Altcoins wide spread

    # Slippage: function of order size / depth
    slippage_base_bps: float = 2.0     # Base slippage
    slippage_multiplier: float = 1.5   # Scales with (notional / depth)

    # Market impact (non-linear with size)
    impact_base_bps: float = 0.5
    impact_exponent: float = 1.2  # Power law: impact ~ size^1.2

    # Adverse selection (informed traders moving market against you)
    adverse_bps: float = 0.5


@dataclass
class ExecutionCosts:
    fees: float
    spread_cost: float
    slippage_cost: float
    impact_cost: float
    adverse_selection_cost: float
    net_return: float


class CostModel:
    def __init__(self, config: CostModelConfig):
        self.config = config

    def _get_spread_bps(self, symbol: str) -> float:
        """Get spread cost based on symbol type."""
        symbol_upper = symbol.upper()
        if "BTC" in symbol_upper:
            return self.config.spread_bps_btc
        elif "ETH" in symbol_upper:
            return self.config.spread_bps_eth
        else:
            return self.config.spread_bps_alts

    def _compute_slippage(self, notional: float, depth_usd: float = 100_000) -> float:
        """
        Compute slippage based on order size relative to book depth.

        slippage_bps = base + multiplier * (notional / depth)
        """
        ratio = notional / max(depth_usd, 1.0)
        slippage_bps = self.config.slippage_base_bps + self.config.slippage_multiplier * ratio * 10_000
        return min(slippage_bps, 100.0)  # Cap at 1% (100 bps)

    def _compute_impact(self, notional: float, avg_daily_volume: float = 1_000_000) -> float:
        """
        Compute market impact using power law.

        impact_bps = base * (notional / avg_volume)^exponent
        """
        ratio = notional / max(avg_daily_volume, 1.0)
        impact_bps = self.config.impact_base_bps * (ratio ** self.config.impact_exponent) * 10_000
        return min(impact_bps, 50.0)  # Cap at 0.5% (50 bps)

    def apply(self, trades: pd.DataFrame, mode: str = "taker") -> pd.DataFrame:
        """
        Apply realistic cost model to trades.

        Args:
            trades: DataFrame with columns: symbol, entry_px, qty, gross_pnl
            mode: "taker" or "maker"

        Returns:
            DataFrame with added cost columns and net_pnl
        """
        df = trades.copy()

        # Fee based on mode
        if mode == "maker":
            fee_bps = self.config.fee_maker_bps
        else:
            fee_bps = self.config.fee_taker_bps

        notionals = (df["entry_px"].astype(float) * df["qty"].abs())

        # Fixed fees
        df["fees"] = notionals * fee_bps / 10_000

        # Spread cost (only for taker)
        if mode == "taker":
            df["spread_cost"] = notionals * df.get("symbol", "ALTUSDT").apply(self._get_spread_bps) / 10_000
        else:
            df["spread_cost"] = 0.0

        # Dynamic slippage based on depth (use defaults if not available)
        depth_col = df.get("book_depth_usd", 100_000)
        df["slippage"] = notionals.combine(depth_col, self._compute_slippage) / 10_000

        # Dynamic impact based on volume
        volume_col = df.get("daily_volume_usd", 1_000_000)
        df["impact"] = notionals.combine(volume_col, self._compute_impact) / 10_000

        # Adverse selection (fixed for now)
        df["adverse_selection"] = notionals * self.config.adverse_bps / 10_000

        # Total costs
        df["total_costs"] = (
            df["fees"]
            + df["slippage"]
            + df["impact"]
            + df["adverse_selection"]
            + df["spread_cost"]
        )

        # Net PnL after costs
        df["net_pnl"] = df["gross_pnl"] - df["total_costs"]

        logger.info({
            "msg": "applied cost model",
            "trades": len(df),
            "mode": mode,
            "avg_fee_bps": (df["fees"].sum() / notionals.sum() * 10_000) if notionals.sum() > 0 else 0,
            "avg_total_cost_bps": (df["total_costs"].sum() / notionals.sum() * 10_000) if notionals.sum() > 0 else 0,
        })
        return df

    def summarize(self, trades: pd.DataFrame) -> ExecutionCosts:
        return ExecutionCosts(
            fees=float(trades.get("fees", 0).sum()),
            spread_cost=float(trades.get("spread_cost", 0).sum()),
            slippage_cost=float(trades.get("slippage", 0).sum()),
            impact_cost=float(trades.get("impact", 0).sum()),
            adverse_selection_cost=float(trades.get("adverse_selection", 0).sum()),
            net_return=float(trades.get("net_pnl", 0).sum()),
        )
