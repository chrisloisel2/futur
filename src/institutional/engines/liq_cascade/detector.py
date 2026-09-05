"""
src/institutional/engines/liq_cascade/detector.py
─────────────────────────────────────────────────────────────────────────────
Détection CAUSALE de cascades de deleveraging sur métriques 5-min.

Un event = chute d'open interest anormalement rapide (z-score vs distribution
GLISSANTE passée) accompagnée d'un mouvement de prix. Interprétation :
  - prix ↓ + OI ↓ violent  → LONG_CASCADE  (longs liquidés / dégagés)
  - prix ↑ + OI ↓ violent  → SHORT_SQUEEZE (shorts liquidés)

Prix implicite : sum_open_interest_value / sum_open_interest (≈ mark price),
aucun feed externe requis à 5 min. Approximation documentée — la validation
finale d'exécution se fera sur klines.

CAUSALITÉ : toutes les statistiques (z-scores, quantiles) sont calculées en
rolling PASSÉ uniquement (min_periods garantit un warm-up, pas de lookahead).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
METRICS_DIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_metrics"

BARS_30M = 6          # 6 barres de 5 min
BARS_1H = 12
ROLL_7D = 2016        # 7 jours en barres 5-min


@dataclass
class CascadeConfig:
    oi_drop_z_min: float = 3.0       # |z| de la chute d'OI 30-min pour déclencher
    px_move_min: float = 0.004       # |ret prix 30-min| minimal (0.4%)
    min_gap_bars: int = 12           # 1h min entre deux events (clusterisation)
    roll_bars: int = ROLL_7D         # fenêtre du z-score glissant
    min_warmup_bars: int = 864       # 3 jours de warm-up avant tout event


# ═══════════════════════════════════════════════════════════════════════════
# QUEUE FRAÎCHE (2026-09-05) — voir reports/live_alpha_lab/DECISION_LATENCY_AUDIT_2026-09-05.md
# ═══════════════════════════════════════════════════════════════════════════
# METRICS_DIR est un backfill d'archives QUOTIDIENNES Binance Vision, en retard
# structurel de 1 à 2 jours. Mesuré : la famille cascade découvrait ses
# événements 45-48 h après coup pour un horizon de 4 h -- 100 % de ses décisions
# forward arrivaient périmées, donc inexécutables.
#
# LIVE_METRICS_DIR contient la QUEUE de la même série, collectée toutes les
# 15 min par scripts/collect_oi_metrics_5m.py depuis les endpoints
# `futures/data` (qui ne retiennent qu'environ 30 jours -- ils ne remplacent
# donc PAS Vision, ils le prolongent).
#
# La spec figée n'est PAS touchée : `detect_cascades` ne déclenche que sur
# `sum_open_interest` et le prix implicite `sum_open_interest_value/sum_open_interest`,
# et ces deux champs sont IDENTIQUES entre les deux sources sur le recouvrement
# (OI identique à 100 %, prix implicite à 0,000000 bps d'écart médian ET maximum,
# vérifié sur 8 symboles × 133 barres). Les écarts résiduels sur les ratios de
# positionnement (5e-5 à 4e-3) sont de l'arrondi de publication et n'alimentent
# que des z-scores glissants sur 7 jours, jamais le déclencheur.
LIVE_METRICS_DIR = ROOT / "data" / "derivatives_live_metrics"


def _append_live_tail(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Prolonge la série Vision par les barres live POSTÉRIEURES à sa fin.

    Priorité absolue à Vision sur le recouvrement : l'archive quotidienne est
    la référence, et une barre déjà servie à un détecteur ne doit jamais être
    remplacée par une valeur republiée -- sinon une décision passée cesserait
    d'être reproductible. On ne garde donc du live que ce que Vision n'a pas.

    Dégradation silencieuse assumée et voulue : pas de fichier live (collecteur
    jamais lancé, symbole renommé côté Binance) -> comportement identique à
    l'ancien, l'appelant n'a rien à savoir.
    """
    live_path = LIVE_METRICS_DIR / f"{symbol}_metrics_5m_live.parquet"
    if not live_path.exists() or df.empty:
        return df
    try:
        live = pd.read_parquet(live_path)
    except (OSError, ValueError):
        return df   # fichier en cours d'écriture ou corrompu : on garde Vision seul
    if live.empty or "create_time" not in live.columns:
        return df
    live["create_time"] = pd.to_datetime(live["create_time"], utc=True)
    tail = live[live["create_time"] > df["create_time"].max()]
    if tail.empty:
        return df
    # Réaligner les colonnes sur celles de Vision : une colonne absente devient
    # NaN plutôt que de faire échouer le concat ou de décaler le schéma.
    tail = tail.reindex(columns=df.columns)
    return pd.concat([df, tail], ignore_index=True)


def load_metrics(symbol: str) -> Optional[pd.DataFrame]:
    p = METRICS_DIR / f"{symbol}_metrics_5m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["create_time"] = pd.to_datetime(df["create_time"], utc=True)
    df = df.sort_values("create_time").reset_index(drop=True)
    df = _append_live_tail(df, symbol)
    df = df.sort_values("create_time").reset_index(drop=True)
    oi = df["sum_open_interest"].astype(float)
    oiv = df["sum_open_interest_value"].astype(float)
    px = np.where(oi > 0, oiv / oi, np.nan)
    df["px"] = np.where(px > 0, px, np.nan)   # px<=0 = donnée invalide → nan
    return df


def detect_cascades(df: pd.DataFrame, cfg: CascadeConfig = CascadeConfig()) -> pd.DataFrame:
    """Retourne un DataFrame d'events (une ligne par cascade clusterisée).

    Colonnes : event_time, kind (LONG_CASCADE|SHORT_SQUEEZE), oi_drop_30m,
    oi_drop_z, px_ret_30m, px (prix implicite à l'event), row (index barre).
    """
    d = df.copy()
    oi = d["sum_open_interest"].astype(float)
    px = d["px"].astype(float)

    d["oi_ret_30m"] = oi.pct_change(BARS_30M)
    d["px_ret_30m"] = px.pct_change(BARS_30M)

    # z-score GLISSANT PASSÉ de la chute d'OI (shift(1) : la barre courante
    # n'entre pas dans sa propre stat → pas d'auto-contamination)
    r = d["oi_ret_30m"]
    mu = r.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).mean()
    sd = r.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).std()
    d["oi_drop_z"] = (r - mu) / sd.replace(0.0, np.nan)

    trigger = (
        (d["oi_drop_z"] <= -cfg.oi_drop_z_min)
        & (d["oi_ret_30m"] < 0)
        & (d["px_ret_30m"].abs() >= cfg.px_move_min)
        & d["px"].notna()
    )

    events = []
    last_row = -10**9
    idx = np.flatnonzero(trigger.values)
    for i in idx:
        if i - last_row < cfg.min_gap_bars:
            continue    # même cascade : on garde la PREMIÈRE barre (entrée au plus tôt)
        last_row = i
        events.append({
            "row": int(i),
            "event_time": d["create_time"].iloc[i],
            "kind": "LONG_CASCADE" if d["px_ret_30m"].iloc[i] < 0 else "SHORT_SQUEEZE",
            "oi_drop_30m": float(d["oi_ret_30m"].iloc[i]),
            "oi_drop_z": float(d["oi_drop_z"].iloc[i]),
            "px_ret_30m": float(d["px_ret_30m"].iloc[i]),
            "px": float(d["px"].iloc[i]),
        })
    return pd.DataFrame(events)
