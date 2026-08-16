from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import math


@dataclass(frozen=True)
class FeeSchedule:
    maker_bps: float
    taker_bps: float


@dataclass(frozen=True)
class MarketSnapshot:
    ts_ns: int
    mid: float
    bid: float
    ask: float
    bid_depth_usd: float
    ask_depth_usd: float
    sigma_bps: float
    adv_usd: float

    def __post_init__(self) -> None:
        if self.mid <= 0 or self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("invalid market snapshot")
        if self.bid_depth_usd < 0 or self.ask_depth_usd < 0 or self.adv_usd <= 0:
            raise ValueError("invalid liquidity snapshot")


@dataclass(frozen=True)
class OrderIntent:
    side: str
    notional_usd: float
    alpha_bps: float
    style: str
    limit_price: Optional[float] = None

    def __post_init__(self) -> None:
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be buy/sell")
        if self.style not in {"maker", "taker"}:
            raise ValueError("style must be maker/taker")
        if self.notional_usd <= 0:
            raise ValueError("notional must be positive")


@dataclass(frozen=True)
class LatencyModel:
    decision_to_send_ms: float
    send_to_ack_ms: float
    market_data_age_ms: float

    @property
    def total_ms(self) -> float:
        return float(self.decision_to_send_ms + self.send_to_ack_ms + self.market_data_age_ms)


@dataclass(frozen=True)
class ExecutionResult:
    filled: bool
    fill_probability: float
    fill_price: float
    gross_edge_bps: float
    fees_bps: float
    slippage_bps: float
    impact_bps: float
    latency_penalty_bps: float
    adverse_selection_bps: float
    net_edge_bps: float
    model_confidence: str


def square_root_impact_bps(notional_usd: float, adv_usd: float, sigma_bps: float, eta: float = 0.50) -> float:
    participation = max(0.0, float(notional_usd) / max(float(adv_usd), 1e-12))
    return float(max(0.0, eta * abs(float(sigma_bps)) * math.sqrt(participation)))


def taker_execution(order: OrderIntent, decision: MarketSnapshot, post_latency_mid: float, fees: FeeSchedule, latency: LatencyModel, eta: float = 0.50) -> ExecutionResult:
    if order.style != "taker":
        raise ValueError("taker_execution requires taker order")
    side_sign = 1.0 if order.side == "buy" else -1.0
    touch = decision.ask if order.side == "buy" else decision.bid
    spread_slippage = side_sign * (touch - decision.mid) / decision.mid * 1e4
    impact = square_root_impact_bps(order.notional_usd, decision.adv_usd, decision.sigma_bps, eta=eta)
    latency_penalty = max(0.0, side_sign * (float(post_latency_mid) - decision.mid) / decision.mid * 1e4)
    fill_price = touch * (1.0 + side_sign * impact / 1e4)
    fees_bps = float(fees.taker_bps)
    net = float(order.alpha_bps - abs(spread_slippage) - impact - latency_penalty - fees_bps)
    return ExecutionResult(True, 1.0, float(fill_price), float(order.alpha_bps), fees_bps, abs(float(spread_slippage)), impact, latency_penalty, 0.0, net, "observed_touch+impact")


def passive_execution(order: OrderIntent, decision: MarketSnapshot, queue_ahead_usd: float, traded_at_price_usd: float, canceled_ahead_usd: float, future_mid_after_fill: float, fees: FeeSchedule, cancel_credit: float = 0.25, queue_model_confidence: str = "L2_CONSERVATIVE") -> ExecutionResult:
    if order.style != "maker":
        raise ValueError("passive_execution requires maker order")
    if queue_ahead_usd < 0 or traded_at_price_usd < 0 or canceled_ahead_usd < 0:
        raise ValueError("queue quantities must be non-negative")
    side_sign = 1.0 if order.side == "buy" else -1.0
    limit = order.limit_price if order.limit_price is not None else (decision.bid if order.side == "buy" else decision.ask)
    effective_progress = float(traded_at_price_usd) + max(0.0, min(1.0, float(cancel_credit))) * float(canceled_ahead_usd)
    required = float(queue_ahead_usd) + float(order.notional_usd)
    fill_probability = min(1.0, effective_progress / max(required, 1e-12))
    filled = effective_progress >= required
    spread_capture = abs(float(limit - decision.mid) / decision.mid * 1e4)
    adverse = 0.0
    if filled and future_mid_after_fill > 0:
        adverse = max(0.0, -side_sign * (float(future_mid_after_fill) - float(limit)) / float(limit) * 1e4)
    fees_bps = float(fees.maker_bps)
    net_if_fill = float(order.alpha_bps + spread_capture - fees_bps - adverse)
    expected_net = float(fill_probability * net_if_fill)
    return ExecutionResult(filled, float(fill_probability), float(limit), float(order.alpha_bps + spread_capture), fees_bps, 0.0, 0.0, 0.0, float(adverse), expected_net, str(queue_model_confidence))
