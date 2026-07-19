#!/usr/bin/env python3
"""
scripts/backtest_options_positioning.py
─────────────────────────────────────────────────────────────────────────────
OPTIONS_POSITIONING v1 — test pré-enregistré (BTC, Deribit 2023-01 → 2026-06).

PROTOCOLE PRÉ-ENREGISTRÉ — déclaré AVANT tout calcul de relation
features → retours. Aucun paramètre n'est optimisé ; tout est fixé ici.

Hypothèse primaire (Alexander, Deng, Feng & Wan, arXiv:2109.02776 : les
options OTM Deribit sont pilotées par des traders directionnels informés) :

  PRIMAIRE  OTM_FLOW_t = z90(net_call_flow_btc) − z90(net_put_flow_btc)
            z-scores causaux rolling 90 j (min 60), aucun retuning.
            Position : long BTC sur [open t+1, open t+2) si OTM_FLOW_t > 0,
            sinon cash. Coûts : 15 bps par unité de turnover (30 bps A/R),
            stress ×2 = 30 bps.

  VERDICT EDGE_CANDIDATE ssi TOUTES :
    P1  t-stat Newey-West (lag 5) de ret_next ~ OTM_FLOW ≥ 2,0 (signe +)
    P2  alpha de timing annualisé net coûts ×2 > 0
        (alpha = mean(strat) − exposition_moyenne × mean(buy&hold))
    P3  signe du coefficient identique sur les 2 moitiés de l'échantillon
    P4  P2 survit à +1 jour de délai (entrée open t+2)
  Robustesse annexe (rapportée, non éliminatoire seule) : drop des 10 plus
  gros jours |ret| ; DSR avec 6 essais déclarés.

  SECONDAIRES (exploratoires, PAS de verdict, comptés dans les essais DSR) :
    S1 skew_25ish z90 bas (calls riches) → cash (fade de l'euphorie)
    S2 d_skew_25ish (variation) → direction
    S3 pc_volume_ratio z90
    S4 top_strike_share z90 haut (pinning) → cash
    S5 OTM_FLOW × (1 + block_share) (poids blocs)
    S6 primaire à horizon 3 j

Env : .venv Python 3.8.10.
Commande : .venv/bin/python scripts/backtest_options_positioning.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / "data/options_backfill/deribit/features/BTC_daily.parquet"
KLINES = ROOT / "data/derivatives_backfill/um_klines_1d/BTCUSDT_1d.parquet"
OUT = ROOT / "reports/OPTIONS_POSITIONING_V1_VERDICT.json"

COST_PER_TURNOVER = 0.0015   # 15 bps par unité de |Δw| (30 bps A/R)
Z_WIN, Z_MIN = 90, 60
NW_LAG = 5
N_TRIALS_DECLARED = 6


def zscore_causal(s: pd.Series) -> pd.Series:
    mu = s.rolling(Z_WIN, min_periods=Z_MIN).mean()
    sd = s.rolling(Z_WIN, min_periods=Z_MIN).std()
    return (s - mu) / sd.replace(0, np.nan)


def nw_tstat(y: np.ndarray, x: np.ndarray, lag: int = NW_LAG):
    """OLS y = a + b·x, t-stat Newey-West sur b."""
    mask = np.isfinite(y) & np.isfinite(x)
    y, x = y[mask], x[mask]
    n = len(y)
    if n < 50:
        return np.nan, np.nan, n
    X = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    u = y - X @ beta
    Xu = X * u[:, None]
    S = Xu.T @ Xu / n
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        G = Xu[k:].T @ Xu[:-k] / n
        S += w * (G + G.T)
    XtX_inv = np.linalg.inv(X.T @ X / n)
    V = XtX_inv @ S @ XtX_inv / n
    return float(beta[1]), float(beta[1] / np.sqrt(V[1, 1])), n


def deflated_sharpe(returns: np.ndarray, n_trials: int) -> float:
    """DSR (Bailey & López de Prado) : P(SR vrai > 0) corrigé de n essais."""
    r = returns[np.isfinite(returns)]
    n = len(r)
    if n < 50 or r.std() == 0:
        return np.nan
    sr = r.mean() / r.std()
    from scipy import stats as st
    g3 = st.skew(r)
    g4 = st.kurtosis(r, fisher=False)
    # SR max attendu sous H0 parmi n_trials essais (approx EV des max de N(0, var_sr))
    var_sr = (1 - g3 * sr + (g4 - 1) / 4 * sr**2) / (n - 1)
    emc = 0.5772156649
    z_max = (1 - emc) * st.norm.ppf(1 - 1 / n_trials) + emc * st.norm.ppf(1 - 1 / (n_trials * np.e))
    sr0 = z_max * np.sqrt(var_sr)
    return float(st.norm.cdf((sr - sr0) / np.sqrt(var_sr)))


def run_strategy(w: pd.Series, ret_next: pd.Series, cost_mult: float = 1.0):
    """w_t ∈ {0,1} décidé jour t, appliqué sur ret [open t+1 → open t+2]."""
    w = w.fillna(0.0)
    turnover = w.diff().abs().fillna(w.abs())
    strat = w * ret_next - turnover * COST_PER_TURNOVER * cost_mult
    strat = strat.dropna()
    if len(strat) == 0:
        return None
    mean_d, std_d = strat.mean(), strat.std()
    eq = (1 + strat).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    return {
        "n": int(len(strat)),
        "exposure": float(w.loc[strat.index].mean()),
        "ann_ret": float(mean_d * 365),
        "sharpe": float(mean_d / std_d * np.sqrt(365)) if std_d > 0 else np.nan,
        "max_dd": dd,
        "daily": strat,
    }


def timing_alpha(strat_daily: pd.Series, ret_next: pd.Series, exposure: float) -> float:
    bh = ret_next.loc[strat_daily.index]
    return float((strat_daily.mean() - exposure * bh.mean()) * 365)


def main() -> None:
    f = pd.read_parquet(FEATURES).set_index("day").sort_index()
    k = pd.read_parquet(KLINES)
    k["day"] = pd.to_datetime(k["open_time"], utc=True).dt.floor("D")
    k = k.set_index("day").sort_index()
    # ret [open t+1 → open t+2], aligné en t (causal : features du jour t complètes à t+1 00:00)
    k["ret_next"] = k["open"].shift(-2) / k["open"].shift(-1) - 1
    df = f.join(k[["ret_next"]], how="inner").dropna(subset=["ret_next"])

    for c in ["net_call_flow_btc", "net_put_flow_btc", "skew_25ish",
              "pc_volume_ratio", "top_strike_share"]:
        df[f"z_{c}"] = zscore_causal(df[c])
    df["otm_flow"] = df["z_net_call_flow_btc"] - df["z_net_put_flow_btc"]

    d = df.dropna(subset=["otm_flow"])
    y, x = d["ret_next"].values, d["otm_flow"].values

    # P1 — t-stat NW pleine période
    beta, t_full, n_full = nw_tstat(y, x)

    # P3 — stabilité de signe sur les 2 moitiés
    half = len(d) // 2
    b1, t1, _ = nw_tstat(y[:half], x[:half])
    b2, t2, _ = nw_tstat(y[half:], x[half:])

    # P2 — stratégie long/cash, coûts ×1 et ×2
    w = (d["otm_flow"] > 0).astype(float)
    s1 = run_strategy(w, d["ret_next"], 1.0)
    s2 = run_strategy(w, d["ret_next"], 2.0)
    alpha_x1 = timing_alpha(s1["daily"], d["ret_next"], s1["exposure"])
    alpha_x2 = timing_alpha(s2["daily"], d["ret_next"], s2["exposure"])

    # P4 — délai +1 jour (le poids décidé en t s'applique en t+1)
    s2_delay = run_strategy(w.shift(1), d["ret_next"], 2.0)
    alpha_x2_delay = timing_alpha(s2_delay["daily"], d["ret_next"], s2_delay["exposure"])

    # Robustesse : drop des 10 plus gros |ret_next|
    keep = d["ret_next"].abs().nsmallest(len(d) - 10).index.sort_values()
    dk = d.loc[keep]
    _, t_drop, _ = nw_tstat(dk["ret_next"].values, dk["otm_flow"].values)
    s2k = run_strategy((dk["otm_flow"] > 0).astype(float), dk["ret_next"], 2.0)
    alpha_x2_drop = timing_alpha(s2k["daily"], dk["ret_next"], s2k["exposure"])

    dsr = deflated_sharpe(s1["daily"].values, N_TRIALS_DECLARED)
    corr_bh = float(s1["daily"].corr(d["ret_next"].loc[s1["daily"].index]))

    p1 = bool(t_full >= 2.0)
    p2 = bool(alpha_x2 > 0)
    p3 = bool(np.sign(b1) == np.sign(b2) == np.sign(beta))
    p4 = bool(alpha_x2_delay > 0)
    verdict = "EDGE_CANDIDATE" if (p1 and p2 and p3 and p4) else "NO_EDGE"

    # Secondaires — exploratoires (IC Spearman + NW-t), pas de verdict
    secondaries = {}
    specs = {
        "S1_skew_low_fade": -d["z_skew_25ish"],          # calls riches → signal négatif attendu
        "S2_d_skew": d["d_skew_25ish"],
        "S3_pc_ratio_z": d["z_pc_volume_ratio"],
        "S4_pinning_cash": -d["z_top_strike_share"],
        "S5_flow_x_block": d["otm_flow"] * (1 + d["block_share"]),
    }
    for name, sig in specs.items():
        dd_ = pd.concat([sig.rename("s"), d["ret_next"]], axis=1).dropna()
        _, t_, n_ = nw_tstat(dd_["ret_next"].values, dd_["s"].values)
        ic = float(dd_["s"].rank().corr(dd_["ret_next"].rank()))
        secondaries[name] = {"nw_t": round(t_, 2), "spearman_ic": round(ic, 4), "n": n_}
    # S6 : primaire à 3 j
    r3 = (k["open"].shift(-4) / k["open"].shift(-1) - 1).reindex(d.index)
    dd_ = pd.concat([d["otm_flow"], r3.rename("r3")], axis=1).dropna()
    _, t3, n3 = nw_tstat(dd_["r3"].values, dd_["otm_flow"].values, lag=8)
    secondaries["S6_primary_3d"] = {"nw_t": round(t3, 2), "n": n3}

    result = {
        "test": "OPTIONS_POSITIONING_V1",
        "date": "2026-07-17",
        "sample": [str(d.index.min().date()), str(d.index.max().date())],
        "n_days": n_full,
        "verdict": verdict,
        "primary": {
            "beta": round(beta, 6), "nw_t_full": round(t_full, 2),
            "nw_t_half1": round(t1, 2), "nw_t_half2": round(t2, 2),
            "beta_half1": round(b1, 6), "beta_half2": round(b2, 6),
            "P1_nw_t_ge_2": p1, "P2_alpha_x2_pos": p2,
            "P3_sign_stable": p3, "P4_delay_survives": p4,
        },
        "strategy": {
            "exposure": round(s1["exposure"], 3),
            "sharpe_x1": round(s1["sharpe"], 2), "ann_ret_x1": round(s1["ann_ret"], 4),
            "sharpe_x2": round(s2["sharpe"], 2), "ann_ret_x2": round(s2["ann_ret"], 4),
            "max_dd_x2": round(s2["max_dd"], 4),
            "timing_alpha_x1": round(alpha_x1, 4), "timing_alpha_x2": round(alpha_x2, 4),
            "timing_alpha_x2_delay1": round(alpha_x2_delay, 4),
            "timing_alpha_x2_drop10": round(alpha_x2_drop, 4),
            "nw_t_drop10": round(t_drop, 2),
            "dsr_6_trials": round(dsr, 3) if np.isfinite(dsr) else None,
            "corr_vs_buyhold": round(corr_bh, 3),
        },
        "secondaries_exploratory": secondaries,
        "notes": [
            "ETH non testé (pas de trades backfillés).",
            "Klines 1d s'arrêtent 2026-06-30 — 17 derniers jours de features non utilisés.",
            "Coûts : 15 bps/turnover (30 A/R), stress x2 = 30 bps.",
        ],
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
