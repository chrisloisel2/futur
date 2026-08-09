#!/usr/bin/env python3
"""
research/edge_factory/funding_relative_value_cross_venue/backtest_funding_rv_v1.py
─────────────────────────────────────────────────────────────────────────────
Step 6 (edge événementiel net) + step 7 minimal (moteur deux jambes) de
funding_relative_value_cross_venue_v1 — Binance <-> Bybit uniquement.
Hyperliquid exclu : pas de prix historique fiable au-delà de 4 jours (voir
DATA_INVENTORY.yaml, update_2026-07-21_hyperliquid_perp_price).

Relation avec FUNDING_XVENUE_V0 (reports/FUNDING_XVENUE_PROTOCOL.md,
2026-07-18/19, clôturé NO_EDGE) : cette piste-là avait pour PAIRE PRIMAIRE
Binance<->Hyperliquid ; Binance<->Bybit y était "secondaire non-gating",
jugée sur un rapport plus ancien (spread médian ~0 bp), jamais testée avec
un vrai moteur deux-jambes ni des prix réels des deux côtés. Ce script est
donc le premier test réellement gaté de CETTE paire précise — pas une
répétition. Attention cependant : le signal préalable (spread quasi nul,
en déclin) rendait déjà un résultat NO_EDGE plausible avant ce test.

Discipline : la logique d'hystérésis (lookback, seuils d'entrée/sortie) est
reprise SANS RETOUCHE de FUNDING_XVENUE_V0 (calibrée sur Binance<->HL, pas
sur cette paire) — ce script ne cherche pas un meilleur seuil pour
Binance<->Bybit, il applique une règle déjà préenregistrée ailleurs.
n_trials=1 pour le DSR : aucune grille n'a été testée sur cette paire.

    .venv/bin/python research/edge_factory/funding_relative_value_cross_venue/backtest_funding_rv_v1.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from research.edge_factory.multileg_engine import costs as costs_mod  # noqa: E402
from research.edge_factory.multileg_engine.backtest_result import (  # noqa: E402
    MultiLegBacktestResult)

ASSETS = ["BTC", "ETH", "SOL", "BNB"]
LOOKBACK = 21          # settlements (~7j a 8h) -- repris de FUNDING_XVENUE_V0, non retouche
THETA_IN_ANN = 4.0     # %/an -- idem
THETA_OUT_ANN = 1.0    # %/an -- idem
SETTLEMENTS_PER_YEAR = 3 * 365   # Binance/Bybit : 8h -> 3/jour

FUNDING_DIR = ROOT / "data" / "derivatives_backfill"


def load_funding(venue: str, symbol: str) -> pd.DataFrame:
    path = FUNDING_DIR / venue / "funding" / f"{symbol}USDT.parquet"
    df = pd.read_parquet(path)[["timestamp", "funding_rate"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def aligned_spread(symbol: str) -> pd.DataFrame:
    b = load_funding("binance", symbol).rename(columns={"funding_rate": "fr_binance"})
    y = load_funding("bybit", symbol).rename(columns={"funding_rate": "fr_bybit"})
    # alignement par intervalle réel, jamais une égalité exacte en ms
    # (leçon settlement_timestamp_alignment_v1, déjà appliquée dans
    # FUNDING_XVENUE_V0 — réutilisée ici, pas redérivée).
    merged = pd.merge_asof(b, y, on="timestamp", tolerance=pd.Timedelta("30min"),
                           direction="nearest").dropna(subset=["fr_bybit"])
    merged["d"] = merged["fr_bybit"] - merged["fr_binance"]   # d>0 => bybit paie plus
    return merged


def hysteresis_state(d: pd.Series) -> np.ndarray:
    ann_pct = d.rolling(LOOKBACK, min_periods=LOOKBACK).mean() * SETTLEMENTS_PER_YEAR * 100
    state = np.zeros(len(ann_pct), dtype=int)
    cur = 0
    for i, v in enumerate(ann_pct.to_numpy()):
        if np.isnan(v):
            state[i] = cur
            continue
        if cur == 0 and abs(v) >= THETA_IN_ANN:
            cur = 1 if v > 0 else -1
        elif cur != 0 and abs(v) < THETA_OUT_ANN:
            cur = 0
        state[i] = cur
    return state


# calculé une seule fois : costs_mod.fee() lit un fichier de config/snapshot à
# chaque appel, l'appeler par ligne (des dizaines de milliers de fois via
# .apply()) était mesurablement trop lent en pratique.
_BINANCE_TAKER_BP = costs_mod.fee("binance", "BTCUSDT").taker_bp
_BYBIT_TAKER_BP = costs_mod.fee("bybit", "BTCUSDT").taker_bp
_SLIPPAGE_BP = 2.0   # configs/alpha20.yaml costs.assumed_defaults.slippage_bp_default
_PER_LEG_BP = (_BINANCE_TAKER_BP + _BYBIT_TAKER_BP) / 2.0 + _SLIPPAGE_BP


def round_trip_cost_frac(n_legs_changed: int, cost_x_mult: float = 1.0) -> float:
    return n_legs_changed * _PER_LEG_BP * cost_x_mult / 10_000.0


def backtest_asset(symbol: str) -> pd.DataFrame:
    df = aligned_spread(symbol)
    df["state"] = hysteresis_state(df["d"])
    prev_state = df["state"].shift(1).fillna(0).astype(int)
    legs_changed = (df["state"] - prev_state).abs() * 2   # +-1 -> 2 jambes ; +-2 -> 4 jambes
    df["funding_pnl"] = df["state"] * df["d"]
    df["cost_x1"] = -legs_changed.apply(lambda n: round_trip_cost_frac(int(n), 1.0))
    df["cost_x2"] = -legs_changed.apply(lambda n: round_trip_cost_frac(int(n), 2.0))
    df["net_x1"] = df["funding_pnl"] + df["cost_x1"]
    df["net_x2"] = df["funding_pnl"] + df["cost_x2"]
    df["symbol"] = symbol
    return df


def main() -> None:
    per_asset = {sym: backtest_asset(sym) for sym in ASSETS}

    panel = pd.concat(
        [df.set_index("timestamp")[["net_x1", "net_x2", "funding_pnl"]]
           .rename(columns={"net_x1": f"net_x1_{sym}", "net_x2": f"net_x2_{sym}",
                            "funding_pnl": f"gross_{sym}"})
         for sym, df in per_asset.items()], axis=1)

    net_x1_cols = [c for c in panel.columns if c.startswith("net_x1_")]
    net_x2_cols = [c for c in panel.columns if c.startswith("net_x2_")]
    portfolio_x1 = panel[net_x1_cols].mean(axis=1, skipna=True)
    portfolio_x2 = panel[net_x2_cols].mean(axis=1, skipna=True)

    per_year = {str(y): float(g.sum())
               for y, g in portfolio_x1.groupby(portfolio_x1.index.year)}

    result = MultiLegBacktestResult(
        trades=panel.reset_index(),
        pnl_daily=portfolio_x1,
        per_year=per_year,
        net_events=portfolio_x1.dropna(),
        net_events_x2=portfolio_x2.dropna(),
        returns_for_dsr=portfolio_x1.dropna(),
        trials_matrix=None,   # aucune grille testée sur cette paire -- pas de PBO ici
        meta={"pair": "binance_bybit", "assets": ASSETS, "lookback": LOOKBACK,
             "theta_in_ann": THETA_IN_ANN, "theta_out_ann": THETA_OUT_ANN,
             "n_trials": 1, "params_reused_from": "FUNDING_XVENUE_V0, non retouchés"})

    sleeve_gates = result.run_sleeve_gate()
    research_gates = result.run_research_gate(n_trials=1)

    per_asset_summary = {}
    for sym, df in per_asset.items():
        n1 = df["net_x1"].dropna()
        years = sorted(df.set_index("timestamp")["net_x1"].groupby(
            df.set_index("timestamp").index.year).sum().to_dict().items())
        per_asset_summary[sym] = {
            "n_settlements": int(len(df)),
            "gross_ann_pct": float(df["funding_pnl"].sum() / len(df) * SETTLEMENTS_PER_YEAR * 100)
                if len(df) else None,
            "net_x1_ann_pct": float(n1.sum() / len(n1) * SETTLEMENTS_PER_YEAR * 100)
                if len(n1) else None,
            "n_direction_changes": int(((df["state"].diff().fillna(0)) != 0).sum()),
            "per_year_net_x1": {str(y): float(v) for y, v in years},
        }

    out = {
        "experiment_id": "funding_relative_value_cross_venue_v1",
        "step": "6/8 -- event-level net edge (Binance<->Bybit only)",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "params": {"lookback": LOOKBACK, "theta_in_ann": THETA_IN_ANN,
                  "theta_out_ann": THETA_OUT_ANN, "reused_unchanged_from": "FUNDING_XVENUE_V0"},
        "per_asset": per_asset_summary,
        "portfolio": {
            "n_events": int(len(portfolio_x1.dropna())),
            "net_x1_ann_pct": float(portfolio_x1.mean() * SETTLEMENTS_PER_YEAR * 100),
            "net_x2_ann_pct": float(portfolio_x2.mean() * SETTLEMENTS_PER_YEAR * 100),
            "per_year_net_x1_pct": per_year,
        },
        "sleeve_gate": [g.__dict__ for g in sleeve_gates],
        "research_gate": [g.__dict__ for g in research_gates],
    }
    out_path = ROOT / "research/edge_factory/funding_relative_value_cross_venue/results"
    out_path.mkdir(parents=True, exist_ok=True)
    fname = out_path / f"EVENT_LEVEL_NET_EDGE_{out['date']}.json"
    fname.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    print(f"\n-> {fname}")


if __name__ == "__main__":
    main()
