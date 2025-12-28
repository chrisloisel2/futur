from __future__ import annotations

from typing import Dict, List

import pandas as pd

from domain.orders.order_plan import OrderIntent, OrdersPlan, StopIntent, TimeStopIntent
from domain.state.targets import TargetPosition


class OrdersPlanBuilder:
    def build(self, targets: List[TargetPosition], portfolio_positions: Dict[str, dict], run_id: str, risk_state_ref: str) -> OrdersPlan:
        orders: List[OrderIntent] = []
        stops: List[StopIntent] = []
        time_stops: List[TimeStopIntent] = []
        for t in targets:
            current = portfolio_positions.get(t.symbol, {})
            current_notional = float(current.get("notional_usd", 0)) * (1 if current.get("side", "LONG") == "LONG" else -1)
            target_notional = t.notional_usd if t.side == "LONG" else -t.notional_usd
            delta = target_notional - current_notional
            qty = abs(delta) / max(t.risk_hints.get("price", 1.0), 1.0) if t.risk_hints else abs(delta)
            if qty == 0:
                continue
            side = "BUY" if delta > 0 else "SELL"
            orders.append(OrderIntent(symbol=t.symbol, order_type="LIMIT_TO_MARKET", side=side, qty=qty, price=None, reduce_only=False, time_in_force="IOC", book=t.book, risk_tags=["var_cap_ok", "scenario_passed"]))
            if "sl" in t.risk_hints:
                stops.append(StopIntent(symbol=t.symbol, side="SELL" if side == "BUY" else "BUY", stop_type="STOP_MARKET", stop_price=t.risk_hints.get("sl"), reduce_only=True, book=t.book, risk_tags=["sl_hint"]))
            if "time_stop_s" in t.risk_hints:
                time_stops.append(TimeStopIntent(symbol=t.symbol, close_after_seconds=int(t.risk_hints.get("time_stop_s", 0)), reduce_only=True, book=t.book, risk_tags=["time_stop_hint"]))
        return OrdersPlan(event_time=pd.Timestamp.utcnow(), run_id=run_id, orders=orders, stops=stops, time_stops=time_stops, risk_state_ref=risk_state_ref)
