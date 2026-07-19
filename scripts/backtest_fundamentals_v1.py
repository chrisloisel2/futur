#!/usr/bin/env python3
"""
scripts/backtest_fundamentals_v1.py
─────────────────────────────────────────────────────────────────────────────
FUNDAMENTALS_V1 — rotation fondamentale protocolaire (test pré-enregistré).

Hypothèse (littérature) : l'ACCÉLÉRATION des fees d'un protocole prédit la
surperformance cross-section de son token — pas le niveau (la TVL brute
n'est pas pricée, Economics Letters 2025 ; il faut être orthogonal au
marché : spread cross-section, pas long-only).

PROTOCOLE PRÉ-ENREGISTRÉ (avant tout téléchargement de fees) :

  Univers CANDIDAT FIXE (déclaré ici, aucune exclusion post-hoc) :
  tout protocole de la liste ci-dessous qui résout sur l'API DefiLlama
  ET possède un perp USDT-M dans um_klines_1d entre dans l'univers.

  Feature (hebdo, causale, calculée chaque lundi avec données ≤ dimanche) :
    fees_accel = log( (1+moy fees 30 j) / (1+moy fees [t−120, t−30]) )
  Portefeuille : long tercile haut equal-weight vs moyenne d'univers
  (spread auto-financé). Retours semaine suivante avec 1 jour de délai
  d'exécution : close t+1 → close t+8. Min 9 protocoles valides/semaine.
  Coûts : 30 bps A/R ×1/×2 sur le turnover de la jambe longue (borne
  haute : rotation complète = 30/60 bps par semaine).

  VERDICT SIGNAL_VALIDATED ssi TOUTES :
    P1  NW-t (lag 4) du spread hebdo ≥ 2,0
    P2  spread net coûts ×2 > 0 (moyenne hebdo − 60 bps × turnover réel)
    P3  signe du spread identique sur les 2 moitiés
    P4  IC Spearman moyen (feature → ret hebdo cross-section) > 0 avec t ≥ 2

  SECONDAIRES (exploratoires, essais DSR = 4) :
    S1 accélération des revenus (dataType=dailyRevenue)
    S2 fees momentum simple (moy 30 j / moy 90 j décalée, sans log)
    S3 tercile bas (short candidat) — rapporté seulement
    S4 horizon 2 semaines

Caveats déclarés : biais de survivance résiduel (protocoles morts absents
de DefiLlama aujourd'hui) — un SIGNAL_VALIDATED devra être reconfirmé sur
univers point-in-time avant tout câblage ; fees DefiLlama = données
actuelles re-publiées (révisions possibles non versionnées).

Env : .venv Python 3.8.10.
Commande : .venv/bin/python scripts/backtest_fundamentals_v1.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
KLINES = ROOT / "data/derivatives_backfill/um_klines_1d"
CACHE = ROOT / "data/fundamentals_backfill"
OUT = ROOT / "reports/FUNDAMENTALS_V1_VERDICT.json"

# Liste candidate FIXE (slug DefiLlama, ticker perp) — déclarée avant fetch.
CANDIDATES = [
    ("aave", "AAVE"), ("uniswap", "UNI"), ("lido", "LDO"),
    ("makerdao", "MKR"), ("curve-finance", "CRV"), ("compound-finance", "COMP"),
    ("synthetix", "SNX"), ("sushiswap", "SUSHI"), ("gmx", "GMX"),
    ("dydx", "DYDX"), ("pendle", "PENDLE"), ("1inch-network", "1INCH"),
    ("pancakeswap", "CAKE"), ("jupiter-aggregator", "JUP"), ("raydium", "RAY"),
    ("ether-fi", "ETHFI"), ("ethereum-name-service", "ENS"),
    ("stargate-finance", "STG"), ("woofi", "WOO"), ("dodo", "DODO"),
    ("balancer", "BAL"), ("trader-joe", "JOE"), ("osmosis", "OSMO"),
    ("kyberswap", "KNC"),
]
NW_LAG = 4
COST_RT = 0.0030  # 30 bps A/R


def nw_tstat_mean(x, lag=NW_LAG):
    """t-stat NW de la moyenne d'une série (H0 : moyenne = 0)."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 30:
        return np.nan, n
    m = x.mean()
    u = x - m
    s = u @ u / n
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        s += 2 * w * (u[k:] @ u[:-k]) / n
    return float(m / np.sqrt(s / n)), n


def fetch_fees(slug: str, data_type: str) -> pd.Series:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / f"{slug}_{data_type}.parquet"
    if cache.exists():
        s = pd.read_parquet(cache)["value"]
        s.index = pd.to_datetime(s.index, utc=True)
        return s
    url = f"https://api.llama.fi/summary/fees/{slug}?dataType={data_type}"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        return pd.Series(dtype=float)
    chart = r.json().get("totalDataChart") or []
    if not chart:
        return pd.Series(dtype=float)
    s = pd.Series({pd.to_datetime(int(t), unit="s", utc=True): float(v)
                   for t, v in chart}).sort_index()
    s.to_frame("value").to_parquet(cache)
    time.sleep(0.5)
    return s


def accel(fees: pd.Series) -> pd.Series:
    m30 = fees.rolling(30, min_periods=20).mean()
    m90lag = fees.rolling(90, min_periods=60).mean().shift(30)
    return np.log((1 + m30) / (1 + m90lag))


def main():
    fees, closes = {}, {}
    resolved = []
    for slug, tick in CANDIDATES:
        kf = KLINES / f"{tick}USDT_1d.parquet"
        if not kf.exists():
            continue
        s = fetch_fees(slug, "dailyFees")
        if len(s) < 200:
            continue
        k = pd.read_parquet(kf)
        k["day"] = pd.to_datetime(k["open_time"], utc=True).dt.floor("D")
        closes[tick] = k.set_index("day")["close"].sort_index()
        fees[tick] = s
        resolved.append((slug, tick))

    # grille hebdo : tous les lundis couverts par les données
    all_days = pd.date_range("2021-07-01", "2026-06-28", freq="D", tz="UTC")
    mondays = [d for d in all_days if d.weekday() == 0]

    def build(feature_fn, fees_map, horizon_days=7):
        rows = []
        for mon in mondays:
            feat_at = {}
            rets = {}
            for tick in fees_map:
                f = feature_fn(fees_map[tick])
                f = f[f.index < mon]  # causal : données ≤ dimanche
                if len(f) == 0 or not np.isfinite(f.iloc[-1]):
                    continue
                c = closes[tick]
                t1, t8 = mon + pd.Timedelta(days=1), mon + pd.Timedelta(days=1 + horizon_days)
                if t1 not in c.index or t8 not in c.index:
                    continue
                feat_at[tick] = float(f.iloc[-1])
                rets[tick] = float(c[t8] / c[t1] - 1)
            if len(feat_at) < 9:
                continue
            fs = pd.Series(feat_at)
            rs = pd.Series(rets)
            n_top = max(3, len(fs) // 3)
            top = fs.nlargest(n_top).index
            bot = fs.nsmallest(n_top).index
            ic = float(fs.rank().corr(rs.rank()))
            rows.append({"monday": mon, "n": len(fs),
                         "spread": float(rs[top].mean() - rs.mean()),
                         "bot_spread": float(rs[bot].mean() - rs.mean()),
                         "ic": ic, "top": set(top)})
        return rows

    rows = build(accel, fees)
    dfw = pd.DataFrame(rows).set_index("monday")
    # turnover de la jambe longue (fraction remplacée / semaine)
    tops = dfw["top"].tolist()
    turn = [1.0] + [len(tops[i] - tops[i - 1]) / max(1, len(tops[i]))
                    for i in range(1, len(tops))]
    dfw["turnover"] = turn
    dfw["net_x1"] = dfw["spread"] - dfw["turnover"] * COST_RT
    dfw["net_x2"] = dfw["spread"] - dfw["turnover"] * COST_RT * 2

    t_spread, n_w = nw_tstat_mean(dfw["spread"])
    t_ic, _ = nw_tstat_mean(dfw["ic"])
    half = len(dfw) // 2
    m1, m2 = dfw["spread"].iloc[:half].mean(), dfw["spread"].iloc[half:].mean()

    p1 = bool(t_spread >= 2.0)
    p2 = bool(dfw["net_x2"].mean() > 0)
    p3 = bool(np.sign(m1) == np.sign(m2))
    p4 = bool(dfw["ic"].mean() > 0 and t_ic >= 2.0)
    verdict = "SIGNAL_VALIDATED" if (p1 and p2 and p3 and p4) else "NO_EDGE"

    # Secondaires
    sec = {}
    rev = {t: fetch_fees(s, "dailyRevenue") for s, t in resolved}
    rev = {t: v for t, v in rev.items() if len(v) >= 200}
    if len(rev) >= 9:
        r_rows = pd.DataFrame(build(accel, rev)).set_index("monday")
        ts_, _ = nw_tstat_mean(r_rows["spread"])
        sec["S1_revenue_accel"] = {"mean_spread": round(float(r_rows["spread"].mean()), 5),
                                   "nw_t": round(ts_, 2), "n_weeks": len(r_rows), "n_prot": len(rev)}
    mom_rows = pd.DataFrame(build(
        lambda f: f.rolling(30, min_periods=20).mean()
        / f.rolling(90, min_periods=60).mean().shift(30), fees)).set_index("monday")
    ts_, _ = nw_tstat_mean(mom_rows["spread"])
    sec["S2_fees_momentum"] = {"mean_spread": round(float(mom_rows["spread"].mean()), 5),
                               "nw_t": round(ts_, 2)}
    tb, _ = nw_tstat_mean(dfw["bot_spread"])
    sec["S3_bottom_tercile"] = {"mean_spread": round(float(dfw["bot_spread"].mean()), 5),
                                "nw_t": round(tb, 2)}
    r2 = pd.DataFrame(build(accel, fees, horizon_days=14)).set_index("monday")
    ts_, _ = nw_tstat_mean(r2["spread"], lag=6)
    sec["S4_2weeks"] = {"mean_spread": round(float(r2["spread"].mean()), 5),
                        "nw_t": round(ts_, 2)}

    result = {
        "test": "FUNDAMENTALS_V1",
        "date": "2026-07-18",
        "universe_resolved": [t for _, t in resolved],
        "n_protocols": len(resolved),
        "n_weeks": int(n_w),
        "sample": [str(dfw.index.min().date()), str(dfw.index.max().date())],
        "verdict": verdict,
        "primary": {
            "mean_weekly_spread": round(float(dfw["spread"].mean()), 5),
            "nw_t_spread": round(t_spread, 2),
            "mean_ic": round(float(dfw["ic"].mean()), 4), "nw_t_ic": round(t_ic, 2),
            "half1_spread": round(float(m1), 5), "half2_spread": round(float(m2), 5),
            "mean_turnover": round(float(dfw["turnover"].mean()), 3),
            "net_x1_weekly": round(float(dfw["net_x1"].mean()), 5),
            "net_x2_weekly": round(float(dfw["net_x2"].mean()), 5),
            "P1": p1, "P2": p2, "P3": p3, "P4": p4,
        },
        "secondaries_exploratory": sec,
        "notes": [
            "Biais de survivance residuel (DefiLlama actuel) : SIGNAL_VALIDATED devrait etre reconfirme point-in-time avant cablage.",
            "Spread vs moyenne d'univers = orthogonal au marche par construction.",
        ],
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
