#!/usr/bin/env python3
"""
scripts/backtest_ctrend_v0.py
─────────────────────────────────────────────────────────────────────────────
CTREND v0 — baseline cross-sectional trend, PROTOCOLE PRÉ-ENREGISTRÉ.

Piste #1 du plan Edge Factory (2026-07-17). Référence académique : trend
factor cross-section crypto (JFQA 2024). v0 est une BASELINE DE CADRAGE sur
l'univers 50 courant — donc **biais de survivance assumé** (univers choisi
en 2026). Le verdict de tradabilité appartient à CTREND v1 (univers
point-in-time). Aucun paramètre n'est optimisé ; un seul run.

Protocole (fixé avant exécution, aucune itération) :
  - univers : les 50 symboles du collecteur dérivés (UNIVERSE_50) ;
  - données : klines 1d Vision um (data/derivatives_backfill/um_klines_1d) ;
  - score(t) = moyenne des z-scores cross-section des rendements
    {1, 3, 7, 14, 30} jours (equal-weight, prix seulement) ;
  - portefeuille : long-only top-5 équipondéré parmi score > 0 ;
    slots vides → cash ;
  - gate régime : BTC close > MA20 sinon 100 % cash ;
  - rebalance hebdomadaire (7 barres), exécution à l'OPEN de la barre
    suivante (signal au close t, exécution open t+1) ;
  - coûts : 30 bps aller-retour appliqués au turnover, rapportés ×1 et ×2 ;
  - pas de forward-fill de rendements : un symbole sans barre = exclu du
    score ce jour-là.

Sortie : reports/ctrend/CTREND_V0_RESULT.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.data.positioning_archiver import UNIVERSE_50  # noqa: E402

DATA_DIR = ROOT / "data" / "derivatives_backfill" / "um_klines_1d"
OUT_DIR = ROOT / "reports" / "ctrend"

LOOKBACKS = [1, 3, 7, 14, 30]
TOP_K = 5
REBALANCE_DAYS = 7
BTC_MA = 20
COST_RT = 0.0030          # 30 bps aller-retour
START = "2020-01-01"


def load_panel():
    closes, opens = {}, {}
    for sym in UNIVERSE_50:
        pq = DATA_DIR / f"{sym}_1d.parquet"
        if not pq.exists():
            continue
        df = pd.read_parquet(pq, columns=["open_time", "open", "close"])
        df = df.set_index("open_time").sort_index()
        closes[sym] = df["close"]
        opens[sym] = df["open"]
    close = pd.DataFrame(closes).loc[START:]
    open_ = pd.DataFrame(opens).loc[START:]
    return close, open_


def compute_scores(close: pd.DataFrame) -> pd.DataFrame:
    zs = []
    for lb in LOOKBACKS:
        r = close.pct_change(lb)
        z = r.sub(r.mean(axis=1), axis=0).div(r.std(axis=1), axis=0)
        zs.append(z)
    return sum(zs) / len(zs)


def run(cost_mult: float, close, open_, scores, gate):
    """Backtest événementiel simple : poids cibles au rebalance, PnL à l'open
    suivant, coûts sur turnover. Retourne série equity quotidienne."""
    dates = close.index
    w = pd.Series(0.0, index=close.columns)     # poids courants (fraction NAV)
    equity = 1.0
    rows = []
    last_reb = -10**9
    # rendement open→open décalé : position prise à l'open t+1 gagne
    # open(t+1)→open(t+2). On approxime en appliquant le rendement
    # close→close de t+1 aux poids décidés au close t (barre suivante).
    ret_cc = close.pct_change()

    for i in range(1, len(dates) - 1):
        t = dates[i]
        # PnL du jour avec les poids en place (décidés au plus tard hier)
        r = ret_cc.loc[t].fillna(0.0)
        day_ret = float((w * r).sum())
        equity *= (1.0 + day_ret)

        # décision au close t, appliquée (coûts) immédiatement après
        if i - last_reb >= REBALANCE_DAYS:
            if not gate.loc[t]:
                target = pd.Series(0.0, index=close.columns)
            else:
                s = scores.loc[t].dropna()
                s = s[s > 0].nlargest(TOP_K)
                target = pd.Series(0.0, index=close.columns)
                if len(s):
                    target[s.index] = 1.0 / TOP_K
            turnover = float((target - w).abs().sum())
            cost = turnover * (COST_RT / 2.0) * cost_mult
            equity *= (1.0 - cost)
            w = target
            last_reb = i
        rows.append((t, equity, float(w.sum())))

    eq = pd.DataFrame(rows, columns=["date", "equity", "gross"]).set_index("date")
    return eq


def metrics(eq: pd.Series) -> dict:
    ret_d = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = float(eq.iloc[-1] ** (1 / years) - 1)
    monthly = eq.resample("M").last().pct_change().dropna()
    dd = (eq / eq.cummax() - 1.0)
    sharpe = float(ret_d.mean() / ret_d.std() * np.sqrt(365)) if ret_d.std() else 0.0
    # contribution top mois/jours (en log-points du PnL total)
    lr_d = np.log1p(ret_d)
    lr_m = np.log1p(monthly)
    tot = float(lr_d.sum())
    top3m = float(lr_m.nlargest(3).sum() / tot) if tot else np.nan
    top10d = float(lr_d.nlargest(10).sum() / tot) if tot else np.nan
    per_year = {str(y): float(v) for y, v in
                eq.resample("Y").last().pct_change().dropna().items()}
    per_year_named = {k[:4]: round(v, 4) for k, v in per_year.items()}
    return {
        "cagr": round(cagr, 4),
        "cmgr": round((1 + cagr) ** (1 / 12) - 1, 4),
        "monthly_mean": round(float(monthly.mean()), 4),
        "monthly_median": round(float(monthly.median()), 4),
        "monthly_positive_share": round(float((monthly > 0).mean()), 4),
        "sharpe_daily_ann": round(sharpe, 2),
        "max_dd": round(float(dd.min()), 4),
        "per_year": per_year_named,
        "top3_months_share_of_pnl": round(top3m, 3),
        "top10_days_share_of_pnl": round(top10d, 3),
        "n_months": int(len(monthly)),
    }


def main():
    close, open_ = load_panel()
    scores = compute_scores(close)
    btc = close["BTCUSDT"]
    gate = (btc > btc.rolling(BTC_MA).mean()).fillna(False)

    out = {"strategy": "CTREND_V0", "protocol": {
        "universe": "UNIVERSE_50 (courant, BIAIS DE SURVIVANCE ASSUMÉ)",
        "lookbacks_days": LOOKBACKS, "top_k": TOP_K,
        "rebalance_days": REBALANCE_DAYS, "gate": f"BTC>MA{BTC_MA}",
        "cost_rt_bps": COST_RT * 1e4, "execution": "barre suivante",
        "no_forward_fill": True, "start": START,
        "preregistered": True, "n_runs": 1}}

    for label, mult in [("cost_x1", 1.0), ("cost_x2", 2.0)]:
        eq = run(mult, close, open_, scores, gate)
        out[label] = metrics(eq["equity"])
        eq.to_parquet(OUT_DIR / f"equity_{label}.parquet")

    h = hashlib.sha256()
    for sym in sorted(close.columns):
        pq = DATA_DIR / f"{sym}_1d.parquet"
        h.update(pq.read_bytes()[:1 << 16])
    out["environment"] = {
        "cutoff_data": str(close.index[-1].date()),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "data_hash_sha256_16": h.hexdigest()[:16],
        "n_symbols_loaded": int(close.shape[1]),
        "python": sys.version.split()[0],
        "command": ".venv/bin/python scripts/backtest_ctrend_v0.py",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "CTREND_V0_RESULT.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
