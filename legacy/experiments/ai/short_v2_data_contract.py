"""
Short v2 data contract.

The v2 short pipeline is intentionally strict: it must not silently replace
derivatives context with OHLCV-only proxies. Funding, open interest,
long/short positioning and liquidation flow are part of the model contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd


class ShortV2DataContractError(RuntimeError):
    """Raised when a dataset cannot be used for SHORT v2 validation."""


OHLCV_REQUIRED: Dict[str, Sequence[str]] = {
    "open": ("Open", "open"),
    "high": ("High", "high"),
    "low": ("Low", "low"),
    "close": ("Close", "close"),
    "volume": ("Volume", "volume"),
}

DERIVATIVE_REQUIRED: Dict[str, Sequence[str]] = {
    "funding": (
        "funding_rate",
        "funding_rate_z_24",
        "funding_z_7d",
        "funding_z_30d",
    ),
    "open_interest": (
        "oi_sum",
        "sum_open_interest",
        "oihist_sumOpenInterest_z_24",
        "oi_z_1d",
        "oi_chg_60m",
    ),
    "long_short_ratio": (
        "global_long_short_ratio",
        "global_ls_longShortRatio_z_24",
        "top_trader_lsr",
        "lsr_z_1d",
    ),
    "taker_flow": (
        "taker_buy_sell_ratio",
        "taker_ls_buySellRatio_z_24",
        "taker_ls_imbalance",
        "taker_buy_ratio",
    ),
}

REAL_LIQUIDATION_COLUMNS: Sequence[str] = (
    "liq_long_usd",
    "liq_short_usd",
    "long_liquidation_usd",
    "short_liquidation_usd",
    "liquidation_long_usd",
    "liquidation_short_usd",
    "force_order_long_usd",
    "force_order_short_usd",
    "binance_liq_long_usd",
    "binance_liq_short_usd",
    "liquidations_long_usd",
    "liquidations_short_usd",
)

LIQUIDATION_PROXY_COLUMNS: Sequence[str] = (
    "liq_long_spike_12",
    "liq_long_spike_24",
    "local_liquidity_sweep_proxy",
    "liquidity_shock_proxy_20",
    "liquidity_score",
)


@dataclass
class ShortV2DataContractReport:
    """Structured result of the v2 data contract validation."""

    ok: bool
    missing_groups: List[str] = field(default_factory=list)
    using_liquidation_proxy: bool = False
    present_columns: Dict[str, List[str]] = field(default_factory=dict)
    message: str = ""


def _first_present(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _present(df: pd.DataFrame, candidates: Iterable[str]) -> List[str]:
    return [col for col in candidates if col in df.columns]


def validate_short_v2_data_contract(
    df: pd.DataFrame,
    *,
    require_liquidations: bool = True,
    allow_liquidation_proxy: bool = False,
) -> ShortV2DataContractReport:
    """
    Validate that a dataset can be used for SHORT v2.

    By default liquidation flow must be real. A caller may opt into
    ``allow_liquidation_proxy`` for research-only dry runs, but the report will
    flag that the result is not deployment-grade.
    """
    missing: List[str] = []
    present: Dict[str, List[str]] = {}

    for group, cols in OHLCV_REQUIRED.items():
        group_present = _present(df, cols)
        present[group] = group_present
        if not group_present:
            missing.append(group)

    for group, cols in DERIVATIVE_REQUIRED.items():
        group_present = _present(df, cols)
        present[group] = group_present
        if not group_present:
            missing.append(group)

    real_liq = _present(df, REAL_LIQUIDATION_COLUMNS)
    proxy_liq = _present(df, LIQUIDATION_PROXY_COLUMNS)
    present["real_liquidations"] = real_liq
    present["liquidation_proxies"] = proxy_liq

    using_proxy = False
    if not real_liq and allow_liquidation_proxy and proxy_liq:
        using_proxy = True
    if require_liquidations and not real_liq and not using_proxy:
        missing.append("real_liquidations")

    ok = not missing
    message = "ok"
    if missing:
        message = "missing required groups: " + ", ".join(missing)
    elif using_proxy:
        message = "research-only: liquidation proxies used instead of real liquidation flow"

    report = ShortV2DataContractReport(
        ok=ok,
        missing_groups=missing,
        using_liquidation_proxy=using_proxy,
        present_columns=present,
        message=message,
    )

    if not ok:
        raise ShortV2DataContractError(message)

    return report


def normalize_short_v2_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add compatibility aliases required by the SHORT v2 feature stack.

    The function only derives backward-looking features from the current or past
    bar. It does not compute labels or any forward-looking value.
    """
    df = df.copy()

    # Canonical OHLCV aliases.
    for canonical, candidates in OHLCV_REQUIRED.items():
        source = _first_present(df, candidates)
        if source is None:
            continue
        cap = canonical.capitalize() if canonical != "volume" else "Volume"
        if cap not in df.columns:
            df[cap] = pd.to_numeric(df[source], errors="coerce")
        if canonical not in df.columns:
            df[canonical] = pd.to_numeric(df[source], errors="coerce")

    close = pd.to_numeric(
        df["Close"] if "Close" in df.columns else df.get("close"),
        errors="coerce",
    )
    high = pd.to_numeric(
        df["High"] if "High" in df.columns else df.get("high"),
        errors="coerce",
    )
    low = pd.to_numeric(
        df["Low"] if "Low" in df.columns else df.get("low"),
        errors="coerce",
    )

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)

    with np.errstate(divide="ignore", invalid="ignore"):
        log_close = np.log(close.clip(lower=1e-12))

    for h in (4, 12, 24, 72, 168):
        col = f"mom_logret_{h}"
        if col not in df.columns:
            direct = f"log_return_{h}"
            if direct in df.columns:
                df[col] = pd.to_numeric(df[direct], errors="coerce")
            else:
                df[col] = log_close - log_close.shift(h)

    if "dist_vwap_pct" not in df.columns:
        for cand in ("distance_vwap", "distance_vwap_20", "short_term_vwap_distance"):
            if cand in df.columns:
                df["dist_vwap_pct"] = pd.to_numeric(df[cand], errors="coerce")
                break

    if "dist_vwap_pct" not in df.columns:
        typical = ((high + low + close) / 3.0).replace([np.inf, -np.inf], np.nan)
        vwap_num = (typical * df["Volume"]).rolling(24, min_periods=6).sum()
        vwap_den = df["Volume"].rolling(24, min_periods=6).sum().clip(lower=1e-12)
        vwap = vwap_num / vwap_den
        df["dist_vwap_pct"] = (close - vwap) / close.clip(lower=1e-12)

    if "above_vwap_4h" not in df.columns:
        df["above_vwap_4h"] = (
            (df["dist_vwap_pct"] > 0.0).astype(float).rolling(4, min_periods=1).mean()
        )

    if "rv_ratio_24_72" not in df.columns:
        ret = log_close.diff()
        rv24 = ret.rolling(24, min_periods=12).std()
        rv72 = ret.rolling(72, min_periods=24).std().clip(lower=1e-12)
        df["rv_ratio_24_72"] = rv24 / rv72

    if "ema_spread_50_200" not in df.columns:
        if "ema_50" in df.columns and "ema_200" in df.columns:
            df["ema_spread_50_200"] = (
                pd.to_numeric(df["ema_50"], errors="coerce")
                - pd.to_numeric(df["ema_200"], errors="coerce")
            ) / close.clip(lower=1e-12)
        else:
            ema50 = close.ewm(span=50, adjust=False).mean()
            ema200 = close.ewm(span=200, adjust=False).mean()
            df["ema_spread_50_200"] = (ema50 - ema200) / close.clip(lower=1e-12)

    if "dist_ema_50" not in df.columns:
        if "distance_ema_50" in df.columns:
            df["dist_ema_50"] = pd.to_numeric(df["distance_ema_50"], errors="coerce")
        else:
            ema50 = close.ewm(span=50, adjust=False).mean()
            df["dist_ema_50"] = (close - ema50) / close.clip(lower=1e-12)

    return df.replace([np.inf, -np.inf], np.nan)
