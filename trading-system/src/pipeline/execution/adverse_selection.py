from __future__ import annotations

import numpy as np
import pandas as pd


class AdverseSelectionDetector:
    def score(self, symbol_state: dict, microstructure_state: pd.Series, recent_returns: pd.Series | None = None) -> float:
        spread = float(microstructure_state.get("x_fast_spread_bps", 0) or 0)
        rv = float(microstructure_state.get("x_mid_rv_5m", 0) or 0)
        ret = float(recent_returns.iloc[-1]) if recent_returns is not None and not recent_returns.empty else 0.0
        score = spread / 10 + rv * 100 + abs(ret) * 10
        return float(np.clip(score, 0.0, 10.0))
