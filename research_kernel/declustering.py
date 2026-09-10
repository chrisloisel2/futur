"""Declustering — how many independent events there really are.

This module exists because of a measured trap.  The obvious implementation
opens a new episode when the gap with the **previous observation** exceeds the
window.  That is single-link chaining: one symbol sampled every three hours for
ten days, with a 24-hour window, collapses eighty decisions into **one**
episode, and a daily cross-sectional book over six years collapses 2190
decisions into **one**.  Moving the window from 24h to 23h changes the sample
size by a factor of 2190.

Three methods are provided.  ``spec.declustering_rule.method`` must name one
**before** any result is looked at, because choosing afterwards is one more
trial:

``first_link``
    Chains from the previous observation.  Kept only so that historical numbers
    remain reproducible.  Never pick it for a new mechanism.
``complete_link``
    A new episode opens when the gap with the episode **start** exceeds the
    window.  Note that at an exactly daily cadence with a 24h window this counts
    by pairs, since 24h is not strictly greater than 24h.
``fixed_windows``
    Calendar buckets of ``window_seconds`` anchored at the epoch.  Independent
    of arrival jitter, which makes it the safest default for a cross-sectional
    book rebalanced on a fixed grid.

Design rule that follows: **rebalance at a step strictly greater than the
declustering window**, or the sample size is an illusion.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

METHODS = ("first_link", "complete_link", "fixed_windows")


class DeclusteringError(ValueError):
    pass


def _as_epoch_seconds(timestamps: Sequence) -> np.ndarray:
    ts = pd.to_datetime(pd.Series(list(timestamps)), utc=True)
    return ts.astype("int64").to_numpy() / 1e9


def episode_ids(
    timestamps: Sequence,
    symbols: Optional[Sequence] = None,
    window_seconds: float = 86400.0,
    method: str = "fixed_windows",
) -> np.ndarray:
    """Return one episode label per observation.

    Episodes are formed within a symbol when ``symbols`` is given; a
    cross-sectional book that rebalances the whole universe at once should pass
    ``symbols=None`` so that one rebalance is one episode.
    """
    if method not in METHODS:
        raise DeclusteringError("method %r not in %s" % (method, METHODS))
    if window_seconds <= 0:
        raise DeclusteringError("window_seconds must be > 0")

    secs = _as_epoch_seconds(timestamps)
    n = len(secs)
    if n == 0:
        return np.empty(0, dtype=object)

    keys = (
        np.array(["__all__"] * n, dtype=object)
        if symbols is None
        else np.asarray(list(symbols), dtype=object)
    )

    labels = np.empty(n, dtype=object)
    order = np.lexsort((secs, keys.astype(str)))

    if method == "fixed_windows":
        buckets = np.floor(secs / window_seconds).astype("int64")
        for i in range(n):
            labels[i] = "%s|%d" % (keys[i], buckets[i])
        return labels

    counters: Dict[object, int] = {}
    anchor: Dict[object, float] = {}
    for idx in order:
        k = keys[idx]
        t = secs[idx]
        if k not in counters:
            counters[k] = 0
            anchor[k] = t
        else:
            if t - anchor[k] > window_seconds:
                counters[k] += 1
                anchor[k] = t
            elif method == "first_link":
                # chain from the previous observation, not the episode start
                anchor[k] = t
        labels[idx] = "%s|%d" % (k, counters[k])
    return labels


def n_independent(
    timestamps: Sequence,
    symbols: Optional[Sequence] = None,
    window_seconds: float = 86400.0,
    method: str = "fixed_windows",
) -> int:
    return int(len(set(episode_ids(timestamps, symbols, window_seconds, method))))


def decluster_frame(
    df: pd.DataFrame,
    window_seconds: float,
    method: str,
    timestamp_col: str = "timestamp",
    symbol_col: Optional[str] = "symbol",
) -> pd.DataFrame:
    """Attach an ``episode_id`` column.  Does not aggregate."""
    if timestamp_col not in df.columns:
        raise DeclusteringError("missing column %r" % timestamp_col)
    symbols = df[symbol_col] if (symbol_col and symbol_col in df.columns) else None
    out = df.copy()
    out["episode_id"] = episode_ids(df[timestamp_col], symbols, window_seconds, method)
    return out


def assert_step_exceeds_window(step_seconds: float, window_seconds: float) -> None:
    """Guard the design rule; call it when building a scheduled book."""
    if step_seconds <= window_seconds:
        raise DeclusteringError(
            "rebalance step %.0fs must be strictly greater than the declustering "
            "window %.0fs, otherwise consecutive rebalances chain into one episode"
            % (step_seconds, window_seconds)
        )


def episode_counts_all_methods(
    timestamps: Sequence,
    symbols: Optional[Sequence] = None,
    window_seconds: float = 86400.0,
) -> Dict[str, int]:
    """Report the three counts side by side — a spread between them is the
    signal that the sample size is an artefact of the counting rule."""
    return {
        m: n_independent(timestamps, symbols, window_seconds, m) for m in METHODS
    }
