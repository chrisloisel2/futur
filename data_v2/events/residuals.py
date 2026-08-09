"""
data_v2/events/residuals.py
─────────────────────────────────────────────────────────────────────────────
Real, causal beta-hedged residual returns vs BTC/ETH, per reports/
EVENT_SCANNER_V1_PROTOCOL.md: "for every other symbol, a rolling causal
regression of 1h returns against BTC and ETH 1h returns ... gives beta_btc,
beta_eth; residual = actual return - (beta_btc*BTC return + beta_eth*ETH
return)."

Pre-unblinding fix (2026-08-10, review round 3): an earlier version of the
event detectors used residual_return_1h as if it already existed and faked
residual_return_15m as residual_return_1h/4 (flagged as a placeholder in
its own comment) -- neither was ever actually computed from price. This
module builds all three real series (5m/15m/1h) from close prices using
one shared set of causal 2-factor betas.

Betas are refit EVERY bar (not literally "daily, frozen intraday" as the
protocol's prose describes) -- both are causal (shift(1) guarantees beta_t
only uses data through t-1, never t), continuous refitting is the more
conservative/adaptive choice, not a leakage risk. A strict daily-refit-
freeze schedule is a possible future refinement, not required for
correctness; documented here rather than silently deviating from the
protocol's wording without a note.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"
BETA_WINDOW_DAYS = 60
BARS_PER_DAY = 288
BETA_WINDOW_BARS = BETA_WINDOW_DAYS * BARS_PER_DAY
BETA_MIN_PERIODS = 20  # small enough to be testable on short synthetic panels


def _log_return(close: pd.Series, bars: int) -> pd.Series:
    return np.log(close / close.shift(bars))


def _causal_2factor_betas(
    y: pd.Series, x1: pd.Series, x2: pd.Series, window_bars: int, min_periods: int
) -> tuple[pd.Series, pd.Series]:
    """Causal rolling 2-factor OLS (no intercept -- pure beta hedge):
    y ~ b1*x1 + b2*x2. shift(1) on all three inputs before the rolling sums
    so beta at t is built only from data through t-1."""
    x1h, x2h, yh = x1.shift(1), x2.shift(1), y.shift(1)
    s11 = (x1h * x1h).rolling(window_bars, min_periods=min_periods).sum()
    s22 = (x2h * x2h).rolling(window_bars, min_periods=min_periods).sum()
    s12 = (x1h * x2h).rolling(window_bars, min_periods=min_periods).sum()
    s1y = (x1h * yh).rolling(window_bars, min_periods=min_periods).sum()
    s2y = (x2h * yh).rolling(window_bars, min_periods=min_periods).sum()
    det = (s11 * s22 - s12 * s12).replace(0, np.nan)
    beta1 = (s1y * s22 - s2y * s12) / det
    beta2 = (s2y * s11 - s1y * s12) / det
    return beta1, beta2


def compute_residual_returns(
    close_by_symbol: Dict[str, pd.Series],
    *,
    window_bars: int = BETA_WINDOW_BARS,
    min_periods: int = BETA_MIN_PERIODS,
) -> Dict[str, pd.DataFrame]:
    """close_by_symbol: {symbol: close Series indexed by 5m timestamp},
    must include BTC_SYMBOL and ETH_SYMBOL. Returns {symbol: DataFrame}
    with residual_logret_5m/residual_return_15m/residual_return_1h columns,
    same index as the input close series.

    BTC/ETH themselves get residual == raw return (no benchmark to regress
    against, per protocol) at all three frequencies.
    """
    if BTC_SYMBOL not in close_by_symbol or ETH_SYMBOL not in close_by_symbol:
        raise ValueError(f"close_by_symbol must include {BTC_SYMBOL} and {ETH_SYMBOL}")

    btc_close = close_by_symbol[BTC_SYMBOL]
    eth_close = close_by_symbol[ETH_SYMBOL]
    btc_ret_5m = _log_return(btc_close, 1)
    eth_ret_5m = _log_return(eth_close, 1)
    btc_ret_15m = _log_return(btc_close, 3)
    eth_ret_15m = _log_return(eth_close, 3)
    btc_ret_1h = _log_return(btc_close, 12)
    eth_ret_1h = _log_return(eth_close, 12)

    out: Dict[str, pd.DataFrame] = {}
    for symbol, close in close_by_symbol.items():
        ret_5m = _log_return(close, 1)
        ret_15m = _log_return(close, 3)
        ret_1h = _log_return(close, 12)

        if symbol in (BTC_SYMBOL, ETH_SYMBOL):
            out[symbol] = pd.DataFrame({
                "residual_logret_5m": ret_5m,
                "residual_return_15m": ret_15m,
                "residual_return_1h": ret_1h,
            })
            continue

        beta_btc, beta_eth = _causal_2factor_betas(ret_1h, btc_ret_1h, eth_ret_1h, window_bars, min_periods)

        out[symbol] = pd.DataFrame({
            "residual_logret_5m": ret_5m - (beta_btc * btc_ret_5m + beta_eth * eth_ret_5m),
            "residual_return_15m": ret_15m - (beta_btc * btc_ret_15m + beta_eth * eth_ret_15m),
            "residual_return_1h": ret_1h - (beta_btc * btc_ret_1h + beta_eth * eth_ret_1h),
        })
    return out
