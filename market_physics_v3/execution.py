from __future__ import annotations

from typing import Dict, Mapping

from .schema import ExecutionTrace


def _side_sign(side: str) -> float:
    return 1.0 if side == "buy" else -1.0


def execution_metrics(trace: ExecutionTrace, future_mid_by_horizon_ms: Mapping[int, float]) -> Dict[str, float]:
    sign = _side_sign(trace.side)
    fill_ratio = trace.filled_qty / trace.requested_qty
    slippage_bps = sign * 1e4 * (trace.avg_fill_price - trace.decision_mid) / trace.decision_mid
    fee_bps = 1e4 * trace.fee_quote / max(trace.avg_fill_price * trace.filled_qty, 1e-12) if trace.filled_qty > 0 else 0.0
    result = {
        "decision_to_send_ms": (trace.send_ts_ns - trace.decision_ts_ns) / 1e6,
        "send_to_ack_ms": (trace.ack_ts_ns - trace.send_ts_ns) / 1e6,
        "ack_to_first_fill_ms": (trace.first_fill_ts_ns - trace.ack_ts_ns) / 1e6,
        "time_to_fill_ms": (trace.last_fill_ts_ns - trace.first_fill_ts_ns) / 1e6,
        "fill_ratio": float(fill_ratio),
        "slippage_bps": float(slippage_bps),
        "fee_bps": float(fee_bps),
        "implementation_shortfall_bps": float(slippage_bps + fee_bps),
    }
    for horizon_ms, mid in sorted(future_mid_by_horizon_ms.items()):
        if mid <= 0:
            continue
        result["markout_%dms_bps" % horizon_ms] = float(sign * 1e4 * (mid - trace.avg_fill_price) / trace.avg_fill_price)
    return result
