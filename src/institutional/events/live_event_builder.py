"""
src/institutional/events/live_event_builder.py
─────────────────────────────────────────────────────────────────────────────
Catalogue chaque liquidation collectée en ÉVÉNEMENT (mode accumulation productive).

On NE construit PAS de modèle ici (pas assez de données). On construit la CHAÎNE
qui, dès maintenant, transforme chaque liquidation live en event record :
  - features STRICTEMENT <= event_time (anti-leakage)
  - labels forward (1h/4h/8h, MAE/MFE) calculés APRÈS coup quand le prix existe

Binance forceOrder side : SELL = un LONG liquidé (long flush → rebond possible) ;
BUY = un SHORT liquidé (short squeeze → continuation possible).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
RAW_ROOT = ROOT / "data" / "derivatives_raw"
CLUSTER_MIN = 5            # regroupe les liquidations par fenêtre de 5 min
SIGNIFICANT_USD = 250_000  # event "significatif" si notional ≥ ce seuil


def load_force_orders() -> pd.DataFrame:
    """Charge les liquidations de TOUS les exchanges (binance/usdm + bybit/linear).

    Le side est déjà normalisé convention Binance par le collecteur
    (SELL = long liquidé, BUY = short liquidé), quel que soit l'exchange.
    """
    frames = []
    for ex_dir in sorted(RAW_ROOT.glob("exchange=*")):
        exchange = ex_dir.name.split("=", 1)[1]
        for p in sorted(ex_dir.glob("market=*/stream=force_order/symbol=*/date=*/part-*.parquet")):
            try:
                df = pd.read_parquet(p)
                df["exchange"] = exchange
                frames.append(df)
            except Exception:
                continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.sort_values("ts")


def _price(sym: str) -> Optional[pd.Series]:
    from src.institutional.engines.legacy_bridge import load_enriched
    enr = load_enriched(sym, required_cols=["close"])
    return enr.set_index("datetime")["close"].sort_index() if enr is not None else None


def build_events() -> pd.DataFrame:
    """Construit l'event lake liquidation depuis les parts collectées."""
    fo = load_force_orders()
    if fo.empty:
        return pd.DataFrame()
    fo["bucket"] = fo["ts"].dt.floor(f"{CLUSTER_MIN}min")
    # side: SELL = long liquidé, BUY = short liquidé
    fo["long_liq_usd"] = np.where(fo["side"] == "SELL", fo["usd"], 0.0)
    fo["short_liq_usd"] = np.where(fo["side"] == "BUY", fo["usd"], 0.0)
    g = fo.groupby(["symbol", "bucket"]).agg(
        event_time=("ts", "max"), total_usd=("usd", "sum"),
        long_liq_usd=("long_liq_usd", "sum"), short_liq_usd=("short_liq_usd", "sum"),
        n_liqs=("usd", "size"), price_at_event=("price", "last")).reset_index()
    g["liquidation_side"] = np.where(g["long_liq_usd"] >= g["short_liq_usd"], "LONG_LIQ", "SHORT_LIQ")
    g["significant"] = (g["total_usd"] >= SIGNIFICANT_USD).astype(int)
    g["event_id"] = (g["symbol"] + "_" + g["bucket"].dt.strftime("%Y%m%d%H%M%S"))

    # labels forward (calculés après coup, seulement si le prix futur existe)
    out = []
    for sym, gg in g.groupby("symbol"):
        px = _price(sym)
        gg = gg.copy()
        if px is not None and len(px):
            idx = pd.DatetimeIndex(gg["event_time"].values, tz="UTC")
            p0 = px.reindex(idx, method="ffill").to_numpy()
            for h in (1, 4, 8):
                ph = px.reindex(idx + pd.Timedelta(hours=h), method="ffill").to_numpy()
                with np.errstate(invalid="ignore", divide="ignore"):
                    gg[f"forward_return_{h}h"] = ph / p0 - 1.0
            # MAE/MFE 4h
            mae, mfe = [], []
            for ts in idx:
                w = px[(px.index > ts) & (px.index <= ts + pd.Timedelta(hours=4))]
                e = float(px.reindex([ts], method="ffill").iloc[0]) if len(px) else np.nan
                if len(w) and np.isfinite(e):
                    mae.append(float(w.min() / e - 1.0)); mfe.append(float(w.max() / e - 1.0))
                else:
                    mae.append(np.nan); mfe.append(np.nan)
            gg["MAE_4h"], gg["MFE_4h"] = mae, mfe
            gg["label_available"] = gg["forward_return_8h"].notna().astype(int)
        else:
            for h in (1, 4, 8):
                gg[f"forward_return_{h}h"] = np.nan
            gg["MAE_4h"] = gg["MFE_4h"] = np.nan
            gg["label_available"] = 0
        out.append(gg)
    events = pd.concat(out, ignore_index=True).sort_values("event_time")
    events["regime_proxy"] = "UNKNOWN"   # à enrichir (btc_regime causal) plus tard
    return events
