"""Gate 4 — beat the null this design generates, not zero.

A placebo answers one question: how large a result does this exact experimental
design produce when there is nothing there?  The answer is rarely zero.  Across
27 configurations of **random** baskets, one reached t = 2.77.  So the reference
for an observed edge is the null interval of its own design.

Five tests, each returning a distribution of null means:

``direction_flip``
    Rademacher sign flips per decision.  Kills any edge that is really a drift.
``same_event_random_side``
    Sign flips at episode level, keeping the clustering intact.
``time_shuffle``
    Keeps the symbol, redraws the outcome from another instant of the panel.
``symbol_shuffle``
    Keeps the instant, redraws the outcome from another symbol.
``entry_time_randomization``
    Keeps only the event count per symbol, redrawing entries uniformly.

The last three need a panel of forward returns.  Without one they report
``SKIPPED_NO_PANEL`` and the gate fails closed: a placebo that could not run is
not a placebo that passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

PLACEBO_TESTS = (
    "direction_flip",
    "same_event_random_side",
    "time_shuffle",
    "symbol_shuffle",
    "entry_time_randomization",
)

PANEL_TESTS = frozenset({"time_shuffle", "symbol_shuffle", "entry_time_randomization"})

DEFAULT_DRAWS = 2000


class PlaceboError(ValueError):
    pass


@dataclass
class PlaceboResult:
    test: str
    status: str
    observed_bps: float = float("nan")
    null_mean_bps: float = float("nan")
    null_p2_5_bps: float = float("nan")
    null_p97_5_bps: float = float("nan")
    percentile: float = float("nan")
    n_draws: int = 0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test": self.test,
            "status": self.status,
            "observed_bps": self.observed_bps,
            "null_mean_bps": self.null_mean_bps,
            "null_p2_5_bps": self.null_p2_5_bps,
            "null_p97_5_bps": self.null_p97_5_bps,
            "percentile": self.percentile,
            "n_draws": self.n_draws,
            "note": self.note,
        }


def _percentile_of(observed: float, null: np.ndarray) -> float:
    if len(null) == 0:
        return float("nan")
    return float(100.0 * np.mean(null < observed))


def _episode_means(gross: np.ndarray, episode_ids: np.ndarray) -> np.ndarray:
    s = pd.Series(gross).groupby(pd.Series(episode_ids)).mean()
    return s.to_numpy(dtype=float)


@dataclass
class PlaceboSuite:
    """Runs the placebo battery for one mechanism run.

    ``decisions`` needs ``timestamp``, ``symbol``, ``gross_bps`` and
    ``episode_id``.  ``panel`` is optional and needs ``timestamp``, ``symbol``,
    ``fwd_bps`` — the forward return every symbol would have delivered at every
    sampled instant, at the mechanism's horizon.
    """

    decisions: pd.DataFrame
    panel: Optional[pd.DataFrame] = None
    n_draws: int = DEFAULT_DRAWS
    seed: int = 20260910
    _rng: np.random.Generator = field(init=False)

    def __post_init__(self) -> None:
        for c in ("timestamp", "symbol", "gross_bps", "episode_id"):
            if c not in self.decisions.columns:
                raise PlaceboError("decisions frame needs a %r column" % c)
        self._rng = np.random.default_rng(self.seed)

    # ------------------------------------------------------------------
    @property
    def observed_bps(self) -> float:
        return float(
            _episode_means(
                self.decisions["gross_bps"].to_numpy(dtype=float),
                self.decisions["episode_id"].to_numpy(),
            ).mean()
        )

    def run(self, tests: Sequence[str]) -> List[PlaceboResult]:
        unknown = sorted(set(tests) - set(PLACEBO_TESTS))
        if unknown:
            raise PlaceboError("unknown placebo tests: %s" % ", ".join(unknown))
        return [getattr(self, "_" + t)() for t in tests]

    # ------------------------------------------------------- flip families
    def _flip(self, by_episode: bool, name: str) -> PlaceboResult:
        gross = self.decisions["gross_bps"].to_numpy(dtype=float)
        eids = self.decisions["episode_id"].to_numpy()
        uniq, inverse = np.unique(eids, return_inverse=True)
        draws = np.empty(self.n_draws, dtype=float)
        for i in range(self.n_draws):
            if by_episode:
                signs = self._rng.choice([-1.0, 1.0], size=len(uniq))[inverse]
            else:
                signs = self._rng.choice([-1.0, 1.0], size=len(gross))
            draws[i] = _episode_means(gross * signs, eids).mean()
        return self._result(name, draws)

    def _direction_flip(self) -> PlaceboResult:
        return self._flip(by_episode=False, name="direction_flip")

    def _same_event_random_side(self) -> PlaceboResult:
        return self._flip(by_episode=True, name="same_event_random_side")

    # ------------------------------------------------------ panel families
    def _panel_or_skip(self, name: str) -> Optional[PlaceboResult]:
        if self.panel is None:
            return PlaceboResult(
                test=name,
                status="SKIPPED_NO_PANEL",
                observed_bps=self.observed_bps,
                note="no forward-return panel supplied; the gate fails closed",
            )
        for c in ("timestamp", "symbol", "fwd_bps"):
            if c not in self.panel.columns:
                raise PlaceboError("panel needs a %r column" % c)
        return None

    def _time_shuffle(self) -> PlaceboResult:
        skip = self._panel_or_skip("time_shuffle")
        if skip:
            return skip
        eids = self.decisions["episode_id"].to_numpy()
        sides = np.sign(self.decisions.get("side", pd.Series(np.ones(len(self.decisions)))).to_numpy(dtype=float))
        sides[sides == 0] = 1.0
        by_symbol = {
            str(s): g["fwd_bps"].to_numpy(dtype=float)
            for s, g in self.panel.groupby("symbol")
        }
        symbols = self.decisions["symbol"].astype(str).to_numpy()
        pools = [by_symbol.get(s) for s in symbols]
        missing = sum(1 for p in pools if p is None or len(p) == 0)
        if missing == len(pools):
            return PlaceboResult(
                test="time_shuffle",
                status="SKIPPED_NO_PANEL",
                observed_bps=self.observed_bps,
                note="panel covers none of the traded symbols",
            )
        draws = np.empty(self.n_draws, dtype=float)
        for i in range(self.n_draws):
            vals = np.array(
                [
                    self._rng.choice(p) if p is not None and len(p) else 0.0
                    for p in pools
                ],
                dtype=float,
            )
            draws[i] = _episode_means(vals * sides, eids).mean()
        return self._result("time_shuffle", draws)

    def _symbol_shuffle(self) -> PlaceboResult:
        skip = self._panel_or_skip("symbol_shuffle")
        if skip:
            return skip
        eids = self.decisions["episode_id"].to_numpy()
        sides = np.sign(self.decisions.get("side", pd.Series(np.ones(len(self.decisions)))).to_numpy(dtype=float))
        sides[sides == 0] = 1.0
        panel = self.panel.copy()
        panel["_ts"] = pd.to_datetime(panel["timestamp"], utc=True)
        by_ts = {ts: g["fwd_bps"].to_numpy(dtype=float) for ts, g in panel.groupby("_ts")}
        dts = pd.to_datetime(self.decisions["timestamp"], utc=True).to_numpy()
        pools = [by_ts.get(pd.Timestamp(t)) for t in dts]
        if all(p is None or len(p) == 0 for p in pools):
            return PlaceboResult(
                test="symbol_shuffle",
                status="SKIPPED_NO_PANEL",
                observed_bps=self.observed_bps,
                note="panel has no instant in common with the decisions",
            )
        draws = np.empty(self.n_draws, dtype=float)
        for i in range(self.n_draws):
            vals = np.array(
                [self._rng.choice(p) if p is not None and len(p) else 0.0 for p in pools],
                dtype=float,
            )
            draws[i] = _episode_means(vals * sides, eids).mean()
        return self._result("symbol_shuffle", draws)

    def _entry_time_randomization(self) -> PlaceboResult:
        skip = self._panel_or_skip("entry_time_randomization")
        if skip:
            return skip
        counts = self.decisions.groupby(self.decisions["symbol"].astype(str)).size()
        by_symbol = {
            str(s): g["fwd_bps"].to_numpy(dtype=float)
            for s, g in self.panel.groupby("symbol")
        }
        usable = {s: by_symbol[s] for s in counts.index if s in by_symbol and len(by_symbol[s])}
        if not usable:
            return PlaceboResult(
                test="entry_time_randomization",
                status="SKIPPED_NO_PANEL",
                observed_bps=self.observed_bps,
                note="panel covers none of the traded symbols",
            )
        draws = np.empty(self.n_draws, dtype=float)
        for i in range(self.n_draws):
            per_symbol = [
                float(np.mean(self._rng.choice(usable[s], size=int(counts[s]), replace=True)))
                for s in usable
            ]
            draws[i] = float(np.mean(per_symbol))
        return self._result("entry_time_randomization", draws)

    # ------------------------------------------------------------------
    def _result(self, name: str, draws: np.ndarray) -> PlaceboResult:
        obs = self.observed_bps
        return PlaceboResult(
            test=name,
            status="RUN",
            observed_bps=obs,
            null_mean_bps=float(np.mean(draws)),
            null_p2_5_bps=float(np.percentile(draws, 2.5)),
            null_p97_5_bps=float(np.percentile(draws, 97.5)),
            percentile=_percentile_of(obs, draws),
            n_draws=int(len(draws)),
        )


def placebo_gate(
    results: Sequence[PlaceboResult], min_percentile: float = 95.0
) -> List[str]:
    """Gate 4.  Returns failures; empty means the mechanism beat its own null."""
    failures: List[str] = []
    for r in results:
        if r.status != "RUN":
            failures.append("%s did not run (%s)" % (r.test, r.status))
            continue
        if not np.isfinite(r.percentile) or r.percentile < min_percentile:
            failures.append(
                "%s: observed %.2f bps sits at the %.1fth percentile of its own null "
                "[%.2f, %.2f]"
                % (
                    r.test,
                    r.observed_bps,
                    r.percentile,
                    r.null_p2_5_bps,
                    r.null_p97_5_bps,
                )
            )
    return failures
