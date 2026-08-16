from __future__ import annotations

from typing import Dict, Mapping, Sequence

import numpy as np
import pandas as pd

from .common import ChunkedPlaneWriter, base_part_paths


def _load_core(base_tape: str) -> pd.DataFrame:
    frames = []
    for path in base_part_paths(base_tape):
        sample = pd.read_parquet(path)
        price_col = "price_fair_value" if "price_fair_value" in sample.columns else "fair_value"
        frames.append(sample[["asof_ns", "symbol", price_col]].rename(columns={price_col: "_price"}))
    frame = pd.concat(frames, ignore_index=True)
    frame["asof_ns"] = pd.to_numeric(frame["asof_ns"], errors="raise").astype("int64")
    frame["symbol"] = frame["symbol"].astype(str)
    frame["_price"] = pd.to_numeric(frame["_price"], errors="coerce")
    return frame


def _rolling_beta(asset: pd.Series, market: pd.Series, window: int) -> pd.Series:
    min_periods = max(20, int(window) // 5)
    cov = asset.rolling(window, min_periods=min_periods).cov(market)
    var = market.rolling(window, min_periods=min_periods).var(ddof=1)
    return (cov / var.where(var.abs() > 1e-18)).shift(1)


def build_cross_asset_plane(base_tape: str, out_dir: str, horizons_ms: Sequence[int] = (100, 500, 1000, 5000, 30000), beta_window_ms: int = 300000, chunk_rows: int = 50000) -> Mapping[str, object]:
    frame = _load_core(base_tape)
    times = np.sort(frame["asof_ns"].unique())
    if len(times) < 3:
        raise ValueError("insufficient timestamps")
    cadence_ms = int(round(float(np.median(np.diff(times))) / 1e6))
    if cadence_ms <= 0:
        raise ValueError("invalid cadence")
    wide = frame.pivot(index="asof_ns", columns="symbol", values="_price").sort_index()
    logp = np.log(wide.where(wide > 0))
    ret1 = logp.diff()
    market = ret1.mean(axis=1, skipna=True)
    beta_window = max(20, int(beta_window_ms // cadence_ms))
    btc_col = next((c for c in wide.columns if str(c).upper().startswith("BTC")), None)
    leader = ret1[btc_col] if btc_col is not None else market
    leader_beta = _rolling_beta(leader, market, beta_window)
    leader_innovation = leader - leader_beta * market

    output = frame[["asof_ns", "symbol"]].copy()
    output["cross_asset__available_ts_ns"] = output["asof_ns"].astype("int64")
    output["cross_asset__symbol_count"] = int(len(wide.columns))

    feature_by_symbol: Dict[str, pd.DataFrame] = {}
    for symbol in wide.columns:
        symbol_ret = ret1[symbol]
        beta = _rolling_beta(symbol_ret, market, beta_window)
        residual = symbol_ret - beta * market
        features = pd.DataFrame(index=wide.index)
        features["cross_asset__market_return_1"] = market * 1e4
        features["cross_asset__beta"] = beta
        features["cross_asset__residual"] = residual * 1e4
        features["cross_asset__leader_innovation"] = leader_innovation * 1e4
        for horizon_ms in horizons_ms:
            steps = max(1, int(round(float(horizon_ms) / cadence_ms)))
            features["cross_asset__return_%sms" % int(horizon_ms)] = (logp[symbol] - logp[symbol].shift(steps)) * 1e4
            features["cross_asset__leader_innovation_%sms" % int(horizon_ms)] = leader_innovation.rolling(steps, min_periods=1).sum() * 1e4
        feature_by_symbol[str(symbol)] = features

    pieces = []
    for symbol, group in output.groupby("symbol", sort=False):
        features = feature_by_symbol[str(symbol)].reindex(group["asof_ns"].to_numpy()).reset_index(drop=True)
        pieces.append(pd.concat([group.reset_index(drop=True), features], axis=1))
    enriched = pd.concat(pieces, ignore_index=True).sort_values(["asof_ns", "symbol"], kind="mergesort")

    writer = ChunkedPlaneWriter(out_dir, chunk_rows=chunk_rows)
    for row in enriched.to_dict(orient="records"):
        writer.append(row)
    return writer.close({"plane": "cross_asset", "cadence_ms": cadence_ms, "symbols": [str(x) for x in wide.columns], "symbol_count": int(len(wide.columns)), "beta_window_ms": int(beta_window_ms), "horizons_ms": [int(x) for x in horizons_ms]})
