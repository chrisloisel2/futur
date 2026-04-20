from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import pandas as pd

from domain.orders.fills import ExecutedFills, ExecutionCostsSnapshot, Fill
from domain.orders.order_plan import OrdersPlan, OrderIntent
from domain.state.execution_state import ExecutionHealth, ExecutionState, OpenOrderState, SymbolExecutionState
from pipeline.execution.adverse_selection import AdverseSelectionDetector
from pipeline.execution.fill_model import FillModel
from pipeline.execution.maker import MakerExecutor
from pipeline.execution.order_events import OrderEvent, OrderEvents
from pipeline.execution.slippage import SlippageModel
from pipeline.execution.taker import TakerExecutor
from pipeline.execution.telemetry import ExecutionTelemetry


@dataclass
class ExecutionEngineConfig:
    exchange: str = "binance"


class ExecutionEngine:
    def __init__(self, config: Dict):
        self.config = ExecutionEngineConfig(**config.get("engine", {}))
        self.maker = MakerExecutor(config.get("maker", {}))
        self.taker = TakerExecutor(config.get("taker", {}))
        self.fill_model = FillModel()
        self.slippage = SlippageModel()
        self.adverse = AdverseSelectionDetector()
        self.telemetry = ExecutionTelemetry()

    def step(self, orders_plan: OrdersPlan, portfolio_state: Dict, state_by_symbol: Dict[str, pd.Series]) -> Tuple[ExecutedFills, ExecutionState, OrderEvents, dict]:
        events = []
        fills = []
        costs_by_order = []
        symbol_states = {}
        open_orders = {}
        for intent in orders_plan.orders:
            ref_state = state_by_symbol.get(intent.symbol, pd.Series())
            mode = intent.directive.mode if hasattr(intent.directive, 'mode') else "TAKER"
            symbol_states[intent.symbol] = SymbolExecutionState(symbol=intent.symbol, mode=mode)
            if mode == "MAKER" or intent.order_type.name == "POST_ONLY":
                quote = self.maker.apply_quotes(intent.symbol, intent.qty, ref_state, intent.directive)
                events.append(OrderEvent(event_time=pd.Timestamp.utcnow(), event_type="SEND", symbol=intent.symbol, client_order_id=f"M_{intent.symbol}", exchange_order_id=None, details={"quote": quote.__dict__}))
            else:
                # TAKER execution with realistic fill model
                mid_price = float(ref_state.get("mid_price", intent.price or 0) or 0)
                spread_bps = float(ref_state.get("x_fast_spread_bps", 2.0))
                depth_usd = float(ref_state.get("x_fast_depth_usd", 100_000))
                volatility = float(ref_state.get("x_mid_rv_5m", 0.001))

                execs = self.taker.execute(intent, mid_price)
                for ex in execs:
                    events.append(OrderEvent(
                        event_time=pd.Timestamp.utcnow(),
                        event_type="SEND",
                        symbol=intent.symbol,
                        client_order_id=f"T_{intent.symbol}",
                        exchange_order_id=None,
                        details=ex
                    ))

                    # CRITICAL FIX: Realistic fill price calculation
                    # Fill at ask (for buy) or bid (for sell), not mid!
                    side = str(intent.side.value if hasattr(intent.side, 'value') else intent.side).upper()
                    half_spread = mid_price * (spread_bps / 10_000) / 2

                    if side in ["BUY", "LONG"]:
                        # Buy at ask price
                        fill_price_before_slippage = mid_price + half_spread
                    else:
                        # Sell at bid price
                        fill_price_before_slippage = mid_price - half_spread

                    # Add slippage based on order size relative to depth
                    notional = fill_price_before_slippage * ex["qty"]
                    slippage_bps = self.slippage.expected_slippage_bps(depth_usd, notional, volatility)
                    slippage_price_impact = fill_price_before_slippage * (slippage_bps / 10_000)

                    if side in ["BUY", "LONG"]:
                        fill_price = fill_price_before_slippage + slippage_price_impact
                    else:
                        fill_price = fill_price_before_slippage - slippage_price_impact

                    # Realistic fees: Binance VIP 0 taker = 0.10% (10 bps)
                    fee_bps = 10.0
                    fee_usd = notional * (fee_bps / 10_000)

                    fills.append(Fill(
                        symbol=intent.symbol,
                        side=side,
                        qty=ex["qty"],
                        price=fill_price,
                        fee_usd=fee_usd,
                        exchange=self.config.exchange,
                        order_id="sim",
                        client_order_id=f"T_{intent.symbol}",
                        event_time=pd.Timestamp.utcnow(),
                        liquidity="TAKER"
                    ))

                    realized_slippage_bps = self.slippage.realized_slippage_bps(fill_price, mid_price)

                    costs_by_order.append({
                        "client_order_id": f"T_{intent.symbol}",
                        "symbol": intent.symbol,
                        "liquidity": "TAKER",
                        "fee_usd": fee_usd,
                        "realized_slippage_bps": realized_slippage_bps,
                        "expected_slippage_bps": slippage_bps,
                        "latency_ms": 100,  # TODO: make dynamic
                    })
        exec_state = ExecutionState(
            event_time=pd.Timestamp.utcnow(),
            run_id=orders_plan.run_id,
            exchange=self.config.exchange,
            symbol_states=symbol_states,
            open_orders=open_orders,
            health=ExecutionHealth(exchange_connected=True, ws_connected=True, last_ping_time=pd.Timestamp.utcnow(), last_error=None, outage_seconds=0),
        )
        executed = ExecutedFills(event_time=pd.Timestamp.utcnow(), fills=fills, aggregate_costs=ExecutionCostsSnapshot(by_order=costs_by_order, aggregate={"fees_usd": sum(c.get("fee_usd", 0) for c in costs_by_order)}))
        order_events = OrderEvents(event_time=pd.Timestamp.utcnow(), events=events)
        costs = executed.aggregate_costs.__dict__
        return executed, exec_state, order_events, costs
