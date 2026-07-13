"""
src/institutional/engines/liq_cascade/dataset.py
─────────────────────────────────────────────────────────────────────────────
Dataset événementiel : features CAUSALES à l'event + labels forward.

Features (STRICTEMENT ≤ event_time — construites depuis les barres ≤ row) :
  intensité   : oi_drop_30m/z, oi_drop_1h, px_ret_30m/1h, accélération
  positionnement (5-min Vision) : taker_ratio z, toptrader_ratio z, ls_ratio z
  contexte    : vol réalisée 24h, distance au plus-bas 24h/7j, OI/OI_7j,
                heure UTC, ret 24h
  cross-asset : nb d'events simultanés market-wide (±30 min, autres symboles)

Labels (calculés APRÈS coup sur le prix implicite 5-min) :
  fwd_1h / fwd_4h / fwd_8h (log-ret depuis la barre d'ENTRÉE = row+1,
  exécution à la barre suivante — pas au prix de détection), MFE/MAE 4h.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.engines.liq_cascade.detector import (
    BARS_1H, CascadeConfig, detect_cascades, load_metrics,
)

FWD_HORIZONS = {"1h": 12, "4h": 48, "8h": 96, "24h": 288}
FEATURES = [
    "oi_drop_30m", "oi_drop_z", "oi_drop_1h", "px_ret_30m", "px_ret_1h",
    "px_accel", "taker_z", "toptrader_z", "ls_ratio_z", "vol_24h",
    "dist_low_24h", "dist_low_7d", "oi_vs_7d", "ret_24h", "hour_utc",
    "n_events_mktwide_30m", "is_long_cascade",
]
# v2 : features causales additionnelles (funding as-of, structure OI,
# deltas de positionnement, contexte BTC, séquencement). NaN si source absente
# (alts sans funding backfillé) — LightGBM les gère nativement.
FEATURES_V2 = FEATURES + [
    "funding_last", "funding_z30", "oi_ret_2h", "oi_ret_24h", "oi_pctile_30d",
    "taker_delta_1h", "toptrader_delta_1h", "btc_ret_30m", "btc_vol_24h",
    "mins_since_prev_event", "n_events_sym_24h", "dow",
]
BARS_24H = 288
BARS_7D = 2016
BARS_30D = 8640
FUNDING_DIR_DEFAULT = None  # résolu dans build_event_dataset


def _roll_z(s: pd.Series, win: int, minp: int) -> pd.Series:
    mu = s.shift(1).rolling(win, min_periods=minp).mean()
    sd = s.shift(1).rolling(win, min_periods=minp).std()
    return (s - mu) / sd.replace(0.0, np.nan)


def _causal_frame(d: pd.DataFrame) -> pd.DataFrame:
    """Colonnes causales pré-calculées sur toute la série (rolling passé only)."""
    out = pd.DataFrame(index=d.index)
    px = d["px"].astype(float)
    oi = d["sum_open_interest"].astype(float)
    lr = np.log(px).diff()

    out["px_ret_1h"] = px.pct_change(BARS_1H)
    out["px_accel"] = px.pct_change(6) - px.pct_change(BARS_1H) / 2.0
    out["oi_drop_1h"] = oi.pct_change(BARS_1H)
    out["taker_z"] = _roll_z(d["sum_taker_long_short_vol_ratio"].astype(float), BARS_7D, 864)
    out["toptrader_z"] = _roll_z(d["sum_toptrader_long_short_ratio"].astype(float), BARS_7D, 864)
    out["ls_ratio_z"] = _roll_z(d["count_long_short_ratio"].astype(float), BARS_7D, 864)
    out["vol_24h"] = lr.rolling(BARS_24H, min_periods=144).std() * np.sqrt(BARS_24H)
    low24 = px.rolling(BARS_24H, min_periods=144).min()
    low7d = px.rolling(BARS_7D, min_periods=864).min()
    out["dist_low_24h"] = px / low24 - 1.0
    out["dist_low_7d"] = px / low7d - 1.0
    out["oi_vs_7d"] = oi / oi.rolling(BARS_7D, min_periods=864).mean() - 1.0
    out["ret_24h"] = px.pct_change(BARS_24H)
    # v2 : structure OI + deltas positionnement
    out["oi_ret_2h"] = oi.pct_change(24)
    out["oi_ret_24h"] = oi.pct_change(BARS_24H)
    # position min-max 30j (O(n), pas rolling.rank qui serait O(n·w))
    lo30 = oi.rolling(BARS_30D, min_periods=BARS_7D).min()
    hi30 = oi.rolling(BARS_30D, min_periods=BARS_7D).max()
    out["oi_pctile_30d"] = (oi - lo30) / (hi30 - lo30).replace(0.0, np.nan)
    out["taker_delta_1h"] = d["sum_taker_long_short_vol_ratio"].astype(float).diff(12)
    out["toptrader_delta_1h"] = d["sum_toptrader_long_short_ratio"].astype(float).diff(12)
    return out


def build_event_dataset(symbols: List[str],
                        cfg: CascadeConfig = CascadeConfig(),
                        detector_fn=None) -> pd.DataFrame:
    """Events multi-actifs + features causales + labels forward.

    detector_fn(d) -> DataFrame[row, event_time, kind, ...] : permet de brancher
    d'autres définitions d'events (crowding washout, premium dislocation…) sur
    le MÊME pipeline features/labels (défaut : cascades OI).
    """
    per_sym: Dict[str, pd.DataFrame] = {}
    frames = []
    for sym in symbols:
        d = load_metrics(sym)
        if d is None or len(d) < cfg.min_warmup_bars + BARS_7D:
            continue
        ev = detector_fn(d) if detector_fn is not None else detect_cascades(d, cfg)
        if ev.empty:
            continue
        ev["symbol"] = sym
        causal = _causal_frame(d)
        px = d["px"].astype(float).values

        rows = ev["row"].values
        for c in causal.columns:
            ev[c] = causal[c].values[rows]
        ev["hour_utc"] = ev["event_time"].dt.hour.astype(float)
        ev["is_long_cascade"] = (ev["kind"] == "LONG_CASCADE").astype(float)

        # labels : ENTRÉE à la barre row+1 (exécution après détection)
        n = len(px)
        entry = np.minimum(rows + 1, n - 1)
        entry_px = px[entry]
        for name, h in FWD_HORIZONS.items():
            exit_i = np.minimum(entry + h, n - 1)
            ev[f"fwd_{name}"] = np.log(px[exit_i] / entry_px)
        # MFE / MAE sur 4h après entrée
        mfe, mae = np.full(len(rows), np.nan), np.full(len(rows), np.nan)
        for k, e0 in enumerate(entry):
            w = px[e0:min(e0 + FWD_HORIZONS["4h"] + 1, n)]
            w = w[np.isfinite(w) & (w > 0)]   # barres invalides exclues (px nan/0)
            if len(w) > 1 and np.isfinite(entry_px[k]) and entry_px[k] > 0:
                mfe[k] = np.log(np.nanmax(w) / entry_px[k])
                mae[k] = np.log(np.nanmin(w) / entry_px[k])
        ev["MFE_4h"], ev["MAE_4h"] = mfe, mae
        ev["label_full"] = (rows + 1 + FWD_HORIZONS["8h"]) < n   # label complet dispo
        per_sym[sym] = ev
        frames.append(ev)

    if not frames:
        return pd.DataFrame()
    allev = pd.concat(frames, ignore_index=True).sort_values("event_time")

    # feature cross-asset : nb d'events market-wide dans ±30 min (CAUSAL :
    # on ne compte que les events STRICTEMENT ANTÉRIEURS dans la fenêtre)
    times = allev["event_time"].values.astype("datetime64[ns]")
    n_mkt = np.zeros(len(allev))
    for i in range(len(allev)):
        lo = times[i] - np.timedelta64(30, "m")
        n_mkt[i] = int(((times >= lo) & (times < times[i])).sum())
    allev["n_events_mktwide_30m"] = n_mkt
    allev = allev.reset_index(drop=True)

    # ── v2 : séquencement par symbole (events passés uniquement) ──
    allev["dow"] = allev["event_time"].dt.dayofweek.astype(float)
    prev = allev.groupby("symbol")["event_time"].shift(1)
    allev["mins_since_prev_event"] = (
        (allev["event_time"] - prev).dt.total_seconds() / 60.0)
    cnt = np.zeros(len(allev))
    for sym, g in allev.groupby("symbol"):
        t = g["event_time"].values.astype("datetime64[ns]")
        for j, i in enumerate(g.index):
            lo = t[j] - np.timedelta64(24, "h")
            cnt[i] = int(((t >= lo) & (t < t[j])).sum())
    allev["n_events_sym_24h"] = cnt

    # ── v2 : funding as-of (dernier funding CONNU ≤ event_time) + z30 ──
    from src.institutional.engines.liq_cascade.detector import ROOT as _ROOT
    fdir = _ROOT / "data" / "derivatives_backfill" / "binance" / "funding"
    allev["funding_last"] = np.nan
    allev["funding_z30"] = np.nan
    if fdir.exists():
        for sym in allev["symbol"].unique():
            fp = fdir / f"{sym}.parquet"
            if not fp.exists():
                continue
            f = pd.read_parquet(fp).sort_values("timestamp")
            f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
            mu = f["funding_rate"].shift(1).rolling(30, min_periods=10).mean()
            sd = f["funding_rate"].shift(1).rolling(30, min_periods=10).std()
            f["funding_z30"] = (f["funding_rate"] - mu) / sd.replace(0.0, np.nan)
            m = allev["symbol"] == sym
            sub = allev.loc[m, ["event_time"]].sort_values("event_time")
            j = pd.merge_asof(sub, f[["timestamp", "funding_rate", "funding_z30"]],
                              left_on="event_time", right_on="timestamp",
                              direction="backward")
            allev.loc[sub.index, "funding_last"] = j["funding_rate"].values
            allev.loc[sub.index, "funding_z30"] = j["funding_z30"].values

    # ── v2 : contexte BTC (as-of sur la barre 5-min BTC ≤ event_time) ──
    allev["btc_ret_30m"] = np.nan
    allev["btc_vol_24h"] = np.nan
    dbtc = load_metrics("BTCUSDT")
    if dbtc is not None:
        px = dbtc["px"].astype(float)
        lr = np.log(px).diff()
        ctx = pd.DataFrame({
            "t": dbtc["create_time"],
            "btc_ret_30m": px.pct_change(6),
            "btc_vol_24h": lr.rolling(BARS_24H, min_periods=144).std() * np.sqrt(BARS_24H),
        }).sort_values("t")
        sub = allev[["event_time"]].sort_values("event_time")
        j = pd.merge_asof(sub, ctx, left_on="event_time", right_on="t",
                          direction="backward")
        allev.loc[sub.index, "btc_ret_30m"] = j["btc_ret_30m"].values
        allev.loc[sub.index, "btc_vol_24h"] = j["btc_vol_24h"].values

    return allev
