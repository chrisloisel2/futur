#!/usr/bin/env python3
"""
research/edge_factory/cross_sectional_momentum_v1/backtest_momentum_crypto_v1.py
─────────────────────────────────────────────────────────────────────────────
MOMENTUM_CRYPTO_V1 — étapes 4-7, univers PIT (audit QUARANTINE_2026-07-21.md
complet) : exécution open-to-open avec délai réel de 2 jours, poids
water-filling (cap jamais dépassé, neutralité dollar exacte quand
faisable), invariants quotidiens vérifiés, et — dernière pièce de l'audit —
univers point-in-time reconstruit par build_pit_universe.py (312 symboles
crypto réellement actifs à chaque date, délistés compris) au lieu du
snapshot CRYPTO_32 du 2026-06-30.

Formule UNIQUE préenregistrée (aucune grille, n_trials=1) — INCHANGÉE
depuis PREREGISTRATION_CRYPTO_V1_ADDENDUM.md et depuis les runs de
validation moteur précédents :

    score_i = 0.4*resid_mom7_i + 0.4*resid_mom30_i + 0.2*resid_mom90_i
              - illiq_penalty_i - funding_cost_i

C'est le rerun final visé par l'audit : mêmes paramètres, même formule,
moteur corrigé (exécution, cap, invariants) + univers corrigé (PIT). Ne
lance PAS une nouvelle variante — un seul run, verdict pris tel quel.

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
RESULTS_DIR = ROOT / "research/edge_factory/cross_sectional_momentum_v1/results"

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


def load_open(symbol: str) -> pd.Series:
    df = pd.read_parquet(PRICE_DIR / f"{symbol}_1d.parquet", columns=["open_time", "open"])
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True).dt.normalize()
    return df.set_index("open_time")["open"].rename(symbol)


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
    manifest = json.loads((RESULTS_DIR / "PIT_UNIVERSE_MANIFEST.json").read_text())
    member = pd.read_parquet(RESULTS_DIR / "pit_universe_membership.parquet")
    close = pd.read_parquet(RESULTS_DIR / "pit_universe_close.parquet")
    qvol = pd.read_parquet(RESULTS_DIR / "pit_universe_qv.parquet")

    # BTC est mathématiquement toujours membre du top-30 PIT (plus gros volume
    # de tout l'univers) -- il apparaît donc déjà dans pit_universe_close.parquet.
    # Il reste néanmoins hors classement : instrument de hedge bêta uniquement,
    # jamais un candidat long/short (même architecture que la version CRYPTO_32
    # précédente). On le retire des noms classés, mais sa série de prix/volume
    # reste disponible dans `close`/`qvol` pour le calcul du bêta.
    ranked_symbols = [s for s in close.columns if s != BTC]
    member = member.reindex(close.index)

    all_symbols = ranked_symbols + [BTC]
    open_px = pd.concat([load_open(s) for s in all_symbols], axis=1).sort_index()
    open_px = open_px.reindex(close.index)
    qvol = qvol.reindex(close.index)

    funding = pd.concat([load_daily_funding(s) for s in all_symbols], axis=1).sort_index()
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

    # cross-sectionnel calculé SEULEMENT sur les noms classés -- BTC (encore
    # présent dans qvol, jamais retiré de ce DataFrame) fausserait la
    # moyenne/écart-type s'il y restait : son volume domine largement
    # celui de n'importe quel altcoin classé.
    log_qvol30 = np.log(qvol[ranked_symbols].rolling(VOL_WINDOW).median().clip(lower=1.0))
    illiq_penalty = log_qvol30.sub(log_qvol30.mean(axis=1), axis=0).div(
        log_qvol30.std(axis=1), axis=0) * -1.0

    funding_cost = funding.rolling(VOL_WINDOW).mean().abs() * 365.0

    score_full = resid_mom_sum - illiq_penalty - funding_cost
    score = score_full[ranked_symbols]

    hist_ok = (close[ranked_symbols].notna()
              .rolling(MIN_HISTORY_DAYS, min_periods=MIN_HISTORY_DAYS).count()
              >= MIN_HISTORY_DAYS)
    # éligibilité = membre PIT du jour ET assez d'historique pour un signal
    # bien défini (build_membership() exige seulement 31j, notre signal en
    # exige 120 pour la fenêtre 90j + bêta 90j)
    is_member = member[ranked_symbols].fillna(False).astype(bool)
    eligible_score = score.where(hist_ok & is_member)

    signed_w, is_long, is_short = compute_weights(
        eligible_score, vol30[ranked_symbols], LONG_SHORT_FRAC, MAX_WEIGHT_PER_NAME)
    btc_hedge_w = compute_btc_hedge(signed_w, beta[ranked_symbols])

    pr = portfolio_returns(signed_w, btc_hedge_w, open_px, funding,
                          ranked_symbols, BTC, EXEC_DELAY_DAYS)

    cost_x1 = -(pr["turnover"] * (FEE_BP + SLIPPAGE_BP) / 10_000.0)
    cost_x2 = -(pr["turnover"] * (FEE_BP + SLIPPAGE_BP) * 2 / 10_000.0)

    net_x1 = pr["gross_ret"] + pr["funding_pnl"] + cost_x1
    net_x2 = pr["gross_ret"] + pr["funding_pnl"] + cost_x2
    valid_index = (hist_ok & is_member).any(axis=1)
    net_x1 = net_x1[valid_index].dropna()
    net_x2 = net_x2.reindex(net_x1.index)

    invariant_violations = check_daily_invariants(
        signed_w.reindex(net_x1.index), btc_hedge_w.reindex(net_x1.index),
        beta[ranked_symbols].reindex(net_x1.index), MAX_WEIGHT_PER_NAME, net_x1)

    asset_net = (pr["asset_gross"] + pr["asset_funding"]).reindex(net_x1.index)
    per_asset_total = asset_net.sum(axis=0)
    per_asset_total["BTC_hedge"] = float(
        (pr["btc_hedge_gross"] + pr["btc_hedge_funding"]).reindex(net_x1.index).sum())
    total_abs = per_asset_total.abs().sum()
    concentration = float(per_asset_total.abs().max() / total_abs) if total_abs else float("nan")
    top_contributor = str(per_asset_total.abs().idxmax())

    per_year = {str(y): cagr(g) for y, g in net_x1.groupby(net_x1.index.year)}
    loy = leave_one_year(net_x1)

    n_symbols_with_funding = int((funding[ranked_symbols].abs().sum(axis=0) > 0).sum())

    result = MultiLegBacktestResult(
        trades=pd.DataFrame({"net_x1": net_x1, "net_x2": net_x2}),
        pnl_daily=net_x1,
        per_year={str(y): float(net_x1[net_x1.index.year == y].sum())
                 for y in net_x1.index.year.unique()},
        net_events=net_x1, net_events_x2=net_x2,
        returns_for_dsr=net_x1,
        trials_matrix=None,
        meta={"universe_size": len(ranked_symbols), "hedge": BTC,
             "n_trials": 1, "exec_delay_days": EXEC_DELAY_DAYS,
             "universe_is_pit": True,
             "n_symbols_with_funding_data": n_symbols_with_funding})

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
        "step": "4-7/8 -- FINAL RERUN, engine-corrected + PIT universe",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "universe": {"n_ranked_ever_member": len(ranked_symbols), "hedge_only": BTC,
                    "n_symbols_with_funding_data": n_symbols_with_funding,
                    "pit_universe_manifest_date": manifest.get("date")},
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

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = RESULTS_DIR / f"MOMENTUM_CRYPTO_V1_PIT_FINAL_{out['date']}.json"
    fname.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    print(f"\n-> {fname}")


if __name__ == "__main__":
    main()
