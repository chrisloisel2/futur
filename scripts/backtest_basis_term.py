#!/usr/bin/env python3
"""
scripts/backtest_basis_term.py
─────────────────────────────────────────────────────────────────────────────
BASIS_TERM — carry de structure par terme (cash-and-carry trimestriel).

Mécanisme : long spot + short quarterly quand le basis annualisé est riche ;
le basis converge MÉCANIQUEMENT vers 0 à l'échéance (delivery) → capture =
basis d'entrée − coûts. INDÉPENDANT du funding (aucune jambe perp).

Règles DÉCLARÉES (pas de tuning ; ladder de seuils RAPPORTÉE en entier) :
  • entrée : basis annualisé ≥ seuil (3/5/8 % rapportés, 5 % = primaire),
    10 ≤ jours restants ≤ 120, 1 position par actif à la fois ;
  • tenue jusqu'à échéance (convergence) ; MTM QUOTIDIEN → DD honnête ;
  • coûts : 23 bps totaux (spot 7 entrée + quarterly 7 entrée + spot 7 sortie
    + 2 delivery) ; référence S = close enriched perp (approximation spot
    documentée : |perp−spot| ≈ premium, quelques bps).
  • sizing sleeve : 25 % et 50 % du capital par actif, rapportés.

Sortie : reports/liq_cascade/BASIS_TERM_BACKTEST.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QDIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_quarterly"
OUT = ROOT / "reports" / "liq_cascade"
COSTS = 0.0023
ENTRY_LADDER = (0.03, 0.05, 0.08)
PRIMARY = 0.05
MIN_D, MAX_D = 10, 120


def spot_series(symbol: str) -> pd.Series:
    from src.institutional.engines.legacy_bridge import load_enriched
    df = load_enriched(symbol, required_cols=["close"])
    s = df.set_index(pd.to_datetime(df["datetime"], utc=True))["close"]
    return s.resample("D").last().dropna()


def contract_frames():
    reg = json.loads((QDIR / "contracts.json").read_text())
    out = []
    for c, meta in sorted(reg.items()):
        pq = QDIR / f"{c}_1d.parquet"
        if not pq.exists():
            continue
        df = pd.read_parquet(pq)
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.floor("D")
        out.append((c, meta["symbol"], pd.Timestamp(meta["expiry"], tz="UTC"),
                    df.set_index("date")["close"]))
    return out


def run_sleeve(entry_thr: float, sizing: float, spots) -> dict:
    """1 position/actif ; MTM quotidien ; PnL sur equity compoundée."""
    daily_pnl = {}   # date -> pnl equity-fraction
    trades = []
    open_until = {"BTCUSDT": pd.Timestamp("2000-01-01", tz="UTC"),
                  "ETHUSDT": pd.Timestamp("2000-01-01", tz="UTC")}
    for c, sym, expiry, F in contract_frames():
        S = spots[sym]
        idx = F.index.intersection(S.index)
        if len(idx) < 15:
            continue
        F2, S2 = F[idx], S[idx]
        days_left = (expiry - idx).days
        basis = F2 / S2 - 1
        ann = basis * 365 / np.maximum(days_left, 1)
        ok = (ann >= entry_thr) & (days_left >= MIN_D) & (days_left <= MAX_D)
        cand = np.flatnonzero(ok.values)
        if len(cand) == 0:
            continue
        # 1ère entrée éligible où l'actif est libre
        e = None
        for i in cand:
            if idx[i] > open_until[sym]:
                e = i
                break
        if e is None:
            continue
        open_until[sym] = expiry
        b0 = float(basis.iloc[e])
        # MTM quotidien : pnl_t = (b0 − b_t) sur le notional (short F, long S)
        window = range(e + 1, len(idx))
        prev_b = b0
        for i in window:
            b_t = float(basis.iloc[i])
            d = idx[i]
            daily_pnl[d] = daily_pnl.get(d, 0.0) + (prev_b - b_t) * sizing
            prev_b = b_t
        # convergence finale à l'échéance (b→0) + coûts
        daily_pnl[expiry] = (daily_pnl.get(expiry, 0.0)
                             + (prev_b - 0.0) * sizing - COSTS * sizing)
        trades.append({"contract": c, "symbol": sym, "entry": str(idx[e].date()),
                       "expiry": str(expiry.date()), "basis_entry": round(b0, 5),
                       "ann_at_entry": round(float(ann.iloc[e]), 4),
                       "days_held": int((expiry - idx[e]).days),
                       "capture_net": round(b0 - COSTS, 5)})
    if not daily_pnl:
        return {"trades": [], "stats": {}}
    pnl = pd.Series(daily_pnl).sort_index()
    eq = (1 + pnl).cumprod()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    by_year = {int(y): round(float((1 + g).prod() - 1), 4)
               for y, g in pnl.groupby(pnl.index.year)}
    return {"trades": trades,
            "equity": eq,
            "stats": {"n_trades": len(trades),
                      "roi_total": round(float(eq.iloc[-1] - 1), 4),
                      "roi_ann": round(float(eq.iloc[-1] ** (1 / yrs) - 1), 4),
                      "maxdd": round(dd, 4), "by_year": by_year,
                      "avg_capture_bps": round(float(np.mean(
                          [t["capture_net"] for t in trades])) * 1e4, 1)}}


def main():
    spots = {s: spot_series(s) for s in ("BTCUSDT", "ETHUSDT")}
    results = {"config": {"costs": COSTS, "primary_entry": PRIMARY,
                          "window_days": [MIN_D, MAX_D]}, "runs": {}}
    L = ["# BASIS_TERM — carry trimestriel (cash-and-carry, sans funding)\n"]
    eq_primary = None
    for thr in ENTRY_LADDER:
        for sz in (0.25, 0.50):
            r = run_sleeve(thr, sz, spots)
            key = f"entry{int(thr*100)}_size{int(sz*100)}"
            if not r["stats"]:
                continue
            results["runs"][key] = {"stats": r["stats"],
                                    "trades": r["trades"] if thr == PRIMARY else None}
            s = r["stats"]
            flag = " ← PRIMAIRE" if (thr == PRIMARY and sz == 0.50) else ""
            L.append(f"- **{key}** : {s['n_trades']} trades, capture moy "
                     f"{s['avg_capture_bps']:+.0f} bps, ROI {s['roi_total']*100:+.2f}% "
                     f"({s['roi_ann']*100:+.2f}%/an), maxDD {s['maxdd']*100:.2f}% | "
                     f"années {s['by_year']}{flag}")
            if thr == PRIMARY and sz == 0.50:
                eq_primary = r["equity"]
    if eq_primary is not None:
        pd.DataFrame({"date": eq_primary.index, "equity": eq_primary.values}
                     ).to_parquet(OUT / "basis_term_equity_daily.parquet", index=False)
    (OUT / "BASIS_TERM_BACKTEST.json").write_text(
        json.dumps({k: v for k, v in results.items()}, indent=2, default=str))
    (OUT / "BASIS_TERM_BACKTEST.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
