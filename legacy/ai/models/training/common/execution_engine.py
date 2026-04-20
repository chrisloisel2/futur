"""
Execution Engine: Order routing with impulse-aware execution.

Pipeline integration:
    Regime → Edge → MetaControl → ExecutionEngine → Exchange

ExecutionEngine responsibilities:
- Order type selection (MAKER vs TAKER)
- Impulse-aware routing (MAKER→TAKER during impulse)
- Cancel/repost logic during impulse
- Latency-aware limits

CRITICAL: During impulse, switch to aggressive execution to avoid adverse selection.
"""

from __future__ import annotations
from typing import Dict, Any, Optional, Literal
from dataclasses import dataclass
from enum import Enum
import logging
import pandas as pd

logger = logging.getLogger(__name__)


class OrderType(Enum):
    """Order types."""
    LIMIT_MAKER = "LIMIT_MAKER"  # Passive, maker fees
    LIMIT = "LIMIT"  # Can be maker or taker
    MARKET = "MARKET"  # Aggressive, taker fees


class OrderSide(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class ExecutionConfig:
    """Configuration for execution engine."""
    # Normal execution (no impulse)
    default_order_type: OrderType = OrderType.LIMIT_MAKER
    default_limit_offset_bps: float = 2.0  # 2bps from mid for LIMIT_MAKER

    # Impulse execution
    impulse_order_type: OrderType = OrderType.MARKET  # Aggressive during impulse
    impulse_cancel_open_orders: bool = True  # Cancel pending orders during impulse
    impulse_max_slippage_bps: float = 20.0  # Max acceptable slippage during impulse

    # Safety
    min_order_size: float = 0.001  # Min size in base currency
    max_order_size: float = 100.0  # Max size in base currency


@dataclass
class Order:
    """Order representation."""
    symbol: str
    side: OrderSide
    order_type: OrderType
    size: float
    price: Optional[float] = None  # For LIMIT orders
    reason: str = ""
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ExecutionResult:
    """Result of order submission."""
    success: bool
    order: Order
    order_id: Optional[str] = None
    filled_size: float = 0.0
    avg_fill_price: Optional[float] = None
    execution_cost_bps: Optional[float] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ExecutionEngine:
    """
    Execution engine with impulse-aware routing.

    During impulse:
    - Switch MAKER → TAKER
    - Cancel all pending orders
    - Use aggressive execution to avoid adverse selection
    """

    def __init__(
        self,
        config: Optional[ExecutionConfig] = None,
    ):
        """
        Args:
            config: ExecutionConfig instance (uses default if None)
        """
        self.config = config if config is not None else ExecutionConfig()

        # State
        self.open_orders: Dict[str, Order] = {}  # order_id → Order

    def place_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        size: float,
        regime: str,
        impulse_active: bool,
        impulse_score: float,
        mid_price: float,
    ) -> Order:
        """
        Create order with impulse-aware routing.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            side: Order side ("BUY" or "SELL")
            size: Order size in base currency
            regime: Current regime ('calm' or 'reversal')
            impulse_active: Binary impulse flag
            impulse_score: Impulse score ∈ [0, 1]
            mid_price: Current mid price (for LIMIT orders)

        Returns:
            Order object ready for submission
        """
        # Validate size
        if size < self.config.min_order_size:
            logger.warning(f"Order size {size} below min {self.config.min_order_size}, skipping")
            return None

        if size > self.config.max_order_size:
            logger.warning(f"Order size {size} above max {self.config.max_order_size}, capping")
            size = self.config.max_order_size

        # Convert side to enum
        order_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL

        # Impulse-aware order type selection
        if impulse_active:
            # During impulse: aggressive execution
            order_type = self.config.impulse_order_type
            price = None  # MARKET orders have no price

            # Cancel all pending orders during impulse
            if self.config.impulse_cancel_open_orders:
                self.cancel_all_open_orders()

            reason = f"regime={regime}, impulse=ACTIVE (score={impulse_score:.2f})"

        else:
            # Normal execution: passive
            order_type = self.config.default_order_type

            # Compute limit price
            offset_bps = self.config.default_limit_offset_bps
            if order_side == OrderSide.BUY:
                # Buy: limit below mid
                price = mid_price * (1 - offset_bps / 10000)
            else:
                # Sell: limit above mid
                price = mid_price * (1 + offset_bps / 10000)

            reason = f"regime={regime}, impulse=inactive (score={impulse_score:.2f})"

        order = Order(
            symbol=symbol,
            side=order_side,
            order_type=order_type,
            size=size,
            price=price,
            reason=reason,
            metadata={
                'regime': regime,
                'impulse_active': impulse_active,
                'impulse_score': impulse_score,
                'mid_price': mid_price,
            },
        )

        return order

    def submit_order(self, order: Order) -> ExecutionResult:
        """
        Submit order to exchange (MOCK for now).

        In production, this would:
        - Call exchange API (Binance, OKX, etc.)
        - Handle responses, retries, errors
        - Track order state

        Args:
            order: Order to submit

        Returns:
            ExecutionResult with fill information
        """
        # MOCK: Simulate immediate fill
        logger.info(f"SUBMITTING ORDER: {order.side.value} {order.size} {order.symbol} @ {order.order_type.value}")
        logger.info(f"  Reason: {order.reason}")

        # Generate mock order ID
        import uuid
        order_id = str(uuid.uuid4())[:8]

        # Mock fill (100% filled immediately)
        if order.order_type == OrderType.MARKET:
            # Market order: filled at "market price" (mock)
            avg_fill_price = order.metadata['mid_price']
            execution_cost_bps = 10.0  # Mock: 10bps taker fee
        else:
            # Limit order: filled at limit price (mock)
            avg_fill_price = order.price
            execution_cost_bps = -2.0  # Mock: -2bps maker rebate

        result = ExecutionResult(
            success=True,
            order=order,
            order_id=order_id,
            filled_size=order.size,
            avg_fill_price=avg_fill_price,
            execution_cost_bps=execution_cost_bps,
            error=None,
            metadata={
                'order_type': order.order_type.value,
                'regime': order.metadata['regime'],
                'impulse_active': order.metadata['impulse_active'],
            },
        )

        logger.info(f"  FILLED: {result.filled_size} @ {result.avg_fill_price:.2f}, cost={result.execution_cost_bps:.1f}bps")

        # Track open order (if LIMIT)
        if order.order_type in [OrderType.LIMIT, OrderType.LIMIT_MAKER]:
            self.open_orders[order_id] = order

        return result

    def cancel_all_open_orders(self):
        """
        Cancel all open orders.

        Called during impulse to avoid adverse selection.
        """
        if not self.open_orders:
            return

        logger.warning(f"CANCELLING {len(self.open_orders)} OPEN ORDERS (impulse active)")

        # MOCK: Simulate cancel
        for order_id, order in list(self.open_orders.items()):
            logger.info(f"  Cancelled order {order_id}: {order.side.value} {order.size} {order.symbol}")
            del self.open_orders[order_id]

    def __repr__(self) -> str:
        return f"ExecutionEngine(open_orders={len(self.open_orders)})"


# Example usage
if __name__ == "__main__":
    from meta_control import MetaControl, MetaControlConfig
    from impulse_detector import ImpulseDetector

    # Setup
    execution_engine = ExecutionEngine()
    meta_control = MetaControl()
    impulse_detector = ImpulseDetector(threshold=0.7)

    # Example 1: Normal execution (no impulse)
    print("=== Example 1: Normal execution ===")
    meta_output = meta_control.compute_position_size(
        timestamp=pd.Timestamp.now(),
        base_size=0.5,
        regime='calm',
        impulse_score=0.15,
        is_impulse=False,
        recent_pnl=0.001,
    )

    order = execution_engine.place_order(
        symbol="BTCUSDT",
        side="BUY",
        size=meta_output.position_size,
        regime=meta_output.regime,
        impulse_active=meta_output.impulse_active,
        impulse_score=meta_output.impulse_score,
        mid_price=50000.0,
    )

    result = execution_engine.submit_order(order)
    print(f"Result: {result.success}, cost={result.execution_cost_bps:.1f}bps")
    print()

    # Example 2: Impulse execution
    print("=== Example 2: Impulse execution ===")
    meta_output = meta_control.compute_position_size(
        timestamp=pd.Timestamp.now(),
        base_size=0.5,
        regime='reversal',
        impulse_score=0.88,
        is_impulse=True,
        recent_pnl=0.001,
    )

    order = execution_engine.place_order(
        symbol="BTCUSDT",
        side="SELL",
        size=meta_output.position_size,
        regime=meta_output.regime,
        impulse_active=meta_output.impulse_active,
        impulse_score=meta_output.impulse_score,
        mid_price=50000.0,
    )

    result = execution_engine.submit_order(order)
    print(f"Result: {result.success}, cost={result.execution_cost_bps:.1f}bps")
    print(f"Order type: {order.order_type.value} (MARKET during impulse)")
