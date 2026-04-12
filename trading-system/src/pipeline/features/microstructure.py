from __future__ import annotations

import pandas as pd


def compute_microstructure(df: pd.DataFrame) -> pd.DataFrame:
    """Compute microstructure fast features from book/price updates."""
    out = pd.DataFrame(index=df.index)
    bid = df.get("bid_px")
    ask = df.get("ask_px")
    if bid is not None and ask is not None:
        best_bid = bid.apply(lambda x: x[0] if isinstance(x, (list, tuple)) and x else None)
        best_ask = ask.apply(lambda x: x[0] if isinstance(x, (list, tuple)) and x else None)
        mid = (best_bid + best_ask) / 2
        spread = (best_ask - best_bid)
        out["mid_price"] = mid
        out["x_fast_spread"] = spread
        out["x_fast_spread_bps"] = (spread / mid.replace(0, pd.NA)) * 10_000
        depth_bid = df.get("bid_sz", pd.Series(dtype=float)).apply(lambda x: sum(x[:5]) if isinstance(x, (list, tuple)) else 0)
        depth_ask = df.get("ask_sz", pd.Series(dtype=float)).apply(lambda x: sum(x[:5]) if isinstance(x, (list, tuple)) else 0)
        out["x_fast_depth_usd"] = depth_bid + depth_ask
        out["x_fast_imbalance"] = (depth_bid - depth_ask) / (depth_bid + depth_ask + 1e-9)
    if "event_time" in df:
        out["x_fast_update_rate"] = df.index.to_series().diff().dt.total_seconds().rdiv(1).fillna(0)
    return out
