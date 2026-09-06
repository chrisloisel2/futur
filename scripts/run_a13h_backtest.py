#!/usr/bin/env python3
"""A13-H backtest: H1/H2/H3 exactly as preregistered in docs/A13H_PREREGISTRATION.md.

Do not change hypothesis definitions, deciles, cost model, or regime cuts here
in response to results -- if a change is needed, it goes in the
preregistration doc first, as a new, dated addendum, not a silent edit.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_a13h_panel import DEFAULT_OHLCV_ROOT, _load_symbol_hourly, rolling_causal_beta

sys.path.insert(0, str(ROOT.parent))
from market_physics_v3.phase5_2_execution_economics import TAKER_FEE_BPS

FEE_BPS_PER_SIDE = TAKER_FEE_BPS["binance"]
HORIZONS_H = (1, 4, 12, 24, 72)
MIN_UNIVERSE = 30
DECILE_FRACTION = 0.10
BETA_TO_BTC_WINDOW_H = 720
REGIME_BOUNDS = [
    ("2019-2020", "2019-01-01", "2021-01-01"),
    ("2021", "2021-01-01", "2022-01-01"),
    ("2022", "2022-01-01", "2023-01-01"),
    ("2023", "2023-01-01", "2024-01-01"),
    ("2024", "2024-01-01", "2025-01-01"),
    ("2025", "2025-01-01", "2026-01-01"),
    ("2026", "2026-01-01", "2027-01-01"),
]


def _spread_bps(adv_usd: float) -> float:
    if not np.isfinite(adv_usd):
        return 15.0
    if adv_usd >= 500_000_000:
        return 1.0
    if adv_usd >= 50_000_000:
        return 5.0
    return 15.0


def _capacity_usd(adv_usd: float) -> float:
    return 0.01 * adv_usd if np.isfinite(adv_usd) else 0.0


def _regime_for(ts: pd.Timestamp) -> str | None:
    for name, start, end in REGIME_BOUNDS:
        if pd.Timestamp(start, tz="UTC") <= ts < pd.Timestamp(end, tz="UTC"):
            return name
    return None


def _load_wide(panel_dir: str) -> dict[str, pd.DataFrame]:
    files = sorted(glob.glob(f"{panel_dir}/part-*.parquet"))
    frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    frame["asof"] = pd.to_datetime(frame["asof_ns"], utc=True)
    out = {}
    for col in ("residual", "ret_1h", "trailing_daily_quote_volume_usd", "universe_size"):
        out[col] = frame.pivot(index="asof", columns="symbol", values=col).sort_index()
    return out


def _load_raw_close(symbols: list[str], full_index: pd.DatetimeIndex, ohlcv_root: str = DEFAULT_OHLCV_ROOT) -> pd.DataFrame:
    # Deliberately NOT the panel's own `close` column: that one only carries a
    # value where eligible=True, and a decile member that stops being eligible
    # mid-horizon (delisted, liquidity dried up) would silently drop out of the
    # portfolio-return sum instead of realizing whatever actually happened to
    # its price -- an optimistic bias around exactly the kind of event (price
    # collapse -> delisting) this backtest most needs to not look away from.
    # Trade *decisions* still come from the eligibility-gated panel; trade
    # *outcomes* come from raw price reality regardless of later eligibility.
    series = {}
    for symbol in symbols:
        loaded = _load_symbol_hourly(symbol, ohlcv_root)
        if loaded is not None:
            series[symbol] = loaded["close"].reindex(full_index)
    return pd.DataFrame(series)


def _forward_cum_return(close: pd.DataFrame, t_idx: int, h_idx: int) -> pd.Series:
    start = close.iloc[t_idx]
    end = close.iloc[t_idx + h_idx]
    return np.log(end / start)


def _decile_members(residual_row: pd.Series, n: int) -> tuple[pd.Index, pd.Index]:
    ranked = residual_row.dropna().sort_values()
    bottom = ranked.index[:n]
    top = ranked.index[-n:]
    return bottom, top


def _leg_weights(symbols: pd.Index, gross_per_leg: float) -> pd.Series:
    if len(symbols) == 0:
        return pd.Series(dtype=float)
    return pd.Series(gross_per_leg / len(symbols), index=symbols)


def run_horizon(data: dict[str, pd.DataFrame], close: pd.DataFrame, beta_to_btc: pd.DataFrame, horizon_h: int) -> pd.DataFrame:
    residual, adv, universe_size = (
        data["residual"], data["trailing_daily_quote_volume_usd"], data["universe_size"],
    )
    index = residual.index
    step_hours = (index[1] - index[0]).total_seconds() / 3600.0
    step = round(horizon_h / step_hours)
    records = []
    t_idx = 0
    while t_idx + step < len(index):
        t = index[t_idx]
        row = residual.iloc[t_idx]
        n_universe = int(universe_size.iloc[t_idx].max()) if not universe_size.iloc[t_idx].isna().all() else 0
        if n_universe < MIN_UNIVERSE:
            t_idx += step
            continue
        n_decile = max(1, round(n_universe * DECILE_FRACTION))
        bottom, top = _decile_members(row, n_decile)
        if len(bottom) == 0 or len(top) == 0:
            t_idx += step
            continue

        fwd = _forward_cum_return(close, t_idx, step)
        adv_row = adv.iloc[t_idx]
        beta_row = beta_to_btc.iloc[t_idx]

        for hyp, long_syms, short_syms in (("H1", bottom, top), ("H2", top, bottom)):
            long_w = _leg_weights(long_syms, 0.5)
            short_w = _leg_weights(short_syms, -0.5)
            weights = pd.concat([long_w, short_w])
            net_beta = float((weights * beta_row.reindex(weights.index)).sum())
            hedge_w = -net_beta
            btc_fwd = fwd.get("BTCUSDT", np.nan)

            gross_ret = float((weights * fwd.reindex(weights.index)).sum())
            if np.isfinite(btc_fwd):
                gross_ret += hedge_w * btc_fwd
            gross_bps = gross_ret * 1e4

            turnover = float(weights.abs().sum()) + abs(hedge_w)
            spread_bps_series = adv_row.reindex(weights.index).apply(_spread_bps)
            spread_cost_bps = float((weights.abs() * 2.0 * spread_bps_series).sum())
            fee_cost_bps = turnover * 2.0 * FEE_BPS_PER_SIDE
            net_bps = gross_bps - fee_cost_bps - spread_cost_bps

            cap_series = adv_row.reindex(weights.index).apply(_capacity_usd)
            capacity_usd = float(cap_series.min()) if len(cap_series) else 0.0

            spread_t = float(row[top].mean() - row[bottom].mean())

            records.append({
                "asof": t, "regime": _regime_for(t), "horizon_h": horizon_h, "hypothesis": hyp,
                "n_universe": n_universe, "n_decile": n_decile,
                "gross_bps": gross_bps, "fee_bps": fee_cost_bps, "spread_bps": spread_cost_bps,
                "net_bps": net_bps, "turnover": turnover, "capacity_usd": capacity_usd,
                "spread_t": spread_t,
            })
        t_idx += step
    return pd.DataFrame.from_records(records)


def summarize(records: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (hyp, h, regime), g in records.groupby(["hypothesis", "horizon_h", "regime"], dropna=False):
        if regime is None or len(g) < 5:
            continue
        net = g["net_bps"].to_numpy()
        gross = g["gross_bps"].to_numpy()
        wins = net[net > 0].sum()
        losses = -net[net < 0].sum()
        pf = float(wins / losses) if losses > 0 else float("inf") if wins > 0 else float("nan")
        sharpe = float(net.mean() / net.std(ddof=1)) if net.std(ddof=1) > 0 else float("nan")
        periods_per_year = (365.0 * 24.0) / h
        sharpe_annualized = sharpe * np.sqrt(periods_per_year) if np.isfinite(sharpe) else float("nan")
        cum = np.cumsum(net)
        running_max = np.maximum.accumulate(cum)
        max_dd_bps = float((running_max - cum).max())
        mean_turnover = float(g["turnover"].mean())
        rows.append({
            "hypothesis": hyp, "horizon_h": h, "regime": regime, "n_trades": len(g),
            "gross_bps_mean": float(gross.mean()), "fee_bps_mean": float(g["fee_bps"].mean()),
            "spread_bps_mean": float(g["spread_bps"].mean()), "net_bps_mean": float(net.mean()),
            "turnover_mean": mean_turnover, "pf": pf, "sharpe_annualized": sharpe_annualized,
            "max_dd_bps": max_dd_bps, "capacity_usd_median": float(g["capacity_usd"].median()),
            "edge_over_turnover": float(net.mean() / mean_turnover) if mean_turnover > 0 else float("nan"),
        })
    return pd.DataFrame.from_records(rows).sort_values(["hypothesis", "horizon_h", "regime"])


def h3_summary(records: pd.DataFrame) -> pd.DataFrame:
    h1 = records[records["hypothesis"] == "H1"].dropna(subset=["regime"])
    rows = []
    for h, g in h1.groupby("horizon_h"):
        if len(g) < 10:
            continue
        ic = float(g["spread_t"].corr(g["net_bps"], method="spearman"))
        terciles = pd.qcut(g["spread_t"], 3, labels=["low", "mid", "high"], duplicates="drop")
        tercile_means = g.groupby(terciles, observed=True)["net_bps"].mean().to_dict()
        rows.append({"horizon_h": h, "n": len(g), "ic_spread_vs_h1_net_bps": ic, **{f"net_bps_{k}_spread_tercile": v for k, v in tercile_means.items()}})
    return pd.DataFrame.from_records(rows).sort_values("horizon_h")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="data/alpha_foundry_v5/a13h_panel")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data = _load_wide(args.panel)
    print(f"[a13h-backtest] loaded panel: {data['residual'].shape}", flush=True)

    btc_ret = data["ret_1h"]["BTCUSDT"]
    beta_to_btc = rolling_causal_beta(data["ret_1h"], btc_ret, BETA_TO_BTC_WINDOW_H)
    print("[a13h-backtest] beta-to-BTC computed", flush=True)

    symbols = sorted(data["residual"].columns)
    close = _load_raw_close(symbols, data["residual"].index)
    print(f"[a13h-backtest] raw close loaded for {close.notna().any().sum()}/{len(symbols)} symbols", flush=True)

    all_records = []
    for h in HORIZONS_H:
        print(f"[a13h-backtest] horizon={h}h", flush=True)
        recs = run_horizon(data, close, beta_to_btc, h)
        all_records.append(recs)
    records = pd.concat(all_records, ignore_index=True)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    records.to_parquet(out_dir / "RECORDS.parquet", index=False)

    summary = summarize(records)
    summary.to_csv(out_dir / "SUMMARY_BY_REGIME.csv", index=False)

    h3 = h3_summary(records)
    h3.to_csv(out_dir / "H3_SUMMARY.csv", index=False)

    pooled = summarize(records.assign(regime="ALL"))
    pooled.to_csv(out_dir / "SUMMARY_POOLED.csv", index=False)

    print(json.dumps({"n_records": len(records), "n_summary_rows": len(summary)}, indent=2), flush=True)
    print(summary.to_string(), flush=True)
    print(h3.to_string(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
