#!/usr/bin/env python3
"""
research/edge_factory/ctrend/ctrend_v0.py — Baseline cross-sectional trend
==========================================================================

Long-only top-K sur un score de tendance multi-horizon vol-ajusté,
rebalancement quotidien, filtre de régime BTC, coûts ×1 et ×2.

Voir README.md pour les limites assumées de la v0 (biais de survivance).

Usage:
  python research/edge_factory/ctrend/ctrend_v0.py             # top 40, 2 ans
  python research/edge_factory/ctrend/ctrend_v0.py --top 30 --years 2 --k 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from data_pipeline.http import PublicHTTPClient  # noqa: E402
from data_pipeline.derivatives_positioning import FAPI_BASE, fetch_universe  # noqa: E402
from data_pipeline.storage import read_partitioned_parquet, write_partitioned_parquet  # noqa: E402

SOURCE_KLINES = "binance_um_klines"
MARKET_TYPE = "futures_um"
KLINE_LIMIT = 1500

MOM_HOURS = [1, 4, 24, 72, 168]  # 1h, 4h, 24h, 3j, 7j
VOL_WINDOW_H = 168               # vol réalisée sur 7 j de barres 1 h


def fetch_klines_1h(client: PublicHTTPClient, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while cursor < end_ms:
        payload = client.get_json(
            f"{FAPI_BASE}/fapi/v1/klines",
            params={"symbol": symbol, "interval": "1h", "startTime": cursor,
                    "endTime": end_ms, "limit": KLINE_LIMIT},
        )
        if not payload:
            break
        rows.extend(payload)
        last_open = payload[-1][0]
        next_cursor = last_open + 3_600_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(payload) < KLINE_LIMIT:
            break
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "n_trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame[["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]]


def load_or_fetch_klines(client: PublicHTTPClient, root: Path, symbol: str,
                         start: datetime, end: datetime) -> pd.DataFrame:
    cached = read_partitioned_parquet(
        root, source=SOURCE_KLINES, market_type=MARKET_TYPE, symbol=symbol, interval="1h"
    )
    fetch_from = start
    if not cached.empty:
        last = cached["timestamp"].max()
        if last >= pd.Timestamp(end) - pd.Timedelta(hours=2):
            return cached
        fetch_from = max(start, last.to_pydatetime())
    fresh = fetch_klines_1h(client, symbol, fetch_from, end)
    if not fresh.empty:
        write_partitioned_parquet(
            fresh, root=root, source=SOURCE_KLINES, market_type=MARKET_TYPE,
            symbol=symbol, interval="1h", dedupe_keys=["symbol", "timestamp"],
        )
    if cached.empty:
        return fresh
    if fresh.empty:
        return cached
    merged = pd.concat([cached, fresh], ignore_index=True)
    return merged.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")


def build_panel(client: PublicHTTPClient, root: Path, symbols: list[str],
                start: datetime, end: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    closes, volumes = {}, {}
    for i, sym in enumerate(symbols, 1):
        df = load_or_fetch_klines(client, root, sym, start, end)
        if df.empty or len(df) < VOL_WINDOW_H * 2:
            print(f"  [{i}/{len(symbols)}] {sym}: historique insuffisant, exclu")
            continue
        s = df.set_index("timestamp")
        closes[sym] = s["close"]
        volumes[sym] = s["quote_volume"]
        print(f"  [{i}/{len(symbols)}] {sym}: {len(df)} barres 1h")
    return pd.DataFrame(closes).sort_index(), pd.DataFrame(volumes).sort_index()


def compute_scores(closes: pd.DataFrame) -> pd.DataFrame:
    """Score composite : momentum multi-horizon / vol réalisée, z-scoré en cross-section."""
    logp = np.log(closes)
    hourly_vol = logp.diff().rolling(VOL_WINDOW_H, min_periods=VOL_WINDOW_H // 2).std()
    zsum = None
    for h in MOM_HOURS:
        mom = logp.diff(h)
        scaled = mom / (hourly_vol * np.sqrt(h))
        z = scaled.sub(scaled.mean(axis=1), axis=0).div(scaled.std(axis=1), axis=0)
        zsum = z if zsum is None else zsum + z
    return zsum / len(MOM_HOURS)


def backtest(closes: pd.DataFrame, scores: pd.DataFrame, *, k: int, cost_bps_side: float,
             regime: pd.Series) -> dict:
    """Rebalancement quotidien 00:00 UTC. Retourne série de PnL quotidienne + stats."""
    daily_idx = closes.index[closes.index.hour == 0]
    daily_close = closes.loc[daily_idx]
    daily_ret = daily_close.pct_change().shift(-1)  # rendement du jour suivant la décision
    daily_scores = scores.loc[daily_idx]
    daily_regime = regime.reindex(daily_idx).fillna(False)

    weights = pd.DataFrame(0.0, index=daily_idx, columns=closes.columns)
    for ts in daily_idx:
        if not bool(daily_regime.loc[ts]):
            continue
        row = daily_scores.loc[ts].dropna()
        row = row[row > 0]
        if row.empty:
            continue
        top = row.nlargest(k)
        weights.loc[ts, top.index] = 1.0 / k

    turnover = weights.diff().abs().sum(axis=1).fillna(weights.iloc[0].abs().sum())
    gross = (weights * daily_ret).sum(axis=1)
    costs = turnover * (cost_bps_side / 10_000.0)
    net = (gross - costs).iloc[:-1]  # dernière ligne sans rendement forward

    equity = (1 + net).cumprod()
    ann = 365.0
    sharpe = float(net.mean() / net.std() * np.sqrt(ann)) if net.std() > 0 else 0.0
    dd = float((equity / equity.cummax() - 1).min())
    monthly = (1 + net).groupby([net.index.year, net.index.month]).prod() - 1
    return {
        "sharpe": round(sharpe, 2),
        "ann_return_pct": round(float((1 + net.mean()) ** ann - 1) * 100, 1),
        "max_drawdown_pct": round(dd * 100, 1),
        "avg_monthly_pct": round(float(monthly.mean()) * 100, 2),
        "pct_months_positive": round(float((monthly > 0).mean()) * 100, 0),
        "avg_turnover": round(float(turnover.mean()), 2),
        "exposure_pct": round(float((weights.sum(axis=1) > 0).mean()) * 100, 0),
        "n_days": int(len(net)),
        "monthly": {f"{y}-{m:02d}": round(float(v) * 100, 2) for (y, m), v in monthly.items()},
        "_net": net,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--cost-bps-side", type=float, default=6.0)
    parser.add_argument("--root", default=str(ROOT / "data" / "raw"))
    args = parser.parse_args()

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=int(args.years * 365) + VOL_WINDOW_H // 24 + 10)
    root = Path(args.root)
    client = PublicHTTPClient(rate_limit_per_minute=100)

    print(f"=== ctrend_v0 === univers top {args.top}, K={args.k}, "
          f"{start.date()} -> {end.date()}, coûts {args.cost_bps_side} bps/side")
    universe = fetch_universe(client, top_n=args.top)
    symbols = [s for s in universe["symbol"] if not s.startswith(("USDC", "FDUSD", "TUSD"))]

    closes, _volumes = build_panel(client, root, symbols, start, end)
    print(f"\nPanel: {closes.shape[1]} symboles x {closes.shape[0]} barres 1h")
    scores = compute_scores(closes)

    btc_daily = closes["BTCUSDT"][closes.index.hour == 0]
    regime = btc_daily > btc_daily.ewm(span=20).mean()

    results = {}
    for label, mult in (("costs_x1", 1.0), ("costs_x2", 2.0)):
        stats = backtest(closes, scores, k=args.k,
                         cost_bps_side=args.cost_bps_side * mult, regime=regime)
        stats.pop("_net")
        results[label] = stats

    btc_ret = btc_daily.pct_change().dropna()
    btc_tail = btc_ret.tail(results["costs_x1"]["n_days"])
    results["benchmark_btc"] = {
        "sharpe": round(float(btc_tail.mean() / btc_tail.std() * np.sqrt(365)), 2),
        "ann_return_pct": round(float((1 + btc_tail.mean()) ** 365 - 1) * 100, 1),
    }
    results["params"] = {
        "top": args.top, "k": args.k, "years": args.years,
        "cost_bps_side": args.cost_bps_side, "mom_hours": MOM_HOURS,
        "universe_date": str(end.date()), "survivorship_bias": True,
    }

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"ctrend_v0_{end.date()}.json"
    out_path.write_text(json.dumps(results, indent=2))

    print("\n=== Résultats ===")
    for label in ("costs_x1", "costs_x2", "benchmark_btc"):
        print(f"{label}: {json.dumps({k: v for k, v in results[label].items() if k != 'monthly'})}")
    print(f"\nSauvegardé: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
