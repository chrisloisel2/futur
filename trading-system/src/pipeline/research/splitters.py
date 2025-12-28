from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pandas as pd

from common.logging.setup import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Split:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    purge_window: pd.Timedelta
    embargo_window: pd.Timedelta


@dataclass(frozen=True)
class Embargo:
    duration: pd.Timedelta

    @classmethod
    def from_minutes(cls, minutes: int) -> "Embargo":
        return cls(pd.Timedelta(minutes=minutes))


class WalkForwardSplitter:
    def __init__(
        self,
        train_window: pd.Timedelta,
        test_window: pd.Timedelta,
        step: pd.Timedelta,
        purge_window: pd.Timedelta,
        embargo: Embargo,
    ) -> None:
        self.train_window = train_window
        self.test_window = test_window
        self.step = step
        self.purge_window = purge_window
        self.embargo = embargo

    def split(self, index: pd.DatetimeIndex) -> List[Split]:
        idx = index.sort_values().unique()
        if len(idx) == 0:
            return []
        splits: List[Split] = []
        start = idx[0]
        end = idx[-1]
        cursor = start
        while cursor + self.train_window + self.test_window <= end:
            train_start = cursor
            train_end = cursor + self.train_window
            test_start = train_end + self.purge_window
            test_end = test_start + self.test_window
            splits.append(
                Split(
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    purge_window=self.purge_window,
                    embargo_window=self.embargo.duration,
                )
            )
            cursor = cursor + self.step
        logger.info({"msg": "computed walk-forward splits", "splits": len(splits)})
        return splits


class PurgedKFoldSplitter:
    def __init__(self, n_splits: int, purge_window: pd.Timedelta, embargo: Embargo) -> None:
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        self.n_splits = n_splits
        self.purge_window = purge_window
        self.embargo = embargo

    def split(self, index: pd.DatetimeIndex) -> List[Split]:
        idx = index.sort_values().unique()
        n = len(idx)
        fold_size = n // self.n_splits
        splits: List[Split] = []
        for fold in range(self.n_splits):
            test_start_idx = fold * fold_size
            test_end_idx = n if fold == self.n_splits - 1 else (fold + 1) * fold_size
            test_start = idx[test_start_idx]
            test_end = idx[test_end_idx - 1]
            train_mask = (idx < test_start - self.purge_window) | (idx > test_end + self.embargo.duration)
            if not train_mask.any():
                continue
            train_start = idx[train_mask][0]
            train_end = idx[train_mask][-1]
            splits.append(
                Split(
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    purge_window=self.purge_window,
                    embargo_window=self.embargo.duration,
                )
            )
        logger.info({"msg": "computed purged k-fold splits", "splits": len(splits)})
        return splits
