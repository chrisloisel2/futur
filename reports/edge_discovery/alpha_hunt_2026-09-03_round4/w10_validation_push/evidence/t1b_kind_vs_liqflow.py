#!/usr/bin/env python
"""T1b — Le label `kind` du detecteur fige correspond-il au flux de liquidation reel ?
(H2 du preregistrement)

On rejoue le DETECTEUR FIGE (import en lecture seule, aucun fichier src/ touche) sur la
fenetre ou les liquidations brutes existent (forceOrder collecte depuis 2026-07-04), puis
on mesure, dans +/-30 min autour de chaque event, la part de liquidations de SHORTS.

Convention etablie par T1a (3 sources independantes, t=30..65) :
    side == 'SELL'  <=> LONG  liquide (vente forcee)
    side == 'BUY'   <=> SHORT liquide (achat force)
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

import sys
sys.path.insert(0, "/home/qbee/futur")
from src.institutional.engines.liq_cascade.detector import (  # noqa: E402
    CascadeConfig, detect_cascades, load_metrics,
)

ROOT = Path("/home/qbee/futur")
OUT = Path(__file__).resolve().parent
START = pd.Timestamp("2026-07-05", tz="UTC")   # 1er jour plein de forceOrder


def universe():
    y = yaml.safe_load((ROOT / "configs/portfolio_v1_1_parallel_50.yaml").read_text())
    for k in ("symbols", "universe", "assets"):
        if isinstance(y, dict) and k in y:
            v = y[k]
            if isinstance(v, list):
                return [s if isinstance(s, str) else s.get("symbol") for s in v]
    # fallback : structure inconnue -> dump
    raise SystemExit(f"universe key not found, keys={list(y) if isinstance(y, dict) else type(y)}")


def liq_flow(syms):
    """Toutes les liquidations bybit+okx pour les symboles demandes, en barres 5-min."""
    frames = []
    for ex, mkt in (("bybit", "linear"), ("okx", "swap")):
        pats = []
        for s in syms:
            d = ROOT / f"data/derivatives_raw/exchange={ex}/market={mkt}/stream=force_order/symbol={s}"
            if d.exists():
                pats.append(str(d / "date=*/*.parquet"))
        if not pats:
            continue
        lst = ", ".join(f"'{p}'" for p in pats)
        q = f"""
        SELECT symbol,
               time_bucket(INTERVAL '5 minutes', to_timestamp(timestamp/1000.0)) AS t,
               SUM(CASE WHEN side='SELL' THEN usd ELSE 0 END) AS long_liq_usd,
               SUM(CASE WHEN side='BUY'  THEN usd ELSE 0 END) AS short_liq_usd
        FROM read_parquet([{lst}], union_by_name=true)
        GROUP BY 1,2
        """
        df = duckdb.sql(q).df()
        df["venue"] = ex
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["t"] = pd.to_datetime(out["t"], utc=True)
    return out.groupby(["symbol", "t"], as_index=False)[["long_liq_usd", "short_liq_usd"]].sum()


def main():
    syms = universe()
    print(f"universe: {len(syms)} symbols")

    cfg = CascadeConfig()
    evs = []
    for s in syms:
        d = load_metrics(s)
        if d is None:
            continue
        ev = detect_cascades(d, cfg)
        if ev.empty:
            continue
        ev["symbol"] = s
        evs.append(ev[ev["event_time"] >= START])
    events = pd.concat(evs, ignore_index=True).sort_values("event_time").reset_index(drop=True)
    print(f"events (frozen detector) since {START.date()}: {len(events)}  "
          f"{events['kind'].value_counts().to_dict()}")

    flow = liq_flow(sorted(events["symbol"].unique()))
    print(f"liq 5m bars: {len(flow)}  symbols={flow['symbol'].nunique()}")

    # fenetre +/-30 min autour de l'event
    rows = []
    fl = {s: g.sort_values("t").reset_index(drop=True) for s, g in flow.groupby("symbol")}
    for r in events.itertuples():
        g = fl.get(r.symbol)
        if g is None:
            continue
        lo, hi = r.event_time - pd.Timedelta("30min"), r.event_time + pd.Timedelta("30min")
        w = g[(g["t"] >= lo) & (g["t"] <= hi)]
        L, S = float(w["long_liq_usd"].sum()), float(w["short_liq_usd"].sum())
        rows.append({"symbol": r.symbol, "event_time": r.event_time, "kind": r.kind,
                     "px_ret_30m": r.px_ret_30m, "oi_drop_z": r.oi_drop_z,
                     "long_liq_usd": L, "short_liq_usd": S})
    j = pd.DataFrame(rows)
    j["tot"] = j["long_liq_usd"] + j["short_liq_usd"]
    j = j[j["tot"] > 0].copy()
    j["short_share"] = j["short_liq_usd"] / j["tot"]

    res = {"window": "+/-30min", "n_events_matched": int(len(j)),
           "detector": "src/institutional/engines/liq_cascade/detector.py (frozen, read-only import)",
           "period": [str(j["event_time"].min()), str(j["event_time"].max())]}
    for k, g in j.groupby("kind"):
        # part de liquidations SHORT, ponderee USD et non ponderee
        w_share = float(g["short_liq_usd"].sum() / g["tot"].sum())
        m_share = float(g["short_share"].mean())
        res[k] = {"n": int(len(g)),
                  "usd_weighted_short_liq_share": round(w_share, 4),
                  "mean_event_short_liq_share": round(m_share, 4),
                  "median_event_short_liq_share": round(float(g["short_share"].median()), 4),
                  "pct_events_majority_short_liq": round(float((g["short_share"] > 0.5).mean()), 4)}
    a = j[j["kind"] == "SHORT_SQUEEZE"]["short_share"]
    b = j[j["kind"] == "LONG_CASCADE"]["short_share"]
    if len(a) > 5 and len(b) > 5:
        se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        res["welch_t_shortshare_SQUEEZE_minus_CASCADE"] = round(float((a.mean() - b.mean()) / se), 2)
    (OUT / "t1b_kind_vs_liqflow.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
