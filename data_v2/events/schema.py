"""
data_v2/events/schema.py
─────────────────────────────────────────────────────────────────────────────
The joined per-symbol feature frame every Event Scanner V1 detector reads,
per reports/EVENT_SCANNER_V1_PROTOCOL.md's "Input feature frame" table.
Not built yet against real Data V2 output (the four P0 backfills aren't
complete) -- this module only defines and validates the CONTRACT, so
detectors/labels/scanner can be written and tested against synthetic data
now, and pointed at the real join later without changing their code.

Pre-unblinding fix (2026-08-10, review round 3): residual_return_1h alone
was not enough -- FORCED_FLOW_REVERSAL needs a genuine residual_return_15m
(an earlier version faked it as residual_return_1h / 4, flagged in its own
comment as a placeholder), and non-overlapping labels need a genuine
residual_logret_5m base increment (summing overlapping 1h-return samples
taken every 5m, as an earlier labels.py version did, inflates/distorts
expectancy -- see data_v2/events/labels.py). All three are now required,
built causally from real price + BTC/ETH betas (data_v2.events.residuals).
research_available_at is also required -- labels must start from a bar's
own causal availability, not its raw timestamp (see labels.py).

Pre-unblinding fix (2026-08-10, review round 4):
  - `open` added as required -- labels.py enters a position at the entry
    bar's OPEN (the fair tradeable price before that bar's own move), not
    its close, which would price the entry after part of the bar's own
    move already happened.
  - `liq_feed_available` added as required (bool, one entry per bar). The
    liquidation feed (Bybit/OKX declared liquidations) only exists from
    2026-07-04 onward per the protocol's own note -- before that, or for
    any bar where the feed was genuinely down, `liq_long_usd_5m`/
    `liq_short_usd_5m` are not "0 liquidations", they are "unknown". Without
    this column, a detector reading 0 there cannot tell a real quiet bar
    from a bar with no feed at all -- see detectors.py's DELEVERAGING
    liq_confirmed and FORCED_FLOW_REVERSAL's per-bar liq/flow fallback.
"""
from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = (
    "timestamp",
    "research_available_at",
    "symbol",
    "open",
    "close",
    "oi",
    "oi_delta_pct_1h",
    "aggressive_buy_usd",
    "aggressive_sell_usd",
    "signed_volume",
    "CVD",
    "funding_rate",
    "basis",
    "basis_z_1d",
    "basis_z_7d",
    "residual_logret_5m",
    "residual_return_15m",
    "residual_return_1h",
    "volume",
    "liq_feed_available",
)

OPTIONAL_COLUMNS = (
    "liq_long_usd_5m",
    "liq_short_usd_5m",
)


def validate_schema(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"feature frame missing required columns: {missing}")
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        raise ValueError("timestamp column must be datetime64")
    if not pd.api.types.is_datetime64_any_dtype(df["research_available_at"]):
        raise ValueError("research_available_at column must be datetime64")
