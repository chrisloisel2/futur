from __future__ import annotations

import re
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

from common.logging.setup import get_logger

logger = get_logger(__name__)


class Normalizer:
    def __init__(self, depth: int = 20):
        self.depth = depth

    def normalize_symbol(self, symbol: str) -> str:
        if not symbol:
            return ""
        cleaned = re.sub(r"[^A-Za-z0-9]", "", str(symbol)).upper()
        return cleaned

    def normalize_side(self, side: str) -> str:
        if not side:
            return ""
        s = str(side).lower()
        return "buy" if s.startswith("b") else "sell"

    def normalize_book(self, bids: Iterable, asks: Iterable) -> Dict[str, List[float]]:
        bid_px, bid_sz, ask_px, ask_sz = [], [], [], []
        for px, sz in list(bids)[: self.depth]:
            bid_px.append(float(px))
            bid_sz.append(float(sz))
        for px, sz in list(asks)[: self.depth]:
            ask_px.append(float(px))
            ask_sz.append(float(sz))
        return {
            "bid_px": bid_px,
            "bid_sz": bid_sz,
            "ask_px": ask_px,
            "ask_sz": ask_sz,
            "depth": min(self.depth, max(len(bid_px), len(ask_px))),
        }

    def deduplicate(self, df: pd.DataFrame) -> pd.DataFrame:
        keys = ["symbol", "venue", "source", "event_type", "seq", "event_time"]
        before = len(df)
        df = df.drop_duplicates(subset=keys, keep="last")
        after = len(df)
        if after < before:
            logger.info({"msg": "deduplicated events", "dropped": before - after})
        return df

    def normalize_events(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "symbol" in df:
            df["symbol"] = df["symbol"].apply(self.normalize_symbol)
        if "side" in df:
            df["side"] = df["side"].apply(self.normalize_side)
        if "price" in df:
            df["price"] = df["price"].astype(float)
        if "qty" in df:
            df["qty"] = df["qty"].astype(float)
        if {"bid_px", "bid_sz", "ask_px", "ask_sz"}.issubset(df.columns):
            df["mid_price"] = (df["bid_px"].apply(lambda x: x[0] if x else np.nan) + df["ask_px"].apply(lambda x: x[0] if x else np.nan)) / 2
            df["spread"] = df["ask_px"].apply(lambda x: x[0] if x else np.nan) - df["bid_px"].apply(lambda x: x[0] if x else np.nan)
        return df
