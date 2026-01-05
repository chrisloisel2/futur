"""
Minute-by-minute Backtester (Corrected)
- No intrabar lookahead TP/SL cheating
- Correct holding time logic (no hardcoded 60)
- Correct fee + slippage application on notional
- Minute-based Sharpe (annualized with 525,600)
- Optional daily Sharpe for reporting
- Deterministic and debuggable
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ----------------------------
# Core PnL calculator (OK)
# ----------------------------
def calculate_trade_pnl(
    side: str,
    entry_price: float,
    exit_price: float,
    position_size: float,
    fee_rate: float,
    slippage_bps: float,
) -> dict:
    """
    position_size: fraction of equity allocated (1.0 = 100% of equity)
    fee_rate: decimal (0.0004 = 4 bps)
    slippage_bps: bps (1.0 = 1 bp)
    """

    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"Invalid side: {side}")

    if entry_price <= 0 or exit_price <= 0:
        raise ValueError(f"Invalid prices entry={entry_price} exit={exit_price}")

    position_size = float(abs(position_size))
    if position_size <= 0:
        raise ValueError(f"Invalid position_size: {position_size}")

    # Gross pnl in % of equity (since position_size is fraction of equity)
    if side == "LONG":
        pnl_gross_pct = (exit_price / entry_price - 1.0) * position_size
    else:
        pnl_gross_pct = (entry_price / exit_price - 1.0) * position_size

    # Notional traded: entry + exit = 2 * position_size (in equity fraction terms)
    notional_traded = 2.0 * position_size

    # Fees & slippage apply on notional
    fee_cost_pct = notional_traded * float(fee_rate)
    slippage_rate = float(slippage_bps) / 10000.0
    slippage_cost_pct = notional_traded * slippage_rate

    pnl_net_pct = pnl_gross_pct - fee_cost_pct - slippage_cost_pct

    return {
        "pnl_gross_pct": pnl_gross_pct,
        "fee_cost_pct": fee_cost_pct,
        "slippage_cost_pct": slippage_cost_pct,
        "pnl_net_pct": pnl_net_pct,
        "notional_traded": notional_traded,
    }


# ----------------------------
# Utility: robust sharpe
# ----------------------------
def sharpe_ratio(returns: pd.Series, periods_per_year: float) -> float:
    if returns is None or len(returns) < 2:
        return 0.0
    mu = float(returns.mean())
    sd = float(returns.std(ddof=1))
    if not np.isfinite(mu) or not np.isfinite(sd) or sd <= 0:
        return 0.0
    return (mu / sd) * float(np.sqrt(periods_per_year))


def sortino_ratio(returns: pd.Series, periods_per_year: float) -> float:
    if returns is None or len(returns) < 2:
        return 0.0
    mu = float(returns.mean())
    neg = returns[returns < 0]
    if len(neg) < 2:
        # No downside volatility -> define as huge? Keep conservative:
        return 0.0
    dd = float(neg.std(ddof=1))
    if not np.isfinite(mu) or not np.isfinite(dd) or dd <= 0:
        return 0.0
    return (mu / dd) * float(np.sqrt(periods_per_year))


# ----------------------------
# Main backtest (bar-by-bar)
# ----------------------------
def backtest_strategy_minute(
    df: pd.DataFrame,
    predictions: pd.DataFrame,
    entry_threshold: float = 0.60,
    use_shorts: bool = True,
    fee_rate: float = 0.0004,
    slippage_bps: float = 1.0,
    position_mode: str = "binary",      # "binary" or "scaled"
    scale: float = 1.0,                 # used when position_mode="scaled"
    cooldown_bars: int = 60,            # RENAMED: number of bars to wait (not minutes!)
    holding_min_bars: int = 15,         # RENAMED: minimum holding period in bars
    holding_max_bars: int = 60,         # RENAMED: maximum holding period in bars
    min_edge: float = 0.05,
    tp_mode: str = "thresholds",        # "thresholds" (from cols) or "fixed"
    tp_fixed: float = 0.01,
    sl_fixed: float = 0.01,
    intrabar_policy: str = "pessimistic",  # "pessimistic" | "optimistic" | "skip"
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Correct bar-by-bar backtest with safe intrabar TP/SL handling.

    CRITICAL: All time parameters are in BARS not minutes!
      - cooldown_bars: Number of bars to wait after closing a position
      - holding_min_bars: Minimum holding period in bars
      - holding_max_bars: Maximum holding period in bars

    For 1m data: 60 bars = 60 minutes
    For 5m data: 60 bars = 300 minutes

    Expected df columns:
      - close, high, low
    predictions columns joined on df index:
      - p_hit_calibrated (or p_hit), q50
      - tp_threshold_used, sl_threshold_used (if tp_mode="thresholds")
    """

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df must have a DatetimeIndex for bar-by-bar backtesting.")

    df = df.copy()
    predictions = predictions.copy()
    df = df.join(predictions, how="left")

    required_cols = {"close", "high", "low"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Parameters
    periods_per_year_minute = 365.0 * 24.0 * 60.0  # 525,600

    # State
    equity = 1.0
    position = 0.0  # +fraction for long, -fraction for short
    entry_price = 0.0
    entry_time = None
    entry_i = None
    cooldown_until_i = -1
    prev_p_hit = 0.5

    # Logs
    equity_curve = []
    trades = []

    # Counters for debugging
    n_tp_sl_same_bar = 0
    n_exit_tp = 0
    n_exit_sl = 0
    n_exit_time = 0

    def _get_thresholds(row) -> tuple[float, float]:
        if tp_mode == "fixed":
            return float(tp_fixed), float(sl_fixed)

        tp = row.get("tp_threshold_used", np.nan)
        sl = row.get("sl_threshold_used", np.nan)
        if not np.isfinite(tp) or tp <= 0:
            tp = float(tp_fixed)
        if not np.isfinite(sl) or sl <= 0:
            sl = float(sl_fixed)
        return float(tp), float(sl)

    # Iterate minute-by-minute
    for i, (ts, row) in enumerate(df.iterrows()):
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])

        # Model signals
        p_hit = row.get("p_hit_calibrated", row.get("p_hit", 0.5))
        try:
            p_hit = float(p_hit) if np.isfinite(p_hit) else 0.5
        except Exception:
            p_hit = 0.5

        q50 = row.get("q50", 0.0)
        try:
            q50 = float(q50) if np.isfinite(q50) else 0.0
        except Exception:
            q50 = 0.0

        tp_thresh, sl_thresh = _get_thresholds(row)

        # ----------------------------
        # EXIT LOGIC
        # ----------------------------
        if position != 0.0 and entry_i is not None:
            holding_time = i - entry_i  # minutes since entry
            exit_reason = None
            pnl_pct_gross = 0.0

            # Evaluate intrabar hits for current bar ONLY
            if position > 0:  # LONG
                hit_tp = (high / entry_price - 1.0) >= tp_thresh
                hit_sl = (low / entry_price - 1.0) <= -sl_thresh
            else:  # SHORT
                # Profit on short if price goes down: entry/low - 1 >= tp
                hit_tp = (entry_price / low - 1.0) >= tp_thresh
                # CRITICAL FIX: Loss on short if price goes UP
                # Correct math: (high / entry_price - 1) >= sl_thresh (not entry/high)
                hit_sl = (high / entry_price - 1.0) >= sl_thresh

            # Enforce minimum holding time BEFORE allowing TP/SL
            # holding_time is in bars, holding_min_bars is in bars
            if holding_time >= holding_min_bars:
                if hit_tp and hit_sl:
                    n_tp_sl_same_bar += 1
                    if intrabar_policy == "skip":
                        # Do nothing: wait for next bar
                        pass
                    else:
                        # pessimistic: assume worst fill
                        # optimistic: assume best fill
                        if intrabar_policy == "pessimistic":
                            pnl_pct_gross = -sl_thresh
                            exit_reason = "SL_AND_TP_SAME_BAR"
                        elif intrabar_policy == "optimistic":
                            pnl_pct_gross = tp_thresh
                            exit_reason = "TP_AND_SL_SAME_BAR"
                        else:
                            raise ValueError(f"Invalid intrabar_policy: {intrabar_policy}")

                elif hit_tp:
                    pnl_pct_gross = tp_thresh
                    exit_reason = "TP"
                elif hit_sl:
                    pnl_pct_gross = -sl_thresh
                    exit_reason = "SL"

            # TIME EXIT: maximum holding time, also respects min holding time automatically
            # holding_time is in bars, holding_max_bars is in bars
            if exit_reason is None and holding_time >= holding_max_bars:
                # Exit at close of current bar
                if position > 0:
                    pnl_pct_gross = (close / entry_price) - 1.0
                else:
                    pnl_pct_gross = (entry_price / close) - 1.0
                exit_reason = "TIME"

            # Execute exit
            if exit_reason is not None:
                if exit_reason == "TP":
                    n_exit_tp += 1
                elif exit_reason == "SL":
                    n_exit_sl += 1
                elif exit_reason == "TIME":
                    n_exit_time += 1

                # Construct consistent exit_price from pnl_pct_gross
                if position > 0:
                    exit_price = entry_price * (1.0 + pnl_pct_gross)
                    side = "LONG"
                else:
                    exit_price = entry_price / (1.0 + pnl_pct_gross)
                    side = "SHORT"

                pnl_calc = calculate_trade_pnl(
                    side=side,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    position_size=abs(position),
                    fee_rate=fee_rate,
                    slippage_bps=slippage_bps,
                )

                equity *= (1.0 + pnl_calc["pnl_net_pct"])

                trades.append(
                    {
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "side": side,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "position_size": abs(position),
                        "pnl_gross_pct": pnl_calc["pnl_gross_pct"],
                        "fee_cost_pct": pnl_calc["fee_cost_pct"],
                        "slippage_cost_pct": pnl_calc["slippage_cost_pct"],
                        "pnl_net_pct": pnl_calc["pnl_net_pct"],
                        "notional_traded": pnl_calc["notional_traded"],
                        "reason": exit_reason,
                        "holding_bars": holding_time,  # RENAMED: this is bars not minutes!
                        "tp_thresh": tp_thresh,
                        "sl_thresh": sl_thresh,
                        "p_hit": p_hit,
                        "q50": q50,
                    }
                )

                # Reset state
                position = 0.0
                entry_price = 0.0
                entry_time = None
                entry_i = None
                cooldown_until_i = i + int(cooldown_bars)

        # ----------------------------
        # ENTRY LOGIC
        # ----------------------------
        if position == 0.0 and i > cooldown_until_i:
            crossed_up = (prev_p_hit < entry_threshold) and (p_hit >= entry_threshold)
            crossed_down = (prev_p_hit > (1.0 - entry_threshold)) and (p_hit <= (1.0 - entry_threshold))

            signal = 0
            if crossed_up and q50 > 0:
                signal = 1
            elif use_shorts and crossed_down and q50 < 0:
                signal = -1

            if signal != 0:
                edge = abs(p_hit - 0.5)
                if edge >= min_edge:
                    if position_mode == "binary":
                        position = float(signal)  # full allocation
                    elif position_mode == "scaled":
                        raw = (p_hit - 0.5) * float(scale)
                        if signal > 0:
                            position = float(np.clip(raw, 0.0, 1.0))
                        else:
                            position = float(np.clip(raw, -1.0, 0.0))
                    else:
                        raise ValueError(f"Invalid position_mode: {position_mode}")

                    if abs(position) > 0.01:
                        entry_price = close
                        entry_time = ts
                        entry_i = i

        prev_p_hit = p_hit

        # Equity curve (minute)
        equity_curve.append(
            {
                "time": ts,
                "equity": equity,
                "position": position,
                "p_hit": p_hit,
                "q50": q50,
            }
        )

    equity_df = pd.DataFrame(equity_curve).set_index("time")
    trades_df = pd.DataFrame(trades)

    # ----------------------------
    # METRICS
    # ----------------------------
    if len(trades_df) == 0:
        metrics = {
            "n_trades": 0,
            "roi": 0.0,
            "sharpe_1m": 0.0,
            "sortino_1m": 0.0,
            "sharpe_daily": 0.0,
            "sortino_daily": 0.0,
            "max_dd": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_trade_pct": 0.0,
            "avg_trade_gross_pct": 0.0,
            "turnover_per_day": 0.0,
            "exposure": float((equity_df["position"] != 0).mean()) if len(equity_df) else 0.0,
            "total_fees_pct": 0.0,
            "total_slippage_pct": 0.0,
            "avg_notional_per_trade": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "tp_sl_same_bar": 0,
            "exit_tp": 0,
            "exit_sl": 0,
            "exit_time": 0,
        }
        return equity_df, trades_df, metrics

    roi = float(equity_df["equity"].iloc[-1] - 1.0)

    # Minute returns
    rets_1m = equity_df["equity"].pct_change().dropna()
    sharpe_1m = sharpe_ratio(rets_1m, periods_per_year_minute)
    sortino_1m = sortino_ratio(rets_1m, periods_per_year_minute)

    # Daily returns (reporting)
    daily_equity = equity_df["equity"].resample("D").last()
    daily_rets = daily_equity.pct_change().dropna()
    sharpe_daily = sharpe_ratio(daily_rets, 365.0)
    sortino_daily = sortino_ratio(daily_rets, 365.0)

    # Costs
    total_fees_pct = float(trades_df["fee_cost_pct"].sum())
    total_slippage_pct = float(trades_df["slippage_cost_pct"].sum())

    # Other trade metrics
    win_rate = float((trades_df["pnl_net_pct"] > 0).mean())
    gross_profit = float(trades_df.loc[trades_df["pnl_net_pct"] > 0, "pnl_net_pct"].sum())
    gross_loss = float(abs(trades_df.loc[trades_df["pnl_net_pct"] < 0, "pnl_net_pct"].sum()))
    profit_factor = gross_profit / max(gross_loss, 1e-12)

    # Turnover/day (approx)
    n_days = max(int((equity_df.index[-1].date() - equity_df.index[0].date()).days), 1)
    turnover_per_day = float(len(trades_df) / n_days)

    exposure = float((equity_df["position"] != 0).mean())

    # Drawdown
    dd = equity_df["equity"] / equity_df["equity"].cummax() - 1.0
    max_dd = float(dd.min())

    metrics = {
        "n_trades": int(len(trades_df)),
        "roi": roi,

        # Minute-based risk metrics (THIS is the correct one for your approach)
        "sharpe_1m": float(sharpe_1m),
        "sortino_1m": float(sortino_1m),

        # Daily reporting (optional)
        "sharpe_daily": float(sharpe_daily),
        "sortino_daily": float(sortino_daily),

        "max_dd": max_dd,
        "win_rate": win_rate,
        "profit_factor": float(profit_factor),

        "avg_trade_pct": float(trades_df["pnl_net_pct"].mean()),
        "avg_trade_gross_pct": float(trades_df["pnl_gross_pct"].mean()),

        "turnover_per_day": turnover_per_day,
        "exposure": exposure,

        "total_fees_pct": total_fees_pct,
        "total_slippage_pct": total_slippage_pct,
        "avg_notional_per_trade": float(trades_df["notional_traded"].mean()),

        "long_trades": int((trades_df["side"] == "LONG").sum()),
        "short_trades": int((trades_df["side"] == "SHORT").sum()),

        # Debug counters
        "tp_sl_same_bar": int(n_tp_sl_same_bar),
        "exit_tp": int(n_exit_tp),
        "exit_sl": int(n_exit_sl),
        "exit_time": int(n_exit_time),

        # Sanity: raw mean/std of minute returns (for your minimal test)
        "ret_1m_mean": float(rets_1m.mean()) if len(rets_1m) else 0.0,
        "ret_1m_std": float(rets_1m.std(ddof=1)) if len(rets_1m) > 1 else 0.0,
        "ret_1m_sharpe_raw": float(rets_1m.mean() / max(rets_1m.std(ddof=1), 1e-12)) if len(rets_1m) > 1 else 0.0,
    }

    return equity_df, trades_df, metrics


# ----------------------------
# Example usage (remove in prod)
# ----------------------------
if __name__ == "__main__":
    # df must be minute-indexed OHLCV with DatetimeIndex
    # predictions must align on df.index
    pass
