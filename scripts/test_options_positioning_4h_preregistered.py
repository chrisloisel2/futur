#!/usr/bin/env python3
"""
scripts/test_options_positioning_4h_preregistered.py
─────────────────────────────────────────────────────────────────────────────
EXÉCUTION UNIQUE du protocole pré-enregistré OPTIONS_POSITIONING_4H
(reports/OPTIONS_POSITIONING_4H_PROTOCOL.md, commité AVANT ce run — 1d06580).

Aucun paramètre n'est exploré : features, fenêtres, seuils et critère PASS/FAIL
viennent du protocole. FAIL ⇒ OPTIONS_POSITIONING définitivement NO_EDGE.

    .venv/bin/python scripts/test_options_positioning_4h_preregistered.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TRADES = ROOT / "data" / "options_backfill" / "deribit" / "trades" / "BTC"
ENRICHED = ROOT / "data" / "enriched" / "BTCUSDT_1h_enriched.parquet"
REPORT_MD = ROOT / "reports" / "OPTIONS_POSITIONING_4H_VERDICT.md"
REPORT_JSON = ROOT / "reports" / "OPTIONS_POSITIONING_4H_VERDICT.json"

SIGNALS = ["d_skew_4h", "d_atm_iv_4h", "net_call_flow_4h", "net_put_flow_4h"]
HORIZONS = [1, 2, 6]            # buckets 4h → 4h, 8h, 24h
DELAYS = [0, 1]
Z_WIN, Z_MIN = 540, 180         # ~90 j de buckets 4h
P_MAX, IC_MIN, HALF_MIN_N = 0.002, 0.04, 500
SPLIT = pd.Timestamp("2024-10-01", tz="UTC")
END = pd.Timestamp("2026-07-01", tz="UTC")   # trades complets jusqu'à 2026-06


def bucket_features(df: pd.DataFrame) -> pd.DataFrame:
    """Features protocolaires par bucket 4h (un fichier mensuel à la fois)."""
    df = df[df["iv"].notna() & (df["index_price"] > 0)].copy()
    df["bucket"] = df["ts"].dt.floor("4h")
    m = df["strike"] / df["index_price"]
    df["is_atm"] = m.between(0.95, 1.05)
    df["is_otm_put"] = (df["cp"] == "P") & m.between(0.80, 0.95)
    df["is_otm_call"] = (df["cp"] == "C") & m.between(1.05, 1.20)
    df["sgn_amount"] = np.where(df["direction"] == "buy", 1.0, -1.0) * df["amount"]
    rows = []
    for b, g in df.groupby("bucket"):
        op, oc = g.loc[g["is_otm_put"], "iv"], g.loc[g["is_otm_call"], "iv"]
        atm = g.loc[g["is_atm"], "iv"]
        rows.append({
            "bucket": b,
            "skew": op.median() - oc.median() if len(op) and len(oc) else np.nan,
            "atm_iv": atm.median() if len(atm) else np.nan,
            "net_call_flow_4h": float(g.loc[g["cp"] == "C", "sgn_amount"].sum()),
            "net_put_flow_4h": float(g.loc[g["cp"] == "P", "sgn_amount"].sum()),
        })
    return pd.DataFrame(rows)


def zroll(s: pd.Series) -> pd.Series:
    return (s - s.rolling(Z_WIN, min_periods=Z_MIN).mean()) / \
        s.rolling(Z_WIN, min_periods=Z_MIN).std()


def main() -> None:
    parts = [bucket_features(pd.read_parquet(f)) for f in sorted(TRADES.glob("*.parquet"))]
    feats = (pd.concat(parts).drop_duplicates("bucket").set_index("bucket").sort_index()
             .loc[:END])
    feats["d_skew_4h"] = feats["skew"].diff()
    feats["d_atm_iv_4h"] = feats["atm_iv"].diff()

    px = pd.read_parquet(ENRICHED, columns=["datetime", "close"])
    px = px.set_index(pd.DatetimeIndex(px["datetime"]))["close"].sort_index()
    # close à la fin du bucket B = close du bar 1h ouvrant à B+3h
    feats["close"] = px.reindex(feats.index + pd.Timedelta(hours=3)).to_numpy()
    feats = feats.dropna(subset=["close"])
    n_skew_nan = int(feats["d_skew_4h"].isna().sum())
    print(f"{len(feats)} buckets 4h, {feats.index.min()} → {feats.index.max()} ; "
          f"d_skew NaN (jambe vide) : {n_skew_nan}")

    results, passing = [], []
    for sig in SIGNALS:
        z = zroll(feats[sig])
        for d in DELAYS:
            zd = z.shift(d)
            for h in HORIZONS:
                fwd = feats["close"].shift(-(d + h)) / feats["close"].shift(-d) - 1
                m = zd.notna() & fwd.notna()
                ic, p = spearmanr(zd[m], fwd[m])
                q = pd.qcut(zd[m], 5, labels=False, duplicates="drop")
                spread = (fwd[m][q == q.max()].mean() - fwd[m][q == 0].mean()) * 1e4
                h1 = m & (feats.index < SPLIT)
                h2 = m & (feats.index >= SPLIT)
                ic1 = spearmanr(zd[h1], fwd[h1])[0] if h1.sum() >= HALF_MIN_N else np.nan
                ic2 = spearmanr(zd[h2], fwd[h2])[0] if h2.sum() >= HALF_MIN_N else np.nan
                same_sign = np.isfinite(ic1) and np.isfinite(ic2) and np.sign(ic1) == np.sign(ic2)
                ok = bool(p < P_MAX and abs(ic) >= IC_MIN and same_sign)
                row = {"signal": sig, "delay": d, "horizon_4h": h, "n": int(m.sum()),
                       "ic": round(float(ic), 4), "p": float(p),
                       "q5_q1_bps": round(float(spread), 1),
                       "ic_half1": None if not np.isfinite(ic1) else round(float(ic1), 4),
                       "ic_half2": None if not np.isfinite(ic2) else round(float(ic2), 4),
                       "PASS": ok}
                results.append(row)
                if ok:
                    passing.append(row)

    verdict = "PASS" if passing else "NO_EDGE_DEFINITIF"
    res_df = pd.DataFrame(results)
    print(res_df.to_string(index=False))
    print(f"\nVERDICT PROTOCOLAIRE : {verdict} "
          f"({len(passing)}/24 cellules satisfont p<{P_MAX} & |IC|≥{IC_MIN} & signe stable)")

    REPORT_JSON.write_text(json.dumps(
        {"protocol": "OPTIONS_POSITIONING_4H", "protocol_commit": "1d06580",
         "date": "2026-07-18", "n_buckets": int(len(feats)),
         "d_skew_nan_buckets": n_skew_nan, "verdict": verdict,
         "passing_cells": passing, "all_cells": results}, indent=1))
    md = ["# VERDICT — OPTIONS_POSITIONING_4H (protocole pré-enregistré 1d06580)",
          f"\nExécution unique 2026-07-18. {len(feats)} buckets 4h "
          f"({feats.index.min().date()} → {feats.index.max().date()}), "
          f"{n_skew_nan} buckets sans skew calculable (jambe OTM vide).\n",
          f"## VERDICT : **{verdict}**\n", "```", res_df.to_string(index=False), "```",
          "\nCritère (fixé avant run) : p<0.002 ET |IC|≥0.04 ET même signe sur les deux "
          "moitiés (n≥500 chacune). "
          + ("Cellules qualifiées listées dans le JSON." if passing else
             "Aucune cellule qualifiée → OPTIONS_POSITIONING est classé DÉFINITIVEMENT "
             "NO_EDGE ; aucune variante ultérieure ne sera tentée (règle utilisateur).")]
    REPORT_MD.write_text("\n".join(md) + "\n")
    print(f"→ {REPORT_MD.relative_to(ROOT)} + JSON")


if __name__ == "__main__":
    main()
