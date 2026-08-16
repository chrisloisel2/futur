from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterator, Sequence, Tuple

import numpy as np

from .contracts import TimeWindow


@dataclass(frozen=True)
class PurgedFold:
    fold_id: str
    train_idx: np.ndarray
    test_idx: np.ndarray
    train_window: TimeWindow
    test_window: TimeWindow


class PurgedWalkForwardSplitter:
    """Past-only expanding walk-forward on unique timestamps.

    Fold boundaries are chosen on unique information times, never row indices,
    so a multi-symbol timestamp cannot be split across train/test folds. The
    pre-test exclusion gap is purge + embargo, which is conservative for
    overlapping labels/features while preserving a strictly past-only train.
    """

    def __init__(self, n_splits: int = 5, purge_ms: int = 0, embargo_ms: int = 0, min_train_fraction: float = 0.35):
        if int(n_splits) < 2:
            raise ValueError("n_splits must be >=2")
        if int(purge_ms) < 0 or int(embargo_ms) < 0:
            raise ValueError("purge_ms/embargo_ms must be non-negative")
        if float(min_train_fraction) <= 0 or float(min_train_fraction) >= 1:
            raise ValueError("min_train_fraction must be in (0,1)")
        self.n_splits = int(n_splits)
        self.purge_ns = int(purge_ms) * 1_000_000
        self.embargo_ns = int(embargo_ms) * 1_000_000
        self.min_train_fraction = float(min_train_fraction)

    def split(self, timestamps_ns: Sequence[int]) -> Iterator[PurgedFold]:
        ts = np.asarray(timestamps_ns, dtype=np.int64)
        if ts.ndim != 1:
            raise ValueError("timestamps must be one-dimensional")
        if np.any(np.diff(ts) < 0):
            raise ValueError("timestamps must be sorted")
        unique_ts = np.unique(ts)
        if len(unique_ts) < self.n_splits * 10:
            raise ValueError("insufficient unique timestamps")

        first_test_u = max(1, int(np.floor(len(unique_ts) * self.min_train_fraction)))
        remaining = len(unique_ts) - first_test_u
        block_u = max(1, remaining // self.n_splits)
        gap_ns = self.purge_ns + self.embargo_ns

        for fold in range(self.n_splits):
            test_start_u = first_test_u + fold * block_u
            test_stop_u = len(unique_ts) if fold == self.n_splits - 1 else min(len(unique_ts), first_test_u + (fold + 1) * block_u)
            if test_start_u >= test_stop_u:
                continue

            test_start_ns = int(unique_ts[test_start_u])
            test_stop_ns = int(unique_ts[test_stop_u]) if test_stop_u < len(unique_ts) else int(unique_ts[-1]) + 1
            train_cut_ns = test_start_ns - gap_ns
            train_idx = np.flatnonzero(ts < train_cut_ns)
            test_idx = np.flatnonzero((ts >= test_start_ns) & (ts < test_stop_ns))
            if len(train_idx) < 10 or len(test_idx) < 10:
                continue
            if np.intersect1d(train_idx, test_idx).size:
                raise AssertionError("train/test row overlap")
            if set(ts[train_idx]).intersection(set(ts[test_idx])):
                raise AssertionError("train/test timestamp overlap")
            yield PurgedFold(
                "wf-%02d" % fold,
                train_idx,
                test_idx,
                TimeWindow(int(ts[train_idx[0]]), int(ts[train_idx[-1]]) + 1),
                TimeWindow(int(ts[test_idx[0]]), int(ts[test_idx[-1]]) + 1),
            )


@dataclass(frozen=True)
class CSCVSplit:
    train_blocks: Tuple[int, ...]
    test_blocks: Tuple[int, ...]
    train_idx: np.ndarray
    test_idx: np.ndarray


def cscv_splits(n_rows: int, n_blocks: int = 10) -> Iterator[CSCVSplit]:
    if int(n_blocks) < 4 or int(n_blocks) % 2 != 0:
        raise ValueError("n_blocks must be even and >=4")
    if int(n_rows) < int(n_blocks):
        raise ValueError("not enough rows")
    blocks = [np.asarray(x, dtype=int) for x in np.array_split(np.arange(int(n_rows)), int(n_blocks))]
    half = int(n_blocks) // 2
    all_ids = tuple(range(int(n_blocks)))
    for train_ids in combinations(all_ids, half):
        train_set = set(train_ids)
        test_ids = tuple(i for i in all_ids if i not in train_set)
        yield CSCVSplit(tuple(train_ids), test_ids, np.concatenate([blocks[i] for i in train_ids]), np.concatenate([blocks[i] for i in test_ids]))
