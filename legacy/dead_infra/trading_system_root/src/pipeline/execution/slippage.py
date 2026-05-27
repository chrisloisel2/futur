from __future__ import annotations

import numpy as np


class SlippageModel:
    def expected_slippage_bps(self, depth_usd: float, order_notional_usd: float, rv: float) -> float:
        if depth_usd <= 0:
            return float('inf')
        impact = order_notional_usd / depth_usd
        return float((impact + rv) * 10_000)

    def realized_slippage_bps(self, fill_price: float, ref_mid: float) -> float:
        if ref_mid == 0:
            return 0.0
        return float((fill_price / ref_mid - 1) * 10_000)
