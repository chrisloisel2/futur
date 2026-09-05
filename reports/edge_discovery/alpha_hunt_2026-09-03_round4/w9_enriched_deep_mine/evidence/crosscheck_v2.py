#!/usr/bin/env python3
"""W9 Phase 1 / test A8 — concordance data/enriched (1h) vs futur-data-v2 panel PIT V2.

Compare, heure par heure et pour un echantillon de symboles :
  - close 1h enrichi  vs  close 1h reconstruit depuis v2 perp_ohlcv (5m -> last close)
  - close 1h enrichi  vs  close 1h reconstruit depuis v2 spot_ohlcv
  - volume, number_of_trades, taker_buy_base_asset_volume
Sortie : evidence/CROSSCHECK_V2.csv  (une ligne par symbole x annee x source)
Aucun intermediaire ecrit sur disque : tout en DuckDB streaming.
Usage: .venv/bin/python evidence/crosscheck_v2.py
"""
import os, duckdb, pandas as pd, numpy as np

ROOT = "/home/qbee/futur"
V2   = "/home/qbee/futur-data-v2/data_v2/normalized"
HERE = os.path.dirname(os.path.abspath(__file__))
SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","ADAUSDT","XRPUSDT","DOGEUSDT","LINKUSDT",
        "AVAXUSDT","BNBUSDT","DOTUSDT","MKRUSDT","AAVEUSDT","PEPEUSDT","TRXUSDT","LTCUSDT"]
# renommages connus du projet
V2NAME = {"PEPEUSDT": "1000PEPEUSDT", "RNDRUSDT": "RENDERUSDT"}

con = duckdb.connect()
con.execute("SET memory_limit='1500MB'; SET threads=2; SET TimeZone='UTC';")

def v2_hourly(kind, sym):
    """agrege les barres 5m v2 en barres 1h (close = derniere, volume = somme)."""
    v2s = V2NAME.get(sym, sym)
    path = f"{V2}/{kind}_ohlcv/venue=binance/symbol={v2s}/year=*/{'perp' if kind=='perp' else 'spot'}_5m.parquet"
    try:
        return con.execute(f"""
          SELECT date_trunc('hour', timestamp) AS h,
                 last(close ORDER BY timestamp)  AS c2,
                 sum(volume)                     AS v2vol,
                 sum(number_of_trades)           AS v2ntr,
                 sum(taker_buy_base_asset_volume) AS v2tbb
          FROM read_parquet('{path}') GROUP BY 1
        """).fetchdf()
    except Exception as e:
        return None

rows = []
for sym in SYMS:
    f = f"{ROOT}/data/enriched/{sym}_1h_enriched.parquet"
    if not os.path.exists(f):
        continue
    en = con.execute(f"""
      SELECT date_trunc('hour', datetime) AS h, close AS c1, volume AS v1,
             number_of_trades AS ntr1, taker_buy_base_asset_volume AS tbb1,
             taker_buy_quote_asset_volume AS tbq1, quote_asset_volume AS qav1
      FROM read_parquet('{f}')
    """).fetchdf()
    for kind in ("perp", "spot"):
        v2 = v2_hourly(kind, sym)
        if v2 is None or not len(v2):
            continue
        m = en.merge(v2, on="h", how="inner")
        if len(m) < 500:
            continue
        m["year"] = pd.to_datetime(m["h"], utc=True).dt.year
        for y, g in m.groupby("year"):
            if len(g) < 200:
                continue
            d = np.abs(g["c1"].astype(float) / g["c2"].astype(float) - 1.0) * 1e4  # bps
            dv = np.abs(g["v1"].astype(float) / g["v2vol"].astype(float).replace(0, np.nan) - 1.0)
            rows.append(dict(
                symbol=sym, source=kind, year=int(y), n_hours=int(len(g)),
                close_med_abs_dev_bps=round(float(np.nanmedian(d)), 3),
                close_p99_abs_dev_bps=round(float(np.nanpercentile(d, 99)), 2),
                frac_close_dev_gt_1bp=round(float(np.nanmean(d > 1.0)), 4),
                vol_med_abs_rel_dev=round(float(np.nanmedian(dv)), 4),
                ntr_enriched_zero_rate=round(float((g["ntr1"].fillna(0) == 0).mean()), 4),
                tbb_eq_half_vol_rate=round(float((np.abs(g["tbb1"].astype(float) - 0.5*g["v1"].astype(float)) < 1e-9).mean()), 4),
                tbq_eq_half_qav_rate=round(float((np.abs(g["tbq1"].astype(float) - 0.5*g["qav1"].astype(float)) < 1e-9).mean()), 4),
                tbb_med_abs_rel_dev_vs_v2=round(float(np.nanmedian(np.abs(
                    g["tbb1"].astype(float) / g["v2tbb"].astype(float).replace(0, np.nan) - 1.0))), 4),
            ))
    print(f"{sym}: done", flush=True)

out = pd.DataFrame(rows).sort_values(["symbol", "source", "year"])
out.to_csv(HERE + "/CROSSCHECK_V2.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 400)
print(out.to_string(index=False))
