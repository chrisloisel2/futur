#!/usr/bin/env python3
"""
research/edge_factory/cross_sectional_momentum_v1/backtest_momentum_crypto_v1.py
─────────────────────────────────────────────────────────────────────────────
MOMENTUM_CRYPTO_V1 — étapes 4-7 (edge événementiel, construction du
portefeuille, walk-forward, gates) sur l'univers crypto-only (32 noms
classés, BTC réservé au hedge bêta, TONUSDT exclu car SETTLING — voir
DATA_INVENTORY.yaml, update_2026-07-21_universe_split).

Formule UNIQUE préenregistrée (aucune grille, n_trials=1) — voir
PREREGISTRATION_CRYPTO_V1_ADDENDUM.md :

    score_i = 0.4*resid_mom7_i + 0.4*resid_mom30_i + 0.2*resid_mom90_i
              - illiq_penalty_i - funding_cost_i

Long top 20% / short bottom 20% (equal-count), pondération inverse-vol,
cap 15% par nom, hedge BTC explicite pour bêta net de portefeuille ~0,
rebalance quotidien, décision en clôture t exécutée à t+1 (délai d'une
barre, jamais de lookahead).

    .venv/bin/python research/edge_factory/cross_sectional_momentum_v1/backtest_momentum_crypto_v1.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from research.edge_factory.multileg_engine.backtest_result import (  # noqa: E402
    MultiLegBacktestResult)
from src.alpha20.validation.promotion_gate import deflated_sharpe_ratio  # noqa: E402

PRICE_DIR = ROOT / "data" / "derivatives_backfill" / "um_klines_1d"
FUNDING_DIR = ROOT / "data" / "derivatives_backfill" / "binance" / "funding"

# univers vérifié via Binance exchangeInfo (underlyingType=COIN) le 2026-07-21.
# TONUSDT exclu (status=SETTLING, pas "réellement tradable"). BTCUSDT tenu à
# part : instrument de hedge bêta, jamais un candidat classé.
CRYPTO_32 = [
    "1000PEPEUSDT", "AAVEUSDT", "ADAUSDT", "ALLOUSDT", "AVAXUSDT", "BCHUSDT",
    "BEATUSDT", "BNBUSDT", "BTWUSDT", "DOGEUSDT", "DOTUSDT", "ENAUSDT",
    "ETHUSDT", "FILUSDT", "HUSDT", "HYPEUSDT", "LABUSDT", "LINKUSDT",
    "LTCUSDT", "NEARUSDT", "ONDOUSDT", "PAXGUSDT", "SOLUSDT", "SUIUSDT",
    "TAOUSDT", "UNIUSDT", "VELVETUSDT", "WLDUSDT", "XLMUSDT", "XRPUSDT",
    "ZECUSDT",
]
BTC = "BTCUSDT"

BETA_WINDOW = 90
VOL_WINDOW = 30
LOOKBACK_WEIGHTS = {7: 0.4, 30: 0.4, 90: 0.2}
LONG_SHORT_FRAC = 0.20
MAX_WEIGHT_PER_NAME = 0.15
MIN_HISTORY_DAYS = 120
FEE_BP = 5.0          # binance_usdm taker, assumed (fee_registry)
SLIPPAGE_BP = 2.0     # configs/alpha20.yaml costs.assumed_defaults.slippage_bp_default


def load_close(symbol: str) -> pd.Series:
    df = pd.read_parquet(PRICE_DIR / f"{symbol}_1d.parquet", columns=["open_time", "close"])
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True).dt.normalize()
    return df.set_index("open_time")["close"].rename(symbol)


def load_quote_volume(symbol: str) -> pd.Series:
    df = pd.read_parquet(PRICE_DIR / f"{symbol}_1d.parquet", columns=["open_time", "quote_volume"])
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True).dt.normalize()
    return df.set_index("open_time")["quote_volume"].rename(symbol)


def load_daily_funding(symbol: str) -> pd.Series:
    path = FUNDING_DIR / f"{symbol}.parquet"
    if not path.exists():
        return pd.Series(dtype=float, name=symbol)
    df = pd.read_parquet(path, columns=["timestamp", "funding_rate"])
    df["date"] = pd.to_datetime(df["timestamp"], utc=True).dt.normalize()
    daily = df.groupby("date")["funding_rate"].sum()
    daily.name = symbol
    return daily


def max_drawdown(returns: pd.Series) -> float:
    equity = (1.0 + returns.fillna(0)).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def cagr(returns: pd.Series) -> float:
    r = returns.dropna()
    if not len(r):
        return float("nan")
    equity = (1.0 + r).cumprod()
    n_years = len(r) / 365.25
    return float(equity.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else float("nan")


def sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    if not len(r) or r.std() == 0:
        return float("nan")
    return float(r.mean() / r.std() * np.sqrt(365.0))


def leave_one_year(returns: pd.Series) -> dict:
    out = {}
    for y in sorted(returns.index.year.unique()):
        remainder = returns[returns.index.year != y]
        out[str(y)] = cagr(remainder)
    return out


def main() -> None:
    universe = CRYPTO_32 + [BTC]
    close = pd.concat([load_close(s) for s in universe], axis=1).sort_index()
    qvol = pd.concat([load_quote_volume(s) for s in universe], axis=1).sort_index()
    funding = pd.concat([load_daily_funding(s) for s in universe], axis=1).sort_index()
    funding = funding.reindex(close.index).fillna(0.0)

    ret = close.pct_change()
    ret_btc = ret[BTC]

    cov = ret.rolling(BETA_WINDOW).cov(ret_btc)
    var_btc = ret_btc.rolling(BETA_WINDOW).var()
    beta = cov.div(var_btc, axis=0)

    resid_mom_sum = None
    for lb, w in LOOKBACK_WEIGHTS.items():
        mom = close.pct_change(lb)
        resid = mom.sub(beta.mul(mom[BTC], axis=0))
        resid_mom_sum = resid * w if resid_mom_sum is None else resid_mom_sum + resid * w

    vol30 = ret.rolling(VOL_WINDOW).std()

    log_qvol30 = np.log(qvol.rolling(VOL_WINDOW).median().clip(lower=1.0))
    illiq_penalty = log_qvol30.sub(log_qvol30.mean(axis=1), axis=0).div(
        log_qvol30.std(axis=1), axis=0) * -1.0

    funding_cost = funding.rolling(VOL_WINDOW).mean().abs() * 365.0

    score_full = resid_mom_sum - illiq_penalty - funding_cost
    score = score_full[CRYPTO_32]

    hist_ok = (close[CRYPTO_32].notna()
              .rolling(MIN_HISTORY_DAYS, min_periods=MIN_HISTORY_DAYS).count()
              >= MIN_HISTORY_DAYS)
    eligible_score = score.where(hist_ok)

    n_names = eligible_score.notna().sum(axis=1)
    n_per_leg = (n_names * LONG_SHORT_FRAC).apply(np.floor).clip(lower=1)

    rank_desc = eligible_score.rank(axis=1, ascending=False, method="first")
    rank_asc = eligible_score.rank(axis=1, ascending=True, method="first")
    is_long = rank_desc.le(n_per_leg, axis=0) & eligible_score.notna()
    is_short = rank_asc.le(n_per_leg, axis=0) & eligible_score.notna()

    inv_vol = 1.0 / vol30[CRYPTO_32].clip(lower=1e-6)

    def normalize_capped(raw_w: pd.DataFrame) -> pd.DataFrame:
        w = raw_w.div(raw_w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        w = w.clip(upper=MAX_WEIGHT_PER_NAME)
        w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        return w

    long_w = normalize_capped(inv_vol.where(is_long, 0.0))
    short_w = normalize_capped(inv_vol.where(is_short, 0.0))
    signed_w = long_w - short_w

    portfolio_beta = (signed_w * beta[CRYPTO_32]).sum(axis=1)
    btc_hedge_w = -portfolio_beta

    signed_w_lag = signed_w.shift(1).fillna(0.0)
    btc_hedge_w_lag = btc_hedge_w.shift(1).fillna(0.0)

    asset_gross = signed_w_lag * ret[CRYPTO_32]
    asset_funding = -signed_w_lag * funding[CRYPTO_32]
    gross_ret = asset_gross.sum(axis=1) + btc_hedge_w_lag * ret_btc
    funding_pnl = asset_funding.sum(axis=1) - btc_hedge_w_lag * funding[BTC]

    turnover = ((signed_w.fillna(0) - signed_w.shift(1).fillna(0)).abs().sum(axis=1)
               + (btc_hedge_w.fillna(0) - btc_hedge_w.shift(1).fillna(0)).abs())
    cost_x1 = -(turnover * (FEE_BP + SLIPPAGE_BP) / 10_000.0)
    cost_x2 = -(turnover * (FEE_BP + SLIPPAGE_BP) * 2 / 10_000.0)

    net_x1 = (gross_ret + funding_pnl + cost_x1)
    net_x2 = (gross_ret + funding_pnl + cost_x2)
    net_x1 = net_x1[hist_ok.any(axis=1)].dropna()
    net_x2 = net_x2.reindex(net_x1.index)

    asset_net = (asset_gross + asset_funding).reindex(net_x1.index)
    per_asset_total = asset_net.sum(axis=0)
    per_asset_total["BTC_hedge"] = float(
        (btc_hedge_w_lag * ret_btc - btc_hedge_w_lag * funding[BTC]).reindex(net_x1.index).sum())
    total_abs = per_asset_total.abs().sum()
    concentration = float(per_asset_total.abs().max() / total_abs) if total_abs else float("nan")
    top_contributor = str(per_asset_total.abs().idxmax())

    per_year = {str(y): cagr(g) for y, g in net_x1.groupby(net_x1.index.year)}
    loy = leave_one_year(net_x1)

    result = MultiLegBacktestResult(
        trades=pd.DataFrame({"net_x1": net_x1, "net_x2": net_x2}),
        pnl_daily=net_x1,
        per_year={str(y): float(net_x1[net_x1.index.year == y].sum())
                 for y in net_x1.index.year.unique()},
        net_events=net_x1, net_events_x2=net_x2,
        returns_for_dsr=net_x1,
        trials_matrix=None,   # une seule formule préenregistrée, aucune grille testée
        meta={"universe": CRYPTO_32, "hedge": BTC, "excluded": ["TONUSDT"],
             "n_trials": 1, "params_reused_from": None})

    sleeve_gates = result.run_sleeve_gate()
    research_gates = result.run_research_gate(n_trials=1)
    dsr_direct = deflated_sharpe_ratio(net_x1, n_trials=1)

    user_gates = {
        "cagr_net_x1_pct": {"value": cagr(net_x1) * 100, "threshold": 12.0, "passed": cagr(net_x1) * 100 > 12.0},
        "sharpe_x1": {"value": sharpe(net_x1), "threshold": 1.2, "passed": sharpe(net_x1) > 1.2},
        "max_dd_pct": {"value": max_drawdown(net_x1) * 100, "threshold": -15.0,
                      "passed": max_drawdown(net_x1) * 100 > -15.0},
        "costs_x2_positive": {"value": cagr(net_x2) * 100, "threshold": 0.0, "passed": cagr(net_x2) > 0.0},
        "leave_one_year_positive": {"value": {k: v * 100 for k, v in loy.items()},
                                    "passed": all(v > 0 for v in loy.values())},
        "max_asset_concentration_pct": {"value": concentration * 100, "threshold": 15.0,
                                        "passed": concentration <= 0.15, "top_contributor": top_contributor},
        "dsr_positive": {"value": dsr_direct, "threshold": 0.0, "passed": dsr_direct > 0.0},
        "pbo": {"value": None, "note": "non calculé -- une seule formule testée, n_trials=1"},
    }

    out = {
        "experiment_id": "cross_sectional_momentum_v1 (MOMENTUM_CRYPTO_V1)",
        "step": "4-7/8 -- event-level edge + portfolio + walk-forward + gates",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "universe": {"ranked": CRYPTO_32, "hedge_only": BTC, "excluded": ["TONUSDT (SETTLING)"],
                    "n_ranked": len(CRYPTO_32)},
        "params": {"beta_window": BETA_WINDOW, "vol_window": VOL_WINDOW,
                  "lookback_weights": LOOKBACK_WEIGHTS, "long_short_frac": LONG_SHORT_FRAC,
                  "max_weight_per_name": MAX_WEIGHT_PER_NAME, "min_history_days": MIN_HISTORY_DAYS,
                  "fee_bp": FEE_BP, "slippage_bp": SLIPPAGE_BP},
        "n_days": int(len(net_x1)),
        "date_range": [str(net_x1.index.min()), str(net_x1.index.max())],
        "per_year_cagr_x1_pct": {k: v * 100 for k, v in per_year.items()},
        "user_gates": user_gates,
        "promotion_gate_sleeve": [g.__dict__ for g in sleeve_gates],
        "promotion_gate_research": [g.__dict__ for g in research_gates],
    }

    out_path = ROOT / "research/edge_factory/cross_sectional_momentum_v1/results"
    out_path.mkdir(parents=True, exist_ok=True)
    fname = out_path / f"MOMENTUM_CRYPTO_V1_BACKTEST_{out['date']}.json"
    fname.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    print(f"\n-> {fname}")


if __name__ == "__main__":
    main()
