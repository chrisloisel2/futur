#!/usr/bin/env python3
"""
scripts/test_perp_listing_event_study.py
─────────────────────────────────────────────────────────────────────────────
Event study point-in-time des listings perp Binance (edge LISTING candidat).

Question : après l'ouverture d'un perp (heure exacte = onboardDate, connue à
l'avance), y a-t-il un drift exploitable — momentum de découverte ou reversion
post-euphorie — net de coûts ×2 ?

Design anti-lookahead :
  - t0 = premier kline 5m réellement tradé ;
  - entrée à t0+delay (close du dernier bar ≤ t0+delay), delay ∈ {30m…24h} ;
  - sortie à entry+horizon, horizon ∈ {1h…14j} ;
  - retours bruts, nets (40 bps aller-retour = 2× le coût projet, taker),
    et ajustés BTC (même fenêtre) ;
  - conditionnements uniquement sur info disponible à l'entrée (volume j0,
    retour première 24h, premier funding) — donc appliqués aux entrées ≥ 24h.

Honnêteté : listings groupés par batchs → N effectif < N ; biais de survivance
résiduel (délistés sans data fapi) rappelé en sortie.

    .venv/bin/python scripts/test_perp_listing_event_study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "listings_backfill" / "binance"
BTC_ENRICHED = ROOT / "data" / "enriched" / "BTCUSDT_1h_enriched.parquet"
REPORT = ROOT / "reports" / "LISTING_EVENT_STUDY.md"

ENTRY_DELAYS_H = [0.5, 1, 4, 8, 24, 72, 168]
HORIZONS_H = [1, 4, 12, 24, 48, 72, 168, 336]
COST_RT_BPS = 40.0        # 2 jambes taker × coût projet ×2 (règle "coûts ×2")


def load_price_series(sym: str) -> pd.Series | None:
    """Série close 5m (72 premières heures) prolongée en 1h (30 j)."""
    p5, p1 = DATA / "klines_5m" / f"{sym}.parquet", DATA / "klines_1h" / f"{sym}.parquet"
    parts = []
    if p5.exists():
        k5 = pd.read_parquet(p5)
        parts.append(k5.set_index("timestamp")["close"])
    if p1.exists():
        k1 = pd.read_parquet(p1)
        s1 = k1.set_index("timestamp")["close"]
        if parts:
            s1 = s1[s1.index > parts[0].index.max()]
        parts.append(s1)
    if not parts:
        return None
    s = pd.concat(parts).sort_index()
    return s[~s.index.duplicated()]


def price_at(s: pd.Series, ts: pd.Timestamp) -> float:
    """Dernier close ≤ ts (NaN si hors couverture)."""
    idx = s.index.searchsorted(ts, side="right") - 1
    if idx < 0 or idx >= len(s):
        return np.nan
    return float(s.iloc[idx])


def day0_features(sym: str, s: pd.Series) -> dict:
    """Features disponibles à t0+24h (pour conditionner les entrées 24h)."""
    t0 = s.index[0]
    feats = {"t0": t0}
    p1 = DATA / "klines_1h" / f"{sym}.parquet"
    if p1.exists():
        k1 = pd.read_parquet(p1)
        d0 = k1[k1["timestamp"] < t0 + pd.Timedelta(hours=24)]
        feats["qvol_24h"] = float(d0["quote_volume"].sum())
        feats["taker_buy_share_24h"] = (float(d0["taker_buy_quote"].sum() / d0["quote_volume"].sum())
                                        if d0["quote_volume"].sum() > 0 else np.nan)
    p0, p24 = price_at(s, t0 + pd.Timedelta(minutes=5)), price_at(s, t0 + pd.Timedelta(hours=24))
    feats["ret_24h"] = p24 / p0 - 1 if np.isfinite(p0) and np.isfinite(p24) and p0 > 0 else np.nan
    pf = DATA / "funding" / f"{sym}.parquet"
    if pf.exists():
        fu = pd.read_parquet(pf)
        fu24 = fu[fu["timestamp"] < t0 + pd.Timedelta(hours=24)]
        feats["first_funding"] = float(fu24["funding_rate"].iloc[0]) if len(fu24) else np.nan
    return feats


def main() -> None:
    cal = pd.read_parquet(DATA / "listings_calendar.parquet")
    btc = pd.read_parquet(BTC_ENRICHED, columns=["datetime", "close"])
    btc_s = btc.set_index(pd.DatetimeIndex(btc["datetime"]))["close"].sort_index()

    rows, feats_rows = [], []
    syms = sorted(p.stem for p in (DATA / "klines_1h").glob("*.parquet"))
    for sym in syms:
        s = load_price_series(sym)
        if s is None or len(s) < 24:
            continue
        t0 = s.index[0]
        pf = DATA / "funding" / f"{sym}.parquet"
        fu_s = (pd.read_parquet(pf).set_index("timestamp")["funding_rate"].sort_index()
                if pf.exists() else pd.Series(dtype=float))
        f = day0_features(sym, s)
        f["symbol"] = sym
        feats_rows.append(f)
        for d in ENTRY_DELAYS_H:
            t_in = t0 + pd.Timedelta(hours=d)
            p_in = price_at(s, t_in)
            if not np.isfinite(p_in) or p_in <= 0:
                continue
            for h in HORIZONS_H:
                t_out = t_in + pd.Timedelta(hours=h)
                p_out = price_at(s, t_out)
                if not np.isfinite(p_out):
                    continue
                # exiger une couverture réelle (pas un asof qui recule de > 2h)
                idx = s.index.searchsorted(t_out, side="right") - 1
                if t_out - s.index[idx] > pd.Timedelta(hours=2):
                    continue
                ret = p_out / p_in - 1
                b_in, b_out = price_at(btc_s, t_in), price_at(btc_s, t_out)
                ret_btc = (b_out / b_in - 1) if np.isfinite(b_in) and np.isfinite(b_out) else np.nan
                # funding cumulé sur la détention : un SHORT le REÇOIT (signe +)
                fund = float(fu_s[(fu_s.index > t_in) & (fu_s.index <= t_out)].sum()) \
                    if len(fu_s) else np.nan
                rows.append({"symbol": sym, "year": t0.year, "delay_h": d, "horizon_h": h,
                             "ret": ret, "fund": fund,
                             "ret_adj": ret - ret_btc if np.isfinite(ret_btc) else np.nan})

    ev = pd.DataFrame(rows)
    feats = pd.DataFrame(feats_rows)
    if not len(ev):
        print("Aucun événement exploitable — lancer d'abord le backfill.")
        return
    from src.institutional.data.atomic_parquet import atomic_write_parquet
    atomic_write_parquet(ev, DATA / "event_study_returns.parquet")
    atomic_write_parquet(feats, DATA / "event_study_features.parquet")

    def agg(g: pd.DataFrame) -> pd.Series:
        net = g["ret"] - COST_RT_BPS / 1e4
        # short : PnL = −ret + funding reçu (funding>0 ⇒ le short encaisse), net de coûts
        short_net = -g["ret"] + g["fund"].fillna(0) - COST_RT_BPS / 1e4
        return pd.Series({"n": len(g), "mean_bps": g["ret"].mean() * 1e4,
                          "med_bps": g["ret"].median() * 1e4,
                          "hit_%": (g["ret"] > 0).mean() * 100,
                          "net_mean_bps": net.mean() * 1e4,
                          "net_med_bps": net.median() * 1e4,
                          "adj_med_bps": g["ret_adj"].median() * 1e4,
                          "short_net_med_bps": short_net.median() * 1e4})

    lines = ["# Event study — listings perp Binance (point-in-time)",
             f"\nÉvénements : {ev['symbol'].nunique()} listings, "
             f"{feats['t0'].min().date()} → {feats['t0'].max().date()}. "
             f"Coût aller-retour {COST_RT_BPS:.0f} bps (×2). "
             "LONG spot du chiffre : net_med < 0 ⇒ pas d'edge long ; "
             "med très négatif ⇒ candidat fade/short (non tradé, SHORT_REJECTED).\n"]

    table = ev.groupby(["delay_h", "horizon_h"]).apply(agg).round(1)
    print("\n=== Tous listings — retours LONG entry t0+delay → +horizon ===")
    print(table.to_string())
    lines += ["\n## Tous listings (LONG)\n", "```", table.to_string(), "```"]

    print("\n=== Par cohorte annuelle (délai 1h, horizons 24/72/168h) ===")
    sub = ev[(ev["delay_h"] == 1) & (ev["horizon_h"].isin([24, 72, 168]))]
    coh = sub.groupby(["year", "horizon_h"]).apply(agg).round(1)
    print(coh.to_string())
    lines += ["\n## Cohortes annuelles (délai 1h)\n", "```", coh.to_string(), "```"]

    # Conditionnements point-in-time (entrées 24h uniquement)
    e24 = ev[ev["delay_h"] == 24].merge(feats, on="symbol", how="left")
    conds = [("ret_24h<0 (dump j0)", e24["ret_24h"] < 0),
             ("ret_24h>+20% (pump j0)", e24["ret_24h"] > 0.20),
             ("first_funding<0", e24["first_funding"] < 0),
             ("qvol_24h tercile haut", e24["qvol_24h"] > e24["qvol_24h"].quantile(2 / 3))]
    print("\n=== Entrée à t0+24h, conditionnée (info dispo à l'entrée) ===")
    lines.append("\n## Entrée t0+24h conditionnée\n\n```")
    for name, mask in conds:
        c = e24[mask & e24["horizon_h"].isin([24, 72, 168])]
        if not len(c):
            continue
        t = c.groupby("horizon_h").apply(agg).round(1)
        print(f"\n-- {name} --\n{t.to_string()}")
        lines.append(f"\n-- {name} --\n{t.to_string()}")
    lines.append("```")

    n_ev = ev["symbol"].nunique()
    lines += ["\n## Honnêteté\n",
              f"- {n_ev} événements mais listings par batchs corrélés → N effectif inférieur.",
              "- Biais de survivance résiduel : perps délistés sans data fapi exclus "
              "(voir listings_backfill_store.yaml `_meta.missing_delisted`).",
              "- PnL short = miroir du long SANS funding ni borrow ; SHORT_REJECTED reste la règle.",
              "- Slippage réel des premières heures > coût modélisé (books fins) — "
              "tout edge < ~100 bps net sur delay ≤ 1h est suspect."]
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"\nRapport → {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
