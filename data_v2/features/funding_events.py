"""
data_v2/features/funding_events.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 10: funding as a DISCRETE settlement event, never a prorated
5-minute accrual. Binance USDM funding settles as a lump sum three times a
day (00:00/08:00/16:00 UTC, confirmed on data/derivatives_backfill/binance/
funding/*.parquet -- DATA_READY, 0/312 PIT symbols missing). A 5m/15m/1h
holding window crosses at most a handful of these marks; pro-rating a lump
sum across every 5m bar would misstate WHEN the cost hits (it would not
even misstate the total $ if no boundary is crossed, which is the common
case for short holds -- but any window that *does* cross a mark needs the
full lump sum applied exactly at the mark, not spread out).

Real funding timestamps carry a few milliseconds of jitter around the
canonical 00:00/08:00/16:00 UTC marks (per-symbol, per-settlement) -- do not
compare timestamps with exact equality when joining to another grid; round
to the hour first (see round_to_settlement_hour).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import pandas as pd

CANONICAL_SETTLEMENT_HOURS = (0, 8, 16)


def round_to_settlement_hour(ts: pd.Series) -> pd.Series:
    """Round a funding timestamp series to its nearest canonical settlement
    hour (00:00/08:00/16:00 UTC) -- collapses the few-ms real-world jitter
    so a join against another 5m/hourly grid doesn't silently drop ~35% of
    rows on exact-timestamp matching (observed and documented pitfall)."""
    ts = pd.to_datetime(ts, utc=True)
    return ts.dt.floor("h")


def settlements_between(
    funding_df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
    *,
    timestamp_col: str = "timestamp",
) -> pd.DataFrame:
    """Funding settlements strictly after entry_ts and up to and including
    exit_ts -- i.e. the settlements a position opened at entry_ts and held
    through exit_ts actually sits through. (entry_ts, exit_ts]."""
    entry_ts = pd.Timestamp(entry_ts)
    exit_ts = pd.Timestamp(exit_ts)
    ts = pd.to_datetime(funding_df[timestamp_col], utc=True)
    mask = (ts > entry_ts) & (ts <= exit_ts)
    return funding_df.loc[mask].sort_values(timestamp_col)


def crosses_settlement(entry_ts: pd.Timestamp, exit_ts: pd.Timestamp) -> bool:
    """True if [entry_ts, exit_ts] spans at least one canonical settlement
    mark, using the fixed 00:00/08:00/16:00 UTC schedule directly (no
    funding_df needed) -- a fast, source-independent version of the same
    check for when you only need a boolean, e.g. in a cost-model gate."""
    entry_ts = pd.Timestamp(entry_ts).tz_convert("UTC") if pd.Timestamp(entry_ts).tzinfo else pd.Timestamp(entry_ts, tz="UTC")
    exit_ts = pd.Timestamp(exit_ts).tz_convert("UTC") if pd.Timestamp(exit_ts).tzinfo else pd.Timestamp(exit_ts, tz="UTC")
    marks = pd.date_range(
        entry_ts.floor("D"), exit_ts.ceil("D"), freq="8h", tz="UTC"
    )
    return bool(((marks > entry_ts) & (marks <= exit_ts)).any())


@dataclass
class FundingCostResult:
    side: Literal["long", "short"]
    notional_usd: float
    crossed_settlements: int
    total_funding_paid_usd: float
    settlements: list = field(default_factory=list)  # list of {timestamp, funding_rate, paid_usd}


def funding_cost_for_window(
    funding_df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
    *,
    position_notional_usd: float,
    side: Literal["long", "short"],
    timestamp_col: str = "timestamp",
    rate_col: str = "funding_rate",
) -> FundingCostResult:
    """Exact discrete funding cost for holding `side` at `position_notional_usd`
    from entry_ts to exit_ts: sums the real settlement lump sums crossed,
    never a per-5m-bar proration. Sign convention: positive funding_rate
    means longs pay shorts (Binance convention) -- a long position PAYS
    funding_rate * notional at each crossed settlement, a short RECEIVES it.
    """
    crossed = settlements_between(funding_df, entry_ts, exit_ts, timestamp_col=timestamp_col)
    sign = -1.0 if side == "long" else 1.0

    settlements = []
    total = 0.0
    for _, row in crossed.iterrows():
        rate = float(row[rate_col])
        paid = sign * rate * position_notional_usd
        settlements.append({"timestamp": row[timestamp_col], "funding_rate": rate, "paid_usd": paid})
        total += paid

    return FundingCostResult(
        side=side,
        notional_usd=position_notional_usd,
        crossed_settlements=len(settlements),
        total_funding_paid_usd=total,
        settlements=settlements,
    )
