from __future__ import annotations

import pandas as pd


class BarBuilder:
    def __init__(self, freq: str = "1min"):
        self.freq = freq

    def build_ohlcv(self, trades_df: pd.DataFrame, freq: str | None = None) -> pd.DataFrame:
        if trades_df.empty:
            return trades_df
        freq = freq or self.freq
        df = trades_df.copy()
        df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
        df = df.set_index("event_time")
        agg = df.resample(freq).agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("qty", "sum"),
            trades_count=("price", "count"),
        )
        agg["bar_start"] = agg.index
        agg["bar_end"] = agg.index + pd.to_timedelta(freq)
        agg["bar_size_s"] = pd.to_timedelta(freq).total_seconds()
        return agg.reset_index(drop=True)

    def build_micro_bars(self, book_df: pd.DataFrame, freq: str = "1s") -> pd.DataFrame:
        if book_df.empty:
            return book_df
        df = book_df.copy()
        df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
        df = df.set_index("event_time")
        agg = df.resample(freq).agg(mid_price=("mid_price", "last"), spread=("spread", "last"))
        agg["bar_start"] = agg.index
        agg["bar_end"] = agg.index + pd.to_timedelta(freq)
        agg["bar_size_s"] = pd.to_timedelta(freq).total_seconds()
        return agg.reset_index(drop=True)

    def build_returns(self, ohlcv_df: pd.DataFrame) -> pd.DataFrame:
        if ohlcv_df.empty:
            return ohlcv_df
        df = ohlcv_df.copy()
        df["return"] = df["close"].pct_change().fillna(0)
        return df
