from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd
from pydantic import BaseModel

from common.logging.setup import get_logger

logger = get_logger(__name__)


class LabelingConfig(BaseModel):
    horizons_s: List[int]
    tp_bps: float
    sl_bps: float
    label_set: str = "v1"


@dataclass
class LabelRecord:
    t0: pd.Timestamp
    symbol: str
    horizon_s: int
    tp_bps: float
    sl_bps: float
    tp_hit: bool
    sl_hit: bool
    time_stop: bool
    barrier_hit: str
    return_fwd: float
    mfe: float
    mae: float
    duration_ms: int


class EventDrivenLabeler:
    def __init__(self, config: LabelingConfig):
        self.config = config

    def label(self, df: pd.DataFrame) -> pd.DataFrame:
        if "event_time" not in df.columns:
            raise ValueError("event_time column required")
        price_col = "mid_price" if "mid_price" in df.columns else "price"
        if price_col not in df.columns:
            raise ValueError("price or mid_price column required")
        df = df.sort_values("event_time").reset_index(drop=True)
        results: List[LabelRecord] = []
        tp_level = self.config.tp_bps / 10_000
        sl_level = self.config.sl_bps / 10_000
        times = pd.to_datetime(df["event_time"])
        prices = df[price_col].astype(float)
        for idx, (t0, p0) in enumerate(zip(times, prices)):
            for horizon in self.config.horizons_s:
                t_end = t0 + pd.Timedelta(seconds=horizon)
                mask = (times > t0) & (times <= t_end)
                window_prices = prices[mask]
                window_times = times[mask]
                if window_prices.empty:
                    results.append(
                        LabelRecord(
                            t0=t0,
                            symbol=str(df.iloc[idx].get("symbol", "")),
                            horizon_s=horizon,
                            tp_bps=self.config.tp_bps,
                            sl_bps=self.config.sl_bps,
                            tp_hit=False,
                            sl_hit=False,
                            time_stop=True,
                            barrier_hit="time",
                            return_fwd=0.0,
                            mfe=0.0,
                            mae=0.0,
                            duration_ms=horizon * 1000,
                        )
                    )
                    continue
                rel = (window_prices / p0) - 1.0
                tp_hit_idx = np.argmax(rel >= tp_level) if (rel >= tp_level).any() else -1
                sl_hit_idx = np.argmax(rel <= -sl_level) if (rel <= -sl_level).any() else -1
                barrier_hit = "time"
                tp_hit = False
                sl_hit = False
                hit_idx = None
                if tp_hit_idx >= 0 and sl_hit_idx >= 0:
                    hit_idx = min(tp_hit_idx, sl_hit_idx)
                    tp_hit = tp_hit_idx == hit_idx
                    sl_hit = sl_hit_idx == hit_idx
                elif tp_hit_idx >= 0:
                    hit_idx = tp_hit_idx
                    tp_hit = True
                    barrier_hit = "tp"
                elif sl_hit_idx >= 0:
                    hit_idx = sl_hit_idx
                    sl_hit = True
                    barrier_hit = "sl"
                else:
                    hit_idx = len(rel) - 1
                    barrier_hit = "time"
                ret = float(rel.iloc[hit_idx])
                mfe = float(rel.max())
                mae = float(rel.min())
                duration_ms = int((window_times.iloc[hit_idx] - t0).total_seconds() * 1000)
                if barrier_hit == "time" and not (tp_hit or sl_hit):
                    barrier_hit = "time"
                results.append(
                    LabelRecord(
                        t0=t0,
                        symbol=str(df.iloc[idx].get("symbol", "")),
                        horizon_s=horizon,
                        tp_bps=self.config.tp_bps,
                        sl_bps=self.config.sl_bps,
                        tp_hit=tp_hit,
                        sl_hit=sl_hit,
                        time_stop=not (tp_hit or sl_hit),
                        barrier_hit=barrier_hit,
                        return_fwd=ret,
                        mfe=mfe,
                        mae=mae,
                        duration_ms=duration_ms,
                    )
                )
        out = pd.DataFrame([r.__dict__ for r in results])
        out["label_set"] = self.config.label_set
        logger.info({"msg": "generated labels", "rows": len(out)})
        return out
