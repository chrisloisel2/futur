"""Gate 0 — data quality, before anything is computed on the data.

Every dataset entering the kernel declares a manifest and passes a contract.
The checks are the ones that have already produced false results here:
placeholder columns that were read as real (``taker_buy_*`` in the enriched
store), stale files that stopped updating months ago, a universe that is not
point-in-time, and a latency that nobody measured until the mechanism turned
out to be unexecutable by 45 hours.

The latency rule is hard and lives here: **if data latency exceeds horizon / 4,
the mechanism is rejected**, however good the historical numbers look.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

BAR_COLUMNS = [
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
]

TRADE_COLUMNS = [
    "timestamp",
    "exchange",
    "symbol",
    "price",
    "qty",
    "side",
    "trade_id",
    "source_latency_ms",
]

ORDERBOOK_L1_COLUMNS = [
    "timestamp",
    "exchange",
    "symbol",
    "bid",
    "bid_qty",
    "ask",
    "ask_qty",
    "mid",
    "spread_bps",
    "source_latency_ms",
]

ORDERBOOK_L2_COLUMNS = [
    "timestamp",
    "exchange",
    "symbol",
    "level",
    "bid_price",
    "bid_qty",
    "ask_price",
    "ask_qty",
    "source_latency_ms",
]

POSITIONING_COLUMNS = [
    "timestamp",
    "exchange",
    "symbol",
    "long_account_ratio",
    "short_account_ratio",
    "long_short_ratio",
    "source_latency_ms",
]

CONTRACTS: Dict[str, List[str]] = {
    "bar": BAR_COLUMNS,
    "trade": TRADE_COLUMNS,
    "orderbook_l1": ORDERBOOK_L1_COLUMNS,
    "orderbook_l2": ORDERBOOK_L2_COLUMNS,
    "positioning": POSITIONING_COLUMNS,
}

#: Values that have masqueraded as data in this repository.
PLACEHOLDER_SENTINELS = (0.0, -1.0, -999.0)

#: Columns that are legitimately constant within one file and are therefore
#: exempt from the constant-column check.
META_COLUMNS = frozenset(
    {
        "symbol",
        "exchange",
        "venue",
        "source",
        "source_stream",
        "source_latency_ms",
        "level",
        "period",
        "event_ts_ns",
        "receive_ts_ns",
    }
)


class DataContractError(ValueError):
    pass


def schema_hash(columns: Sequence[str]) -> str:
    blob = ",".join(sorted(str(c) for c in columns))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class Manifest:
    dataset_id: str
    source: str
    created_at: str
    start: str
    end: str
    symbols: List[str] = field(default_factory=list)
    latency_assumption_ms: Optional[float] = None
    point_in_time: bool = False
    known_gaps: List[List[str]] = field(default_factory=list)
    schema_hash: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        return path

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        with Path(path).open("r", encoding="utf-8") as fh:
            return cls(**json.load(fh))


@dataclass
class DataQualityReport:
    dataset_id: str
    n_rows: int = 0
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    measured_latency_ms: Optional[float] = None
    measured_latency_p95_ms: Optional[float] = None
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["passed"] = self.passed
        return out

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True, default=str)
        return path

    def merge(self, other: "DataQualityReport") -> "DataQualityReport":
        self.violations.extend("%s: %s" % (other.dataset_id, v) for v in other.violations)
        self.warnings.extend("%s: %s" % (other.dataset_id, w) for w in other.warnings)
        self.stats[other.dataset_id] = other.stats
        return self


def check_frame(
    df: pd.DataFrame,
    dataset_id: str,
    contract: Optional[str] = None,
    manifest: Optional[Manifest] = None,
    timestamp_col: str = "timestamp",
    symbol_col: Optional[str] = "symbol",
    numeric_cols: Optional[Sequence[str]] = None,
    max_gap_seconds: Optional[float] = None,
) -> DataQualityReport:
    """Run the gate 0 battery.  Violations reject; warnings are recorded."""
    rep = DataQualityReport(dataset_id=dataset_id, n_rows=int(len(df)))

    if contract is not None:
        if contract not in CONTRACTS:
            raise DataContractError("unknown contract %r" % (contract,))
        missing = [c for c in CONTRACTS[contract] if c not in df.columns]
        if missing:
            rep.violations.append("missing contract columns: %s" % ", ".join(missing))

    if len(df) == 0:
        rep.violations.append("dataset is empty")
        return rep

    if timestamp_col not in df.columns:
        rep.violations.append("missing timestamp column %r" % timestamp_col)
        return rep

    ts = pd.to_datetime(df[timestamp_col], utc=True, errors="coerce")
    if ts.isna().any():
        rep.violations.append("%d unparseable timestamps" % int(ts.isna().sum()))
        return rep

    rep.stats["start"] = str(ts.min())
    rep.stats["end"] = str(ts.max())

    # --- monotonicity, per symbol when there is one
    if symbol_col and symbol_col in df.columns:
        grouped = ts.groupby(df[symbol_col].values)
        non_mono = [str(k) for k, v in grouped if not v.is_monotonic_increasing]
        if non_mono:
            rep.violations.append(
                "non-monotonic timestamps for %d symbols (%s...)"
                % (len(non_mono), ", ".join(non_mono[:3]))
            )
        dup = df.duplicated(subset=[timestamp_col, symbol_col]).sum()
    else:
        if not ts.is_monotonic_increasing:
            rep.violations.append("non-monotonic timestamps")
        dup = df.duplicated(subset=[timestamp_col]).sum()

    if dup:
        rep.violations.append("%d duplicated keys" % int(dup))

    # --- constant and placeholder columns
    cols = list(numeric_cols) if numeric_cols else [
        c
        for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and c not in META_COLUMNS
    ]
    for c in cols:
        s = df[c]
        if s.isna().all():
            rep.violations.append("column %r is entirely null" % c)
            continue
        nunique = s.nunique(dropna=True)
        if nunique <= 1:
            val = s.dropna().iloc[0] if s.notna().any() else None
            msg = "column %r is constant (%r)" % (c, val)
            if val in PLACEHOLDER_SENTINELS:
                rep.violations.append(msg + " — placeholder, not data")
            else:
                rep.violations.append(msg)

    # --- gaps
    if max_gap_seconds:
        deltas = ts.sort_values().diff().dt.total_seconds().dropna()
        big = deltas[deltas > max_gap_seconds]
        if len(big):
            known = set()
            if manifest:
                known = {tuple(g) for g in manifest.known_gaps}
            msg = "%d gaps longer than %.0fs (max %.0fs)" % (
                len(big),
                max_gap_seconds,
                big.max(),
            )
            if known:
                rep.warnings.append(msg + " — manifest declares %d known gaps" % len(known))
            else:
                rep.violations.append(msg + " — undocumented")

    # --- latency
    if {"event_ts_ns", "receive_ts_ns"}.issubset(df.columns):
        lat = (df["receive_ts_ns"] - df["event_ts_ns"]) / 1e6
        rep.measured_latency_ms = float(np.nanmedian(lat))
        rep.measured_latency_p95_ms = float(np.nanpercentile(lat, 95))
        if (lat < 0).any():
            rep.violations.append(
                "%d rows received before they happened" % int((lat < 0).sum())
            )
    elif "source_latency_ms" in df.columns and df["source_latency_ms"].notna().any():
        rep.measured_latency_ms = float(df["source_latency_ms"].median())
        rep.measured_latency_p95_ms = float(df["source_latency_ms"].quantile(0.95))
    elif manifest is not None and manifest.latency_assumption_ms is not None:
        rep.measured_latency_ms = float(manifest.latency_assumption_ms)
        rep.warnings.append("latency is a manifest assumption, not a measurement")
    else:
        rep.violations.append(
            "data availability delay is unknown — neither measured nor declared"
        )

    # --- point-in-time
    if manifest is not None and not manifest.point_in_time:
        rep.violations.append(
            "manifest declares the universe is not point-in-time (survivorship)"
        )

    return rep


def latency_gate(measured_latency_ms: float, max_latency_ms: float) -> Optional[str]:
    """The horizon/4 rule.  Returns a violation string, or None when it passes."""
    if measured_latency_ms is None:
        return "latency unknown"
    if measured_latency_ms > max_latency_ms:
        return (
            "data latency %.0f ms exceeds the budget of %.0f ms (horizon / 4): the "
            "signal expires before it can be acted on"
            % (measured_latency_ms, max_latency_ms)
        )
    return None


def assert_no_lookahead(
    df: pd.DataFrame,
    decision_time_col: str = "decision_ts",
    available_at_col: str = "available_at",
) -> None:
    """A decision may only use information already published at that instant."""
    for c in (decision_time_col, available_at_col):
        if c not in df.columns:
            raise DataContractError("missing column %r for the look-ahead check" % c)
    d = pd.to_datetime(df[decision_time_col], utc=True)
    a = pd.to_datetime(df[available_at_col], utc=True)
    bad = int((a > d).sum())
    if bad:
        raise DataContractError(
            "%d decisions use data published after the decision instant" % bad
        )
