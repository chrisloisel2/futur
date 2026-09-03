#!/usr/bin/env python
"""Gate de validation §2 du briefing round 4 — module reutilisable.

Fournit :
  - decluster(df) : 3 niveaux (L1 symbole/24h, L2 jour calendaire, L3 episode
    cross-symbole chaine >=4h)
  - gate(df, ret_col, direction) : tous les champs du §2
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COST_BPS = 14.0
COST_STRESS_BPS = 28.0
Z_ALPHA = 1.6449   # unilateral 5%
Z_POWER = 0.8416   # power 80%
HAIRCUT = 0.5


def _episode_ids(df: pd.DataFrame, gap: str, by_symbol: bool) -> np.ndarray:
    """Chainage temporel : nouvel episode si l'ecart au precedent > gap."""
    g = pd.Timedelta(gap)
    out = np.zeros(len(df), dtype=np.int64)
    d = df.sort_values("event_time")
    if by_symbol:
        k = 0
        for _, grp in d.groupby("symbol", sort=False):
            t = grp["event_time"].values
            newep = np.r_[True, (np.diff(t) > g.to_timedelta64())]
            ids = np.cumsum(newep) + k
            k = ids.max()
            out[d.index.get_indexer(grp.index)] = ids
    else:
        t = d["event_time"].values
        newep = np.r_[True, (np.diff(t) > g.to_timedelta64())]
        out[:] = np.cumsum(newep)
    s = pd.Series(out, index=d.index)
    return s.reindex(df.index).values


def decluster(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d = d.sort_values("event_time").reset_index(drop=True)
    d["L1"] = d["symbol"].astype(str) + "|" + pd.Series(_episode_ids(d, "24h", True)).astype(str)
    d["L2"] = d["event_time"].dt.floor("D").astype(str)
    d["L3"] = "EP" + pd.Series(_episode_ids(d, "4h", False)).astype(str)
    return d


def _tstat(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def _block_boot_ci(x: np.ndarray, n_boot: int = 4000, seed: int = 20260903):
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 5:
        return [float("nan"), float("nan")]
    idx = rng.integers(0, n, size=(n_boot, n))
    means = x[idx].mean(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def _n_required(x_ep: np.ndarray) -> float:
    """N d'episodes independants pour confirmer un effet HAIRCUTE 50%."""
    x = np.asarray(x_ep, dtype=float)
    if len(x) < 5:
        return float("nan")
    mu, sd = x.mean(), x.std(ddof=1)
    eff = HAIRCUT * abs(mu)
    if eff <= 0:
        return float("inf")
    return float((Z_ALPHA + Z_POWER) ** 2 * sd ** 2 / eff ** 2)


def gate(df: pd.DataFrame, ret_col: str = "fwd_4h", direction: str = "LONG",
         label: str = "", recent_months: int = 6, cost: float = COST_BPS) -> dict:
    """df doit contenir event_time, symbol, ret_col. direction LONG|SHORT."""
    d = decluster(df)
    sgn = 1.0 if direction == "LONG" else -1.0
    d["gross_bps"] = sgn * d[ret_col].astype(float) * 1e4
    d = d[np.isfinite(d["gross_bps"])].copy()
    d["net_bps"] = d["gross_bps"] - cost
    n_raw = len(d)
    if n_raw < 10:
        return {"label": label, "direction": direction, "n_raw": n_raw, "status": "TOO_FEW"}

    lvl = {}
    for L in ("L1", "L2", "L3"):
        ep = d.groupby(L)["net_bps"].mean().values
        lvl[L] = {"n_independent": int(len(ep)),
                  "mean_net_bps": round(float(ep.mean()), 2),
                  "t_stat": round(_tstat(ep), 2)}

    ep3 = d.groupby("L3")["net_bps"].mean()
    ci = _block_boot_ci(ep3.values)
    nreq = _n_required(ep3.values)

    # by year (sur les episodes L3 pour rester declusterise)
    d["_year"] = d["event_time"].dt.year
    yr = d.groupby(["_year", "L3"])["net_bps"].mean().reset_index()
    by_year = (yr.groupby("_year")["net_bps"]
               .agg(["count", "mean"]).round(2).rename(columns={"count": "n_ep", "mean": "net_bps"}))
    by_year_d = {int(k): {"n_ep": int(v["n_ep"]), "net_bps": float(v["net_bps"])}
                 for k, v in by_year.iterrows()}
    best = max(by_year_d, key=lambda k: by_year_d[k]["net_bps"]) if by_year_d else None
    ex_best = d[d["_year"] != best] if best is not None else d
    ex_best_ep = ex_best.groupby("L3")["net_bps"].mean().values if len(ex_best) else np.array([])

    # event rate sur les N derniers mois (conservateur)
    tmax = d["event_time"].max()
    cut = tmax - pd.DateOffset(months=recent_months)
    rec = d[d["event_time"] >= cut]
    weeks = max((tmax - cut).total_seconds() / (7 * 86400), 1e-9)
    rate_l3 = rec["L3"].nunique() / weeks

    eta_weeks = nreq / rate_l3 if rate_l3 > 0 and np.isfinite(nreq) else float("inf")

    return {
        "label": label, "direction": direction,
        "n_raw": int(n_raw),
        "n_independent_L1": lvl["L1"]["n_independent"],
        "n_independent_L2": lvl["L2"]["n_independent"],
        "n_independent_L3": lvl["L3"]["n_independent"],
        "gross_bps": round(float(d["gross_bps"].mean()), 2),
        "net_bps": round(float(d["net_bps"].mean()), 2),
        "net_bps_stress28": round(float(d["gross_bps"].mean() - COST_STRESS_BPS), 2),
        "net_bps_L3_declustered": lvl["L3"]["mean_net_bps"],
        "t_stat_raw": round(_tstat(d["net_bps"].values), 2),
        "t_stat_declustered_L1": lvl["L1"]["t_stat"],
        "t_stat_declustered_L2": lvl["L2"]["t_stat"],
        "t_stat_declustered": lvl["L3"]["t_stat"],
        "bootstrap_ci95": [round(ci[0], 2), round(ci[1], 2)],
        "year_by_year": by_year_d,
        "best_year": best,
        "ex_best_year_net_bps": round(float(ex_best_ep.mean()), 2) if len(ex_best_ep) else None,
        "ex_best_year_t": round(_tstat(ex_best_ep), 2) if len(ex_best_ep) else None,
        "profit_factor": round(float(d.loc[d["net_bps"] > 0, "net_bps"].sum() /
                                     abs(d.loc[d["net_bps"] < 0, "net_bps"].sum())), 3)
        if (d["net_bps"] < 0).any() else None,
        "n_required": int(nreq) if np.isfinite(nreq) else None,
        "event_rate_per_week_L3_last%dm" % recent_months: round(float(rate_l3), 3),
        "eta_days": round(eta_weeks * 7, 1) if np.isfinite(eta_weeks) else None,
        "eta_years": round(eta_weeks / 52.18, 2) if np.isfinite(eta_weeks) else None,
        "period": [str(d["event_time"].min()), str(d["event_time"].max())],
    }


def contrast(df_a: pd.DataFrame, df_b: pd.DataFrame, ret_col: str, direction: str,
             label: str, recent_months: int = 6) -> dict:
    """bras_A - bras_B sur la MEME population (regle §1.3 : jamais contre zero).

    Le contraste est evalue sur les episodes L3 : pour chaque episode present dans
    les deux bras on prend la difference des moyennes ; sinon on utilise un t de
    Welch sur les moyennes d'episode des deux bras (independants)."""
    sgn = 1.0 if direction == "LONG" else -1.0
    a = decluster(df_a); b = decluster(df_b)
    a["v"] = sgn * a[ret_col].astype(float) * 1e4
    b["v"] = sgn * b[ret_col].astype(float) * 1e4
    a = a[np.isfinite(a["v"])]; b = b[np.isfinite(b["v"])]
    ea = a.groupby("L3")["v"].mean()
    eb = b.groupby("L3")["v"].mean()
    if len(ea) < 5 or len(eb) < 5:
        return {"label": label, "status": "TOO_FEW"}
    se = np.sqrt(ea.var(ddof=1) / len(ea) + eb.var(ddof=1) / len(eb))
    delta = float(ea.mean() - eb.mean())
    # bootstrap du delta (rechantillonnage independant des deux bras d'episodes)
    rng = np.random.default_rng(20260903)
    va, vb = ea.values, eb.values
    bs = (va[rng.integers(0, len(va), (4000, len(va)))].mean(axis=1)
          - vb[rng.integers(0, len(vb), (4000, len(vb)))].mean(axis=1))
    return {"label": label, "direction": direction,
            "arm_A_n_ep": int(len(ea)), "arm_B_n_ep": int(len(eb)),
            "arm_A_gross_bps": round(float(ea.mean()), 2),
            "arm_B_gross_bps": round(float(eb.mean()), 2),
            "delta_bps": round(delta, 2),
            "welch_t_declustered": round(float(delta / se), 2) if se > 0 else None,
            "delta_bootstrap_ci95": [round(float(np.percentile(bs, 2.5)), 2),
                                     round(float(np.percentile(bs, 97.5)), 2)]}
