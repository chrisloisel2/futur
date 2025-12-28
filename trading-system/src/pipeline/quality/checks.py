from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from domain.state.quality import QualityDecision, QualityFlag
from pipeline.quality.anomalies import detect_spikes, robust_zscore


@dataclass
class BaseCheck:
    name: str
    critical: bool
    flags: List[QualityFlag]

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def _set_flag(self, df: pd.DataFrame, mask: pd.Series, flag: QualityFlag) -> pd.DataFrame:
        df.loc[mask, "quality_flags"] = df.loc[mask, "quality_flags"].astype(int) | int(flag)
        return df


@dataclass
class MissingnessCheck(BaseCheck):
    required_fields: List[str]

    def __init__(self, required_fields: List[str]):
        super().__init__(name="missingness", critical=True, flags=[QualityFlag.MISSING_FIELDS])
        self.required_fields = required_fields

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        mask = df[self.required_fields].isna().any(axis=1)
        df["check_missing"] = mask
        return self._set_flag(df, mask, QualityFlag.MISSING_FIELDS)


@dataclass
class StalenessCheck(BaseCheck):
    max_staleness_ms: int

    def __init__(self, max_staleness_ms: int):
        super().__init__(name="staleness", critical=False, flags=[QualityFlag.STALE_EVENT])
        self.max_staleness_ms = max_staleness_ms

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df["staleness_ms"] = df.get("staleness_ms", 0)
        mask = df["staleness_ms"] > self.max_staleness_ms
        df["check_stale"] = mask
        return self._set_flag(df, mask, QualityFlag.STALE_EVENT)


@dataclass
class OutlierCheck(BaseCheck):
    zscore_threshold: float
    window: int

    def __init__(self, zscore_threshold: float = 6.0, window: int = 50):
        super().__init__(name="outlier", critical=False, flags=[QualityFlag.OUTLIER_PRICE, QualityFlag.OUTLIER_QTY])
        self.zscore_threshold = zscore_threshold
        self.window = window

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "price" not in df:
            return df
        z_price = robust_zscore(df["price"].astype(float), self.window).abs()
        price_mask = z_price > self.zscore_threshold
        qty_mask = pd.Series(False, index=df.index)
        if "qty" in df:
            z_qty = robust_zscore(df["qty"].astype(float), self.window).abs()
            qty_mask = z_qty > self.zscore_threshold
        df["check_outlier_price"] = price_mask
        df["check_outlier_qty"] = qty_mask
        df["outlier"] = price_mask | qty_mask
        df = self._set_flag(df, price_mask, QualityFlag.OUTLIER_PRICE)
        df = self._set_flag(df, qty_mask, QualityFlag.OUTLIER_QTY)
        return df


@dataclass
class DuplicateCheck(BaseCheck):
    subset: List[str]

    def __init__(self, subset: Optional[List[str]] = None):
        super().__init__(name="duplicate", critical=False, flags=[QualityFlag.DUPLICATE_EVENT])
        self.subset = subset or ["symbol", "venue", "source", "event_type", "seq", "event_time_aligned"]

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        mask = df.duplicated(subset=self.subset, keep="first")
        df["duplicate"] = mask
        return self._set_flag(df, mask, QualityFlag.DUPLICATE_EVENT)


@dataclass
class SequenceGapCheck(BaseCheck):
    tolerance: int

    def __init__(self, tolerance: int = 1):
        super().__init__(name="seq_gap", critical=False, flags=[QualityFlag.SEQ_GAP])
        self.tolerance = tolerance

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "seq" not in df:
            return df
        df = df.sort_values("seq")
        gaps = df["seq"].diff().fillna(0) > self.tolerance
        df["check_seq_gap"] = gaps
        return self._set_flag(df, gaps, QualityFlag.SEQ_GAP)


@dataclass
class TimeTravelCheck(BaseCheck):
    def __init__(self):
        super().__init__(name="time_travel", critical=True, flags=[QualityFlag.TIME_TRAVEL])

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.sort_values("event_time_aligned")
        mask = df["event_time_aligned"].diff().dt.total_seconds().fillna(0) < 0
        df["check_time_travel"] = mask
        return self._set_flag(df, mask, QualityFlag.TIME_TRAVEL)


@dataclass
class BookSanityCheck(BaseCheck):
    max_spread_bps: float
    min_depth: int

    def __init__(self, max_spread_bps: float, min_depth: int = 1):
        super().__init__(name="book_sanity", critical=True, flags=[QualityFlag.BOOK_INVALID, QualityFlag.SPREAD_ANOMALY, QualityFlag.BOOK_EMPTY, QualityFlag.BOOK_EVAPORATION])
        self.max_spread_bps = max_spread_bps
        self.min_depth = min_depth

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "spread" not in df:
            return df
        spread = df["spread"].astype(float)
        mid = df.get("mid_price", pd.Series(np.nan, index=df.index)).astype(float)
        spread_bps = spread / mid.replace(0, np.nan) * 10_000
        empty_mask = df.get("bid_px", pd.Series([[]]*len(df))).apply(lambda x: len(x) == 0) | df.get("ask_px", pd.Series([[]]*len(df))).apply(lambda x: len(x) == 0)
        bad_spread = (spread_bps.abs() > self.max_spread_bps) | spread.isna()
        book_evap = df.get("bid_sz", pd.Series([[]]*len(df))).apply(lambda x: len(x) < self.min_depth) | df.get("ask_sz", pd.Series([[]]*len(df))).apply(lambda x: len(x) < self.min_depth)
        df["check_spread_anomaly"] = bad_spread
        df["book_ok"] = ~(bad_spread | empty_mask | book_evap)
        df = self._set_flag(df, bad_spread, QualityFlag.SPREAD_ANOMALY)
        df = self._set_flag(df, empty_mask, QualityFlag.BOOK_EMPTY)
        df = self._set_flag(df, book_evap, QualityFlag.BOOK_EVAPORATION)
        critical_mask = bad_spread | empty_mask
        return self._set_flag(df, critical_mask, QualityFlag.BOOK_INVALID)


@dataclass
class CrossSourceConsistencyCheck(BaseCheck):
    max_premium_bps: float

    def __init__(self, max_premium_bps: float = 50.0):
        super().__init__(name="cross_source", critical=True, flags=[QualityFlag.CROSS_SOURCE_MISMATCH])
        self.max_premium_bps = max_premium_bps

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if "source" not in df or "mid_price" not in df:
            df["cross_source_ok"] = True
            df["cross_source_error_code"] = 0
            return df
        pivot = df.pivot_table(index="event_time_aligned", columns="source", values="mid_price", aggfunc="last")
        mismatch = pd.Series(False, index=df.index)
        for ts, row in pivot.dropna(how="any").iterrows():
            vals = row.values
            ref = vals[0]
            premium_bps = (vals - ref) / ref * 10_000
            if (np.abs(premium_bps) > self.max_premium_bps).any():
                mask = df["event_time_aligned"] == ts
                mismatch |= mask
        df["cross_source_ok"] = ~mismatch
        df["cross_source_error_code"] = mismatch.astype(int)
        return self._set_flag(df, mismatch, QualityFlag.CROSS_SOURCE_MISMATCH)


@dataclass
class HaltDetectionCheck(BaseCheck):
    max_no_trade_s: int

    def __init__(self, max_no_trade_s: int = 300):
        super().__init__(name="halt", critical=False, flags=[QualityFlag.HALT_DETECTED])
        self.max_no_trade_s = max_no_trade_s

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        trades = df[df["event_type"] == "trade"].sort_values("event_time_aligned")
        if trades.empty:
            df["check_halt"] = True
            return self._set_flag(df, pd.Series(True, index=df.index), QualityFlag.HALT_DETECTED)
        gaps = trades["event_time_aligned"].diff().dt.total_seconds().fillna(0)
        halt_times = trades.loc[gaps > self.max_no_trade_s, "event_time_aligned"]
        mask = df["event_time_aligned"].isin(halt_times)
        df["check_halt"] = mask
        return self._set_flag(df, mask, QualityFlag.HALT_DETECTED)


@dataclass
class SchemaValidationCheck(BaseCheck):
    required: List[str]

    def __init__(self, required: List[str]):
        super().__init__(name="schema", critical=True, flags=[QualityFlag.SCHEMA_INVALID])
        self.required = required

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        missing_cols = [c for c in self.required if c not in df.columns]
        if missing_cols:
            df["schema_ok"] = False
            return self._set_flag(df, pd.Series(True, index=df.index), QualityFlag.SCHEMA_INVALID)
        mask = df[self.required].isna().any(axis=1)
        df["schema_ok"] = ~mask
        return self._set_flag(df, mask, QualityFlag.SCHEMA_INVALID)


@dataclass
class MicrostructureToxicityCheck(BaseCheck):
    spread_threshold_bps: float

    def __init__(self, spread_threshold_bps: float = 200.0):
        super().__init__(name="toxicity", critical=False, flags=[QualityFlag.MICROSTRUCTURE_TOXIC])
        self.spread_threshold_bps = spread_threshold_bps

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "spread" not in df or "mid_price" not in df:
            return df
        spread_bps = df["spread"].astype(float) / df["mid_price"].replace(0, np.nan) * 10_000
        toxic = spread_bps.abs() > self.spread_threshold_bps
        df["check_toxic"] = toxic
        return self._set_flag(df, toxic, QualityFlag.MICROSTRUCTURE_TOXIC)


@dataclass
class ClockSkewCheck(BaseCheck):
    max_skew_ms: int

    def __init__(self, max_skew_ms: int = 2000):
        super().__init__(name="clock_skew", critical=True, flags=[QualityFlag.CLOCK_SKEW_HIGH])
        self.max_skew_ms = max_skew_ms

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        mask = df["skew_ms"].abs() > self.max_skew_ms
        df["check_skew"] = mask
        return self._set_flag(df, mask, QualityFlag.CLOCK_SKEW_HIGH)
