from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class LeverageState:
    label: str
    directional_pressure: int
    forced_flow_risk: float


def classify_leverage_state(price_change: float, oi_change: float) -> str:
    if price_change > 0 and oi_change > 0:
        return "NEW_LONG_LEVERAGE"
    if price_change > 0 and oi_change < 0:
        return "SHORT_SQUEEZE_DELEVERAGING"
    if price_change < 0 and oi_change > 0:
        return "NEW_SHORT_LEVERAGE"
    if price_change < 0 and oi_change < 0:
        return "LONG_LIQUIDATION_DELEVERAGING"
    return "NEUTRAL"


def leverage_tensor(price_change: float, oi_change: float, funding: float, funding_expected: float, basis_bps: float, basis_velocity_bps: float, long_liquidation_usd: float, short_liquidation_usd: float, visible_depth_usd: float) -> Dict[str, float]:
    depth = max(float(visible_depth_usd), 1e-9)
    liquidation_net = float(long_liquidation_usd) - float(short_liquidation_usd)
    return {
        "price_change": float(price_change),
        "oi_change": float(oi_change),
        "funding": float(funding),
        "funding_surprise": float(funding) - float(funding_expected),
        "basis_bps": float(basis_bps),
        "basis_velocity_bps": float(basis_velocity_bps),
        "long_liquidation_usd": float(long_liquidation_usd),
        "short_liquidation_usd": float(short_liquidation_usd),
        "liquidation_net_usd": liquidation_net,
        "liquidation_depth_ratio": (float(long_liquidation_usd) + float(short_liquidation_usd)) / depth,
        "state": classify_leverage_state(float(price_change), float(oi_change)),
    }
