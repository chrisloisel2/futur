#!/usr/bin/env python3
"""
scripts/backtest_xs_momentum.py
─────────────────────────────────────────────────────────────────────────────
Momentum CROSS-SECTIONNEL hebdomadaire long-only sur l'univers enriched (50).

Base académique (la plus documentée des anomalies crypto) :
  • Liu, Tsyvinski & Wu (J. Finance 2022) — factor momentum 1-4 semaines
    dans la cross-section crypto ;
  • Tan & Wang (JFQA 2024) — CTREND : le signal tendance survit aux coûts
    sur les coins liquides.
Jamais testé dans ce repo sous forme FACTORIELLE LENTE (le CROSS_SECTIONAL
horaire rejeté en 2026-06 était un moteur ML à churn — autre objet).

Règles PRÉ-DÉCLARÉES (aucun tuning post-hoc ; primaire unique, sensibilités
rapportées mais revendication ancrée sur le primaire) :
  • rebalance hebdo lundi 00:00 UTC ;
  • signal = retour 28j (4 semaines) avec skip du dernier jour
    (P[t-1j]/P[t-28j] − 1) ;
  • portefeuille = top-5 équipondéré, LONG ONLY (SHORT interdit projet) ;
  • gate régime = BTC close > MA20j (même gate trend que le live déployé) ;
    sinon 100 % cash ;
  • coûts = 15 bps par side × turnover (30 bps aller-retour) ;
  • univers = tout actif enriched avec ≥ 29j d'historique à la date t.

⚠ BIAIS DE SURVIVANCE DOCUMENTÉ : l'univers des 50 a été sélectionné en 2026
(survivants — pas de LUNA/FTT). Le chiffre est une BORNE HAUTE ; toute
promotion exige la fenêtre paper forward.

Sorties : reports/xs_momentum/XS_MOMENTUM_BACKTEST.{json,md}
          reports/xs_momentum/xs_momentum_equity_daily.parquet
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ENR = ROOT / "data" / "enriched"
OUT = ROOT / "reports" / "xs_momentum"
LOOKBACK_D = 28
SKIP_D = 1
TOP_K = 5
COST_SIDE = 0.0015
MA_GATE_D = 20


def load_daily_closes() -> pd.DataFrame:
    cols = {}
    for p in sorted(ENR.glob("*_1h_enriched.parquet")):
        sym = p.name.split("_")[0]
        d = pd.read_parquet(p, columns=["datetime", "close"])
        d["datetime"] = pd.to_datetime(d["datetime"], utc=True)
        s = (d.set_index("datetime")["close"].sort_index()
              .resample("1D").last())
        cols[sym] = s
    panel = pd.DataFrame(cols).sort_index()
    return panel


def run(panel: pd.DataFrame, top_k: int, gate: bool,
        require_pos: bool, weighting: str) -> dict:
    btc = panel["BTCUSDT"]
    btc_ma = btc.rolling(MA_GATE_D).mean()
    rets = panel.pct_change()
    # rebalances = lundis où BTC a un historique MA complet
    mondays = [d for d in panel.index
               if d.dayofweek == 0 and not np.isnan(btc_ma.loc[d])]
    daily = []      # (date, ret_net)
    weights = pd.Series(dtype=float)
    n_reb, n_gated = 0, 0
    for i, t in enumerate(mondays):
        nxt = mondays[i + 1] if i + 1 < len(mondays) else panel.index[-1]
        sig_hi = panel.loc[:t].index[-1 - SKIP_D] if len(panel.loc[:t]) > SKIP_D else None
        new_w = pd.Series(dtype=float)
        gated = gate and not (btc.loc[t] > btc_ma.loc[t])
        if gated:
            n_gated += 1
        elif sig_hi is not None:
            px_now = panel.loc[sig_hi]
            idx_lo = panel.index.searchsorted(t - pd.Timedelta(days=LOOKBACK_D))
            px_then = panel.iloc[idx_lo]
            sig = (px_now / px_then - 1).dropna()
            # exiger l'historique complet du lookback
            enough = panel.iloc[max(0, idx_lo - 1)].notna()
            sig = sig[enough.reindex(sig.index, fill_value=False)]
            if require_pos:
                sig = sig[sig > 0]
            top = sig.nlargest(top_k)
            if len(top) > 0:
                if weighting == "inv_vol":
                    vol = rets[top.index].loc[:t].tail(28).std()
                    iv = (1 / vol.replace(0, np.nan)).fillna(0)
                    new_w = iv / iv.sum() if iv.sum() > 0 else pd.Series(dtype=float)
                else:
                    new_w = pd.Series(1 / len(top), index=top.index)
        # coût de turnover à la bascule
        turn = float((new_w.subtract(weights, fill_value=0)).abs().sum())
        cost = turn * COST_SIDE
        n_reb += 1
        weights = new_w
        window = panel.loc[t:nxt].index[1:]
        first = True
        for d in window:
            r = float((rets.loc[d].reindex(weights.index).fillna(0) * weights).sum()) \
                if len(weights) else 0.0
            daily.append((d, r - (cost if first else 0.0)))
            first = False
        if len(window) == 0 and cost > 0:
            daily.append((nxt, -cost))
    eq = pd.DataFrame(daily, columns=["date", "ret"]).drop_duplicates("date")
    eq = eq.set_index("date").sort_index()
    eq["equity"] = (1 + eq["ret"]).cumprod()
    e = eq["equity"]
    dd = float(((e - e.cummax()) / e.cummax()).min())
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    ann = float(e.iloc[-1] ** (1 / yrs) - 1)
    sharpe = float(eq["ret"].mean() / (eq["ret"].std() + 1e-12) * np.sqrt(365))
    by_year = {}
    for y, g in eq.groupby(eq.index.year):
        gy = (1 + g["ret"]).cumprod()
        ddy = float(((gy - gy.cummax()) / gy.cummax()).min())
        by_year[int(y)] = {"roi": round(float(gy.iloc[-1] - 1), 4),
                           "maxdd": round(ddy, 4)}
    return {"ann": round(ann, 4), "total": round(float(e.iloc[-1] - 1), 4),
            "maxdd": round(dd, 4), "sharpe": round(sharpe, 2),
            "n_reb": n_reb, "n_gated_weeks": n_gated,
            "by_year": by_year, "_equity": eq}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panel = load_daily_closes()
    print(f"panel : {panel.shape[1]} actifs × {panel.shape[0]} jours "
          f"({panel.index[0].date()} → {panel.index[-1].date()})")

    configs = {
        "PRIMARY_top5_eq_gate": dict(top_k=5, gate=True, require_pos=False,
                                     weighting="eq"),
        "sens_no_gate": dict(top_k=5, gate=False, require_pos=False,
                             weighting="eq"),
        "sens_pos_only": dict(top_k=5, gate=True, require_pos=True,
                              weighting="eq"),
        "sens_top3": dict(top_k=3, gate=True, require_pos=False,
                          weighting="eq"),
        "sens_top10": dict(top_k=10, gate=True, require_pos=False,
                           weighting="eq"),
        "sens_inv_vol": dict(top_k=5, gate=True, require_pos=False,
                             weighting="inv_vol"),
    }
    results, lines = {}, [
        "# Momentum cross-sectionnel hebdo — univers enriched 50 (long-only)\n",
        "Règles pré-déclarées (voir docstring). ⚠ univers 2026 = biais de "
        "survivance → borne haute ; promotion = paper forward obligatoire.\n",
        "| config | %/an | total | maxDD | Sharpe | semaines gated |",
        "|---|---:|---:|---:|---:|---:|"]
    for name, cfg in configs.items():
        r = run(panel, **cfg)
        eq = r.pop("_equity")
        if name.startswith("PRIMARY"):
            eq[["ret", "equity"]].to_parquet(OUT / "xs_momentum_equity_daily.parquet")
        results[name] = r
        lines.append(f"| {name} | {r['ann']*100:+.1f}% | {r['total']*100:+.1f}% | "
                     f"{r['maxdd']*100:.1f}% | {r['sharpe']} | {r['n_gated_weeks']} |")
        print(f"{name:24} ann={r['ann']*100:+6.1f}% total={r['total']*100:+7.1f}% "
              f"DD={r['maxdd']*100:6.1f}% sharpe={r['sharpe']:5.2f} "
              f"gated={r['n_gated_weeks']}", flush=True)
        print("   ", {y: d['roi'] for y, d in sorted(r['by_year'].items())}, flush=True)

    lines.append("\n## Par année (PRIMARY)\n")
    rows = [{"year": y, **d} for y, d in
            sorted(results["PRIMARY_top5_eq_gate"]["by_year"].items())]
    lines.append(pd.DataFrame(rows).to_markdown(index=False))
    (OUT / "XS_MOMENTUM_BACKTEST.json").write_text(
        json.dumps(results, indent=2, default=str))
    (OUT / "XS_MOMENTUM_BACKTEST.md").write_text("\n".join(lines))
    print(f"→ {OUT}/XS_MOMENTUM_BACKTEST.md")


if __name__ == "__main__":
    main()
