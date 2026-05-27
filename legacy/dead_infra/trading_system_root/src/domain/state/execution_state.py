from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class ExecutionHealth:
    exchange_connected: bool
    ws_connected: bool
    last_ping_time: object | None
    last_error: str | None
    outage_seconds: int = 0


@dataclass
class SymbolExecutionState:
    symbol: str
    mode: str
    last_quote_time: object | None = None
    last_cancel_time: object | None = None
    last_fill_time: object | None = None
    adverse_score: float = 0.0
    latency_ms_ewma: float = 0.0
    slippage_bps_ewma: float = 0.0
    rate_limit_tokens: float = 0.0


@dataclass
class OpenOrderState:
    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    side: str
    qty: float
    price: float | None
    status: str
    created_time: object
    last_update_time: object
    reduce_only: bool = False
    post_only: bool = False
    book: str = ""
    risk_tags: list[str] = field(default_factory=list)


@dataclass
class ExecutionState:
    event_time: object
    run_id: str
    exchange: str
    symbol_states: Dict[str, SymbolExecutionState]
    open_orders: Dict[str, OpenOrderState]
    health: ExecutionHealth
