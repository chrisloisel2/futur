"""
data_v2/events/schema.py
─────────────────────────────────────────────────────────────────────────────
The joined per-symbol feature frame every Event Scanner V1 detector reads,
per reports/EVENT_SCANNER_V1_PROTOCOL.md's "Input feature frame" table.
Not built yet against real Data V2 output (the four P0 backfills aren't
complete) -- this module only defines and validates the CONTRACT, so
detectors/labels/scanner can be written and tested against synthetic data
now, and pointed at the real join later without changing their code.
"""
from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = (
    "timestamp",
    "symbol",
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
    "residual_return_1h",
    "volume",
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
