from __future__ import annotations

from domain.signal.signal import TradeMode


def choose_mode(spread_bps: float, depth_usd: float, thresholds: dict) -> TradeMode:
    max_spread = thresholds.get("max_spread_bps", 200.0)
    min_depth = thresholds.get("min_depth_usd", 10_000)
    if spread_bps > max_spread or depth_usd < min_depth:
        return TradeMode.OFF
    if spread_bps < max_spread / 2 and depth_usd > min_depth * 2:
        return TradeMode.MAKER
    return TradeMode.TAKER
