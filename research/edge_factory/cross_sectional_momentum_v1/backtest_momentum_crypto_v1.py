#!/usr/bin/env python3
"""
research/edge_factory/cross_sectional_momentum_v1/backtest_momentum_crypto_v1.py
─────────────────────────────────────────────────────────────────────────────
MOMENTUM_CRYPTO_V1 — étapes 4-7. Réécrit le 2026-07-21 suite à l'audit
(QUARANTINE_2026-07-21.md) : exécution alignée open-to-open avec un vrai
délai (signal connu à close(t), exécuté à open(t+1), rendement capté
open(t+1)->open(t+2) — jamais close-to-close avec un simple décalage d'un
jour), cap de poids qui ne viole plus jamais la borne, invariants
quotidiens vérifiés et rapportés. La logique de poids/rendement est dans
momentum_engine.py (fonctions pures, testées indépendamment dans
tests/test_momentum_engine.py — symétrie de signe, identité comptable,
direction du classement).

Formule UNIQUE préenregistrée (aucune grille, n_trials=1) — inchangée
depuis PREREGISTRATION_CRYPTO_V1_ADDENDUM.md :

    score_i = 0.4*resid_mom7_i + 0.4*resid_mom30_i + 0.2*resid_mom90_i
              - illiq_penalty_i - funding_cost_i

Univers : toujours CRYPTO_32 (snapshot du 2026-06-30) dans CETTE version —
**l'univers PIT historique n'est PAS encore restauré ici** (commit séparé
"data: restore full point-in-time historical crypto universe", puis rerun
final dans "research: rerun unchanged 7/30/90 hypothesis once"). Ce script
sert à valider que le MOTEUR (exécution, poids, invariants) est correct
avant de changer l'univers — ne pas citer un résultat produit ici comme
verdict de famille : voir QUARANTINE_2026-07-21.md.

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

from research.edge_factory.cross_sectional_momentum_v1.momentum_engine import (  # noqa: E402
    check_daily_invariants, compute_btc_hedge, compute_weights, portfolio_returns)
from research.edge_factory.multileg_engine.backtest_result import (  # noqa: E402
    MultiLegBacktestResult)
from src.alpha20.validation.promotion_gate import deflated_sharpe_ratio  # noqa: E402

PRICE_DIR = ROOT / "data" / "derivatives_backfill" / "um_klines_1d"
FUNDING_DIR = ROOT / "data" / "derivatives_backfill" / "binance" / "funding"

# ATTENTION : snapshot du 2026-06-30, PAS un univers point-in-time. Voir le
# bandeau ci-dessus et QUARANTINE_2026-07-21.md.
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
FEE_BP = 5.0
SLIPPAGE_BP = 2.0
EXEC_DELAY_DAYS = 2   # close(t) connu -> open(t+1) exécuté -> rendement open(t+1)->open(t+2)


def load_field(symbol: str, field: str) -> pd.Series:
    df = pd.read_parquet(PRICE_DIR / f"{symbol}_1d.parquet", columns=["open_time", field])
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True).dt.normalize()
    return df.set_index("open_time")[field].rename(symbol)


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
    return {str(y): cagr(returns[returns.index.year != y])
           for y in sorted(returns.index.year.unique())}


def main() -> None:
    universe = CRYPTO_32 + [BTC]
    close = pd.concat([load_field(s, "close") for s in universe], axis=1).sort_index()
    open_px = pd.concat([load_field(s, "open") for s in universe], axis=1).sort_index()
    qvol = pd.concat([load_field(s, "quote_volume") for s in universe], axis=1).sort_index()
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

    signed_w, is_long, is_short = compute_weights(
        eligible_score, vol30[CRYPTO_32], LONG_SHORT_FRAC, MAX_WEIGHT_PER_NAME)
    btc_hedge_w = compute_btc_hedge(signed_w, beta[CRYPTO_32])

    pr = portfolio_returns(signed_w, btc_hedge_w, open_px, funding,
                          CRYPTO_32, BTC, EXEC_DELAY_DAYS)

    cost_x1 = -(pr["turnover"] * (FEE_BP + SLIPPAGE_BP) / 10_000.0)
    cost_x2 = -(pr["turnover"] * (FEE_BP + SLIPPAGE_BP) * 2 / 10_000.0)

    net_x1 = pr["gross_ret"] + pr["funding_pnl"] + cost_x1
    net_x2 = pr["gross_ret"] + pr["funding_pnl"] + cost_x2
    valid_index = hist_ok.any(axis=1)
    net_x1 = net_x1[valid_index].dropna()
    net_x2 = net_x2.reindex(net_x1.index)

    invariant_violations = check_daily_invariants(
        signed_w.reindex(net_x1.index), btc_hedge_w.reindex(net_x1.index),
        beta[CRYPTO_32].reindex(net_x1.index), MAX_WEIGHT_PER_NAME, net_x1)

    asset_net = (pr["asset_gross"] + pr["asset_funding"]).reindex(net_x1.index)
    per_asset_total = asset_net.sum(axis=0)
    per_asset_total["BTC_hedge"] = float(
        (pr["btc_hedge_gross"] + pr["btc_hedge_funding"]).reindex(net_x1.index).sum())
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
        trials_matrix=None,
        meta={"universe": CRYPTO_32, "hedge": BTC, "excluded": ["TONUSDT"],
             "n_trials": 1, "exec_delay_days": EXEC_DELAY_DAYS,
             "universe_is_pit": False})

    sleeve_gates = result.run_sleeve_gate()
    research_gates = result.run_research_gate(n_trials=1)
    dsr_direct = deflated_sharpe_ratio(net_x1, n_trials=1)

    user_gates = {
        "cagr_net_x1_pct": {"value": cagr(net_x1) * 100, "threshold": 12.0,
                           "passed": cagr(net_x1) * 100 > 12.0},
        "sharpe_x1": {"value": sharpe(net_x1), "threshold": 1.2, "passed": sharpe(net_x1) > 1.2},
        "max_dd_pct": {"value": max_drawdown(net_x1) * 100, "threshold": -15.0,
                      "passed": max_drawdown(net_x1) * 100 > -15.0},
        "costs_x2_positive": {"value": cagr(net_x2) * 100, "threshold": 0.0,
                             "passed": cagr(net_x2) > 0.0},
        "leave_one_year_positive": {"value": {k: v * 100 for k, v in loy.items()},
                                    "passed": all(v > 0 for v in loy.values())},
        "max_asset_concentration_pct": {"value": concentration * 100, "threshold": 15.0,
                                        "passed": concentration <= 0.15,
                                        "top_contributor": top_contributor},
        "dsr_positive": {"value": dsr_direct, "threshold": 0.0, "passed": dsr_direct > 0.0},
        "pbo": {"value": None, "note": "non calculé -- une seule formule testée, n_trials=1"},
    }

    out = {
        "experiment_id": "cross_sectional_momentum_v1 (MOMENTUM_CRYPTO_V1)",
        "step": "4-7/8 -- ENGINE VALIDATION RUN, universe NOT YET PIT-corrected",
        "warning": "universe_is_pit=False -- do not cite as a family-level verdict, "
                  "see QUARANTINE_2026-07-21.md",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "universe": {"ranked": CRYPTO_32, "hedge_only": BTC,
                    "excluded": ["TONUSDT (SETTLING)"], "n_ranked": len(CRYPTO_32)},
        "params": {"beta_window": BETA_WINDOW, "vol_window": VOL_WINDOW,
                  "lookback_weights": LOOKBACK_WEIGHTS, "long_short_frac": LONG_SHORT_FRAC,
                  "max_weight_per_name": MAX_WEIGHT_PER_NAME, "min_history_days": MIN_HISTORY_DAYS,
                  "fee_bp": FEE_BP, "slippage_bp": SLIPPAGE_BP,
                  "exec_delay_days": EXEC_DELAY_DAYS},
        "n_days": int(len(net_x1)),
        "date_range": [str(net_x1.index.min()), str(net_x1.index.max())],
        "per_year_cagr_x1_pct": {k: v * 100 for k, v in per_year.items()},
        "invariant_violations": invariant_violations,
        "user_gates": user_gates,
        "promotion_gate_sleeve": [g.__dict__ for g in sleeve_gates],
        "promotion_gate_research": [g.__dict__ for g in research_gates],
    }

    out_path = ROOT / "research/edge_factory/cross_sectional_momentum_v1/results"
    out_path.mkdir(parents=True, exist_ok=True)
    fname = out_path / f"MOMENTUM_CRYPTO_V1_ENGINE_VALIDATION_{out['date']}.json"
    fname.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    print(f"\n-> {fname}")


if __name__ == "__main__":
    main()
