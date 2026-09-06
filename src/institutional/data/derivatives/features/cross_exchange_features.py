"""
src/institutional/data/derivatives/features/cross_exchange_features.py
─────────────────────────────────────────────────────────────────────────────
Panel funding Binance×Bybit 2-exchanges (~3.6 ans d'overlap) + features causales
+ labels forward. Sert à tester 4 hypothèses : directionnel, risk-off, carry
quality, crowding asymétrique.

Anti-leakage : zscore = rolling causal ; labels forward calculés STRICTEMENT
après le timestamp ; spread signé = bybit − binance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
BACKFILL = ROOT / "data" / "derivatives_backfill"
FUNDING_HOURS = (0, 8, 16)


def _load(ex: str, sym: str) -> Optional[pd.Series]:
    p = BACKFILL / ex / "funding" / f"{sym}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    s = df.set_index(pd.to_datetime(df["timestamp"], utc=True))["funding_rate"].sort_index()
    s.index = s.index.floor("8h")
    return s[~s.index.duplicated(keep="last")]


def _price_8h(sym: str) -> Optional[pd.Series]:
    from src.institutional.engines.legacy_bridge import load_enriched
    enr = load_enriched(sym, required_cols=["close"])
    if enr is None:
        return None
    return enr.set_index("datetime")["close"].sort_index()


def build_panel(symbol: str) -> pd.DataFrame:
    """Panel Binance×Bybit aligné 8h + features causales + labels forward."""
    fb = _load("binance", symbol)
    fy = _load("bybit", symbol)
    if fb is None or fy is None:
        return pd.DataFrame()
    df = pd.DataFrame({"funding_binance": fb, "funding_bybit": fy}).dropna()
    if df.empty:
        return df
    df["symbol"] = symbol
    # ── features cross-exchange (causales) ──
    df["funding_spread"] = df["funding_bybit"] - df["funding_binance"]   # signé
    df["abs_funding_spread"] = df["funding_spread"].abs()
    for w in (30, 90):
        m = df["funding_spread"].rolling(w * 3, min_periods=20).mean()
        s = df["funding_spread"].rolling(w * 3, min_periods=20).std()
        df[f"funding_spread_zscore_{w}d"] = (df["funding_spread"] - m) / (s + 1e-12)
    am = df["abs_funding_spread"].rolling(90 * 3, min_periods=20).rank(pct=True)
    df["abs_spread_pct"] = am
    df["funding_consensus_min"] = df[["funding_binance", "funding_bybit"]].min(axis=1)
    df["funding_consensus_mean"] = df[["funding_binance", "funding_bybit"]].mean(axis=1)
    df["funding_positive_both"] = ((df["funding_binance"] > 0) & (df["funding_bybit"] > 0)).astype(int)
    # flip = changement de signe sur 24h (3 périodes) — causal
    df["funding_flip_binance_24h"] = (np.sign(df["funding_binance"]).diff().abs().rolling(3).sum() > 0).astype(int)
    df["funding_flip_bybit_24h"] = (np.sign(df["funding_bybit"]).diff().abs().rolling(3).sum() > 0).astype(int)

    # ── labels forward (strictement après t) ──
    px = _price_8h(symbol)
    if px is not None:
        idx = df.index
        p0 = px.reindex(idx, method="ffill").to_numpy()
        for h in (8, 24, 72):
            ph = px.reindex(idx + pd.Timedelta(hours=h), method="ffill").to_numpy()
            with np.errstate(invalid="ignore", divide="ignore"):
                df[f"forward_return_{h}h"] = ph / p0 - 1.0
        # max drawdown forward 24h/72h depuis le prix
        for h, steps in ((24, 3), (72, 9)):
            mdd = []
            arr = px.reindex(pd.Index(idx), method="ffill")
            for ts in idx:
                w = px[(px.index > ts) & (px.index <= ts + pd.Timedelta(hours=h))]
                if len(w):
                    e = float(px.reindex([ts], method="ffill").iloc[0])
                    mdd.append(float((w.min() / e) - 1.0))
                else:
                    mdd.append(np.nan)
            df[f"future_max_drawdown_{h}h"] = mdd
    # carry net 24h (récolte funding côté Binance, 3 prochaines périodes)
    df["future_net_carry_24h"] = df["funding_binance"].shift(-1).rolling(3).sum().shift(-2)
    df["future_flip_24h"] = (np.sign(df["funding_binance"]).diff().abs().shift(-3).rolling(3).sum() > 0).astype(float)
    df["year"] = df.index.year
    return df


def build_universe(symbols) -> pd.DataFrame:
    frames = [build_panel(s) for s in symbols]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames) if frames else pd.DataFrame()
