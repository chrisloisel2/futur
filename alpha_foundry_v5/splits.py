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
    """Expanding walk-forward with purge and embargo around each test block."""

    def __init__(self, n_splits: int = 5, purge_ms: int = 0, embargo_ms: int = 0, min_train_fraction: float = 0.35):
        if int(n_splits) < 2:
            raise ValueError("n_splits must be >=2")
        if float(min_train_fraction) <= 0 or float(min_train_fraction) >= 1:
            raise ValueError("min_train_fraction must be in (0,1)")
        self.n_splits = int(n_splits)
        self.purge_ns = int(purge_ms) * 1_000_000
        self.embargo_ns = int(embargo_ms) * 1_000_000
        self.min_train_fraction = float(min_train_fraction)

    def split(self, timestamps_ns: Sequence[int]) -> Iterator[PurgedFold]:
        ts = np.asarray(timestamps_ns, dtype=np.int64)
        if ts.ndim != 1 or len(ts) < self.n_splits * 10:
            raise ValueError("insufficient timestamps")
        if np.any(np.diff(ts) < 0):
            raise ValueError("timestamps must be sorted")
        n = len(ts)
        first_test = max(1, int(np.floor(n * self.min_train_fraction)))
        remaining = n - first_test
        block = max(1, remaining // self.n_splits)
        for fold in range(self.n_splits):
            test_start_i = first_test + fold * block
            test_stop_i = n if fold == self.n_splits - 1 else min(n, first_test + (fold + 1) * block)
            if test_start_i >= test_stop_i:
                continue
            test_start_ns = int(ts[test_start_i])
            test_stop_ns = int(ts[test_stop_i - 1]) + 1
            train_cut_ns = test_start_ns - self.purge_ns
            train_idx = np.flatnonzero(ts < train_cut_ns)
            test_idx = np.flatnonzero((ts >= test_start_ns) & (ts < test_stop_ns))
            if len(train_idx) < 10 or len(test_idx) < 10:
                continue
            yield PurgedFold("wf-%02d" % fold, train_idx, test_idx, TimeWindow(int(ts[train_idx[0]]), int(ts[train_idx[-1]]) + 1), TimeWindow(int(ts[test_idx[0]]), int(ts[test_idx[-1]]) + 1))


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
