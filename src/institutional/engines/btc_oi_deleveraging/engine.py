"""
src/institutional/engines/btc_oi_deleveraging/engine.py
─────────────────────────────────────────────────────────────────────────────
BTC OI-Deleveraging Event Engine (Phase 2) — test rapide d'edge convexe sur
DONNÉES RÉELLES (BTC oi_sum 2021-2025).

Hypothèse : une chute brutale d'open interest + chute de prix = deleveraging
forcé (proxy de cascade de liquidation, sans feed liquidation), souvent suivie
d'un rebond court.

EVENT-FIRST : on ne prédit pas chaque bougie. On détecte un événement, on entre,
on sort sur horizon court. Pas de short. Rule-based d'abord (mesure l'edge brut
avant d'investir dans un classifieur / la collecte live).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

from src.institutional.engines.legacy_bridge import load_enriched


@dataclass
class OIEventConfig:
    oi_drop_4h: float = 0.03     # OI chute > 3% sur 4h
    price_drop_4h: float = 0.02  # prix chute > 2% sur 4h
    min_funding_at_event: float = None  # réparation : longs sur-leveragés (funding>seuil)
    cooldown_h: int = 8          # anti-chevauchement d'événements
    exit_horizon_h: int = 4      # sortie après N heures
    stop_loss: float = 0.03
    cost: float = 0.001          # aller-retour (10 bps)


def detect_events(df: pd.DataFrame, cfg: OIEventConfig) -> pd.DataFrame:
    """df indexé datetime avec close, oi_sum[, funding_rate]. Retourne les événements."""
    d = df.copy()
    d["oi_ret_4h"] = d["oi_sum"] / d["oi_sum"].shift(4) - 1.0
    d["price_ret_4h"] = d["close"] / d["close"].shift(4) - 1.0
    mask = (d["oi_ret_4h"] <= -cfg.oi_drop_4h) & (d["price_ret_4h"] <= -cfg.price_drop_4h)
    if cfg.min_funding_at_event is not None and "funding_rate" in d.columns:
        # funding élevé AVANT le flush = longs sur-leveragés → rebond plus probable
        mask &= (d["funding_rate"].shift(4) >= cfg.min_funding_at_event)
    ev = d[mask].copy()
    # cooldown : garder le 1er événement par fenêtre
    kept = []
    last = None
    for ts in ev.index:
        if last is None or (ts - last) >= pd.Timedelta(hours=cfg.cooldown_h):
            kept.append(ts); last = ts
    return ev.loc[kept]


def backtest(cfg: OIEventConfig = OIEventConfig(), cost_mult: float = 1.0) -> Dict:
    enr = load_enriched("BTCUSDT", required_cols=["close", "oi_sum", "funding_rate"],
                        start="2021-01-01", end="2025-12-31")
    if enr is None:
        return {"status": "no_data"}
    keep = [c for c in ["close", "oi_sum", "funding_rate"] if c in enr.columns]
    df = enr.set_index("datetime")[keep].dropna(subset=["close", "oi_sum"]).sort_index()
    events = detect_events(df, cfg)
    close = df["close"]
    cost = cfg.cost * cost_mult

    rows = []
    for ts in events.index:
        i = close.index.searchsorted(ts)
        if i + cfg.exit_horizon_h >= len(close):
            continue
        entry = float(close.iloc[i])
        # sortie : horizon ou stop (au plus bas touché)
        window = close.iloc[i: i + cfg.exit_horizon_h + 1]
        exit_px = float(window.iloc[-1])
        low = float(window.min())
        stopped = (low / entry - 1.0) <= -cfg.stop_loss
        ret = (-cfg.stop_loss if stopped else (exit_px / entry - 1.0)) - cost
        rows.append({"event_time": ts, "year": ts.year, "ret": ret})

    if not rows:
        return {"status": "no_events", "n_events": 0}
    r = pd.DataFrame(rows)
    wins = r[r["ret"] > 0]["ret"]; losses = r[r["ret"] <= 0]["ret"]
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else float("inf")
    avg_wl = float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else 0.0
    by_year = r.groupby("year")["ret"].agg(["count", "mean", "sum"]).round(4)
    # concentration : part du PnL de la meilleure année
    year_pnl = r.groupby("year")["ret"].sum()
    top_share = float(year_pnl.max() / year_pnl.sum()) if year_pnl.sum() > 0 else 1.0
    return {
        "status": "ok", "n_events": int(len(r)),
        "win_rate": float((r["ret"] > 0).mean()),
        "pf": pf, "avg_win_loss": avg_wl,
        "mean_ret": float(r["ret"].mean()), "total_ret": float(r["ret"].sum()),
        "by_year": by_year.to_dict("index"),
        "top_year_pnl_share": top_share,
    }
