#!/usr/bin/env python
"""T1a — SEMANTIQUE DU SIGNE DE LIQUIDATION (H1 du preregistrement).

Question : dans les donnees brutes, un ordre de liquidation SELL est-il la
fermeture forcee d'un LONG (et BUY d'un SHORT) ?

Test : sur barres 5-min, imbalance = (short_liq - long_liq)/(short_liq+long_liq)
doit etre POSITIVEMENT correle au rendement contemporain (achats forces -> prix up).

Trois sources INDEPENDANTES :
  - OKX      : posSide explicite (long|short) -> non ambigu, verite terrain
  - Bybit    : allLiquidation, normalisation appliquee par le collecteur du projet
  - Binance Vision COIN-M : champ `side` BRUT de Binance, jamais retouche ici

Prix : px implicite 5-min du detecteur figé (sum_open_interest_value/sum_open_interest)
       -> exactement la serie que voit liq_cascade/detector.py
"""
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path("/home/qbee/futur")
OUT = Path(__file__).resolve().parent
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def px_5m(sym):
    p = ROOT / "data/derivatives_backfill/binance_vision_metrics" / f"{sym}_metrics_5m.parquet"
    d = pd.read_parquet(p, columns=["create_time", "sum_open_interest", "sum_open_interest_value"])
    d["create_time"] = pd.to_datetime(d["create_time"], utc=True)
    oi = d["sum_open_interest"].astype(float)
    oiv = d["sum_open_interest_value"].astype(float)
    px = np.where(oi > 0, oiv / oi, np.nan)
    d["px"] = np.where(px > 0, px, np.nan)
    return d[["create_time", "px"]].sort_values("create_time").reset_index(drop=True)


def liq_5m_raw(glob_pat, side_expr):
    """Agrege les liquidations en barres 5-min. side_expr doit produire 'LONG'/'SHORT'
    = le type de POSITION liquidee selon la convention testee."""
    q = f"""
    SELECT
      time_bucket(INTERVAL '5 minutes', to_timestamp(timestamp/1000.0)) AS t,
      SUM(CASE WHEN {side_expr}='LONG'  THEN usd ELSE 0 END) AS long_liq_usd,
      SUM(CASE WHEN {side_expr}='SHORT' THEN usd ELSE 0 END) AS short_liq_usd,
      COUNT(*) AS n
    FROM read_parquet('{glob_pat}')
    GROUP BY 1 ORDER BY 1
    """
    df = duckdb.sql(q).df()
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df


def stats(df, label):
    """df: t, long_liq_usd, short_liq_usd joint avec px. Retourne correlations."""
    d = df.dropna(subset=["px", "px_prev"]).copy()
    d = d[(d["long_liq_usd"] + d["short_liq_usd"]) > 0]
    d["ret"] = np.log(d["px"] / d["px_prev"])
    d = d[np.isfinite(d["ret"])]
    tot = d["long_liq_usd"] + d["short_liq_usd"]
    d["imb"] = (d["short_liq_usd"] - d["long_liq_usd"]) / tot
    n = len(d)
    if n < 30:
        return {"source": label, "n_bars": n, "status": "TOO_FEW"}
    r = float(np.corrcoef(d["imb"], d["ret"])[0, 1])
    t = r * np.sqrt(max(n - 2, 1)) / np.sqrt(max(1e-12, 1 - r * r))
    # test non parametrique : signe du rendement median par bucket d'imbalance
    pure_short = d[d["imb"] > 0.9]   # quasi exclusivement liquidations de SHORTS
    pure_long = d[d["imb"] < -0.9]   # quasi exclusivement liquidations de LONGS
    return {
        "source": label,
        "n_bars": int(n),
        "corr_imb_ret": round(r, 4),
        "t_stat": round(float(t), 2),
        "mean_ret_bps_pure_short_liq": round(float(pure_short["ret"].mean() * 1e4), 2) if len(pure_short) > 20 else None,
        "n_pure_short": int(len(pure_short)),
        "mean_ret_bps_pure_long_liq": round(float(pure_long["ret"].mean() * 1e4), 2) if len(pure_long) > 20 else None,
        "n_pure_long": int(len(pure_long)),
        "frac_up_when_pure_short": round(float((pure_short["ret"] > 0).mean()), 4) if len(pure_short) > 20 else None,
        "frac_up_when_pure_long": round(float((pure_long["ret"] > 0).mean()), 4) if len(pure_long) > 20 else None,
        "date_min": str(d["t"].min()), "date_max": str(d["t"].max()),
    }


def main():
    res = []

    for sym in SYMS:
        px = px_5m(sym)
        px["px_prev"] = px["px"].shift(1)

        # ── OKX : posSide explicite. side_raw = "<orderside>/<posSide>" ──
        okx_glob = str(ROOT / f"data/derivatives_raw/exchange=okx/market=swap/stream=force_order/symbol={sym}/date=*/*.parquet")
        try:
            okx = liq_5m_raw(okx_glob, "CASE WHEN split_part(side_raw,'/',2)='long' THEN 'LONG' ELSE 'SHORT' END")
            m = okx.merge(px, left_on="t", right_on="create_time", how="inner")
            res.append(dict(symbol=sym, **stats(m, "OKX_posSide_explicit")))
        except Exception as e:
            res.append({"symbol": sym, "source": "OKX_posSide_explicit", "error": str(e)[:200]})

        # ── Bybit : convention NORMALISEE du projet (side SELL == long liquide) ──
        by_glob = str(ROOT / f"data/derivatives_raw/exchange=bybit/market=linear/stream=force_order/symbol={sym}/date=*/*.parquet")
        try:
            by = liq_5m_raw(by_glob, "CASE WHEN side='SELL' THEN 'LONG' ELSE 'SHORT' END")
            m = by.merge(px, left_on="t", right_on="create_time", how="inner")
            res.append(dict(symbol=sym, **stats(m, "BYBIT_normalized_side")))
        except Exception as e:
            res.append({"symbol": sym, "source": "BYBIT_normalized_side", "error": str(e)[:200]})

        # ── Bybit : convention BRUTE Bybit (side_raw) testee a l'envers pour controle ──
        try:
            by2 = liq_5m_raw(by_glob, "CASE WHEN side_raw='Buy' THEN 'SHORT' ELSE 'LONG' END")
            m = by2.merge(px, left_on="t", right_on="create_time", how="inner")
            res.append(dict(symbol=sym, **stats(m, "BYBIT_raw_side_AS_IF_orderside")))
        except Exception as e:
            res.append({"symbol": sym, "source": "BYBIT_raw_side_AS_IF_orderside", "error": str(e)[:200]})

    # ── Binance Vision COIN-M : champ `side` BRUT Binance forceOrder ──
    # convention testee : side=SELL  => ordre de vente force => LONG liquide
    for coin in ["BTCUSD_PERP", "ETHUSD_PERP"]:
        p = ROOT / "data/derivatives_backfill/binance_vision_liquidation" / f"{coin}_liq.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d["time"] = pd.to_datetime(d["time"], utc=True)
        d["usd"] = d["original_quantity"].astype(float)   # COIN-M : quantite en USD de contrat
        d["t"] = d["time"].dt.floor("5min")
        g = d.groupby("t").apply(lambda x: pd.Series({
            "long_liq_usd": x.loc[x["side"] == "SELL", "usd"].sum(),
            "short_liq_usd": x.loc[x["side"] == "BUY", "usd"].sum(),
            "n": len(x)})).reset_index()
        sym = "BTCUSDT" if coin.startswith("BTC") else "ETHUSDT"
        px = px_5m(sym); px["px_prev"] = px["px"].shift(1)
        m = g.merge(px, left_on="t", right_on="create_time", how="inner")
        res.append(dict(symbol=coin, **stats(m, "BINANCE_VISION_COINM_raw_side")))

    out = OUT / "t1a_sign_semantics.json"
    out.write_text(json.dumps(res, indent=2))
    for r in res:
        print(json.dumps(r))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
