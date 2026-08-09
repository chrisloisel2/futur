"""Blacklist guard for the known data/enriched taker_buy_* placeholder.

Audit finding (2026-07-17, memory data_pitfalls_enriched_vision): in
data/enriched/*_1h_enriched.parquet, taker_buy_base_asset_volume and
taker_buy_quote_asset_volume were fabricated as exactly volume * 0.5 and
quote_asset_volume * 0.5 on ~every row (data_pipeline/normalization.py used
to synthesize this default when the real Binance field was absent). Any
"flow" feature built on top of that (taker_buy_ratio, taker_delta, CVD-like
deltas...) is a constant-0.5 / constant-0 signal, not real aggressor flow.

Real taker flow must come from Binance Vision klines/aggTrades taker fields
(data_v2 pipeline), never from this placeholder.
"""
from __future__ import annotations

import pandas as pd

PLACEHOLDER_RATIO = 0.5
PLACEHOLDER_TOLERANCE = 1e-6
PLACEHOLDER_ROW_FRACTION = 0.99
MIN_ROWS_FOR_DETECTION = 5


class PlaceholderTakerFlowError(ValueError):
    """Raised when taker_buy_* columns look like the fabricated data/enriched
    50/50 split instead of genuine aggressor-side flow."""


def _placeholder_fraction(value: pd.Series, total: pd.Series) -> float:
    value = pd.to_numeric(value, errors="coerce")
    total = pd.to_numeric(total, errors="coerce")
    valid = total.notna() & value.notna() & (total.abs() > 1e-9)
    if int(valid.sum()) < MIN_ROWS_FOR_DETECTION:
        return 0.0
    ratio = (value[valid] - total[valid] * PLACEHOLDER_RATIO).abs() / total[valid].abs()
    return float((ratio < PLACEHOLDER_TOLERANCE).mean())


def _constant_ratio_fraction(series: pd.Series, center: float) -> float:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if len(series) < MIN_ROWS_FOR_DETECTION:
        return 0.0
    return float(((series - center).abs() < PLACEHOLDER_TOLERANCE).mean())


def looks_like_placeholder_taker_flow(df: pd.DataFrame) -> bool:
    """True if taker_buy_* raw columns or their derived ratios match the
    known fabricated-50% pattern on ~all rows (see module docstring)."""
    fractions = []

    if {"taker_buy_base_asset_volume", "volume"}.issubset(df.columns):
        fractions.append(_placeholder_fraction(df["taker_buy_base_asset_volume"], df["volume"]))
    if {"taker_buy_quote_asset_volume", "quote_asset_volume"}.issubset(df.columns):
        fractions.append(_placeholder_fraction(df["taker_buy_quote_asset_volume"], df["quote_asset_volume"]))

    for ratio_col in ("taker_buy_ratio_base", "taker_buy_ratio_quote", "taker_buy_ratio"):
        if ratio_col in df.columns:
            fractions.append(_constant_ratio_fraction(df[ratio_col], PLACEHOLDER_RATIO))
    if "taker_delta" in df.columns:
        fractions.append(_constant_ratio_fraction(df["taker_delta"], 0.0))

    return any(frac >= PLACEHOLDER_ROW_FRACTION for frac in fractions)


def assert_no_placeholder_taker_flow(df: pd.DataFrame, *, context: str = "") -> None:
    """Raise PlaceholderTakerFlowError if df carries the fabricated 50/50
    taker_buy_* placeholder instead of real aggressor flow."""
    if looks_like_placeholder_taker_flow(df):
        where = f" ({context})" if context else ""
        raise PlaceholderTakerFlowError(
            "taker_buy_base_asset_volume / taker_buy_quote_asset_volume look like the "
            "known data/enriched placeholder: == volume or quote_asset_volume * 0.5 on "
            f"~every row{where}. This is fabricated data, not real aggressor flow "
            "(see memory data_pitfalls_enriched_vision). Use real taker fields from "
            "Binance Vision klines/aggTrades (data_v2) instead."
        )
