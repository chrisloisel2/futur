#!/usr/bin/env python3
"""
scripts/optimize_long_sleeve.py
─────────────────────────────────────────────────────────────────────────────
Répare le sleeve LONG (source unique du drawdown, cf. audit). Mesure — pas
d'affirmation — les techniques des fonds systématiques rentables :

  A. REGIME_ONLY   : gate actuel (BTC > EMA200) — le baseline.
  B. +TREND        : overlay trend-following — long actif SEULEMENT si régime
                     bull ET actif > sa MA20 (ne pas tenir dans un repli).
  C. +TREND+IVOL   : B + sizing inverse-volatilité (risk parity : SOL très
                     volatil pèse moins que BTC).
  D. +TREND+IVOL+VOLTGT : C + volatility targeting (échelle l'expo pour viser
                     une vol cible constante — cœur de l'approche AQR/Man).

Actifs long : BTC/ETH/SOL. Coûts 6 bps/rotation. Fenêtre 2022→2026.
Sortie : reports/liq_cascade/LONG_SLEEVE_OPTIMIZATION.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.engines.legacy_bridge import load_enriched

OUT = ROOT / "reports" / "liq_cascade"
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
COST = 0.0006
TARGET_VOL = 0.15          # 15%/an annualisé (vol targeting)
START = "2022-01-01"


def daily(sym):
    df = load_enriched(sym, required_cols=["close"])
    s = df.set_index(pd.to_datetime(df["datetime"], utc=True))["close"].resample("D").last().dropna()
    return s[s.index >= pd.Timestamp(START, tz="UTC")]


def metrics(ret: pd.Series) -> dict:
    ret = ret.dropna()
    if len(ret) < 30:
        return {}
    eq = (1 + ret).cumprod()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = (ret.index[-1] - ret.index[0]).days / 365.25
    ann = float(eq.iloc[-1] ** (1 / yrs) - 1)
    vol = float(ret.std() * np.sqrt(365))
    return {"roi_ann": round(ann, 4), "vol": round(vol, 4),
            "maxdd": round(dd, 4),
            "sharpe": round(float(ret.mean() / (ret.std() + 1e-12) * np.sqrt(365)), 2),
            "roi_total": round(float(eq.iloc[-1] - 1), 4)}


def build():
    px = {s: daily(s) for s in ASSETS}
    idx = sorted(set().union(*[set(s.index) for s in px.values()]))
    idx = pd.DatetimeIndex(idx)
    close = pd.DataFrame({s: px[s].reindex(idx).ffill() for s in ASSETS})
    ret = close.pct_change()
    btc = close["BTCUSDT"]
    ema200 = btc.ewm(span=200, adjust=False).mean()
    regime_bull = (btc > ema200)                      # gate macro (causal)
    ma20 = close.rolling(20).mean()
    trend = close > ma20                              # overlay trend/actif
    vol20 = ret.rolling(20).std() * np.sqrt(365)      # vol réalisée/actif
    return close, ret, regime_bull, trend, vol20


def sim(ret, weights_t, gate_t):
    """weights_t: DataFrame poids/actif (t) ; gate_t: bool/actif (t, applique décalé)."""
    w = (weights_t * gate_t.astype(float)).shift(1).fillna(0.0)   # décision à t, expo t+1
    gross = (w * ret).sum(axis=1)
    turnover = w.diff().abs().sum(axis=1).fillna(0.0)
    return gross - turnover * COST


def main():
    close, ret, regime_bull, trend, vol20 = build()
    eqw = pd.DataFrame(1/3, index=ret.index, columns=ASSETS)
    rb = pd.concat([regime_bull] * 3, axis=1); rb.columns = ASSETS

    # A. régime seul, équipondéré
    A = sim(ret, eqw, rb)
    # B. + trend filter (régime ET actif>MA20)
    gate_B = rb & trend
    B = sim(ret, eqw, gate_B)
    # C. + inverse-vol (risk parity)
    inv = (1.0 / vol20).replace([np.inf, -np.inf], np.nan)
    ivw = inv.div(inv.sum(axis=1), axis=0).fillna(1/3)
    C = sim(ret, ivw, gate_B)
    # D. + volatility targeting sur le sleeve
    base = sim(ret, ivw, gate_B)
    realized = base.rolling(20).std() * np.sqrt(365)
    scale = (TARGET_VOL / realized.replace(0, np.nan)).clip(upper=3.0).shift(1).fillna(1.0)
    D = base * scale

    res = {"A_regime_only": metrics(A), "B_plus_trend": metrics(B),
           "C_plus_ivol": metrics(C), "D_plus_voltarget": metrics(D),
           "current_ivol_weights": {s: round(float(ivw[s].iloc[-1]), 3) for s in ASSETS},
           "trend_now": {s: bool(trend[s].iloc[-1]) for s in ASSETS},
           "regime_now": bool(regime_bull.iloc[-1])}

    L = ["# Optimisation du sleeve LONG — mesuré 2022→2026 (net de coûts)\n",
         "| config | ROI/an | vol | maxDD | Sharpe |", "|---|---:|---:|---:|---:|"]
    for k, lab in [("A_regime_only", "A · régime seul (ACTUEL)"),
                   ("B_plus_trend", "B · +trend filter"),
                   ("C_plus_ivol", "C · +inverse-vol"),
                   ("D_plus_voltarget", "D · +vol targeting")]:
        m = res[k]
        L.append(f"| {lab} | {m['roi_ann']*100:+.1f}% | {m['vol']*100:.1f}% | "
                 f"{m['maxdd']*100:.1f}% | {m['sharpe']} |")
    L.append(f"\nPoids inverse-vol actuels : {res['current_ivol_weights']}")
    L.append(f"Trend actuel (>MA20) : {res['trend_now']} · régime bull : {res['regime_now']}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "LONG_SLEEVE_OPTIMIZATION.json").write_text(json.dumps(res, indent=2))
    (OUT / "LONG_SLEEVE_OPTIMIZATION.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
