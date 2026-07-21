#!/usr/bin/env python3
"""
scripts/backtest_stress_gate_dispersion_v2.py
─────────────────────────────────────────────────────────────────────────────
STRESS_GATE_DISPERSION_V2 — reconstruction indépendante et causale de
cross_exchange_stress_gate_h2 (statut UNVERIFIED_PROVENANCE, voir
configs/alpha20.yaml -> experiment_registry.provenance_blocked et
research/edge_factory/basis_dispersion/stress_gate_dispersion_v2/PREREGISTRATION.md).

Ce module ne vise PAS à reproduire -2,05 %/-1,82 % : c'est un test neuf sur
la prédiction "dispersion cross-venue élevée -> drawdown forward plus
profond", avec un seuil causal (jamais un percentile pleine-échantillon).

Phase 1 (intégrité du panel) est implémentée et testée ici avec des données
synthétiques (tests/test_stress_gate_dispersion_v2.py). Phase 2 (test
événementiel primaire) nécessite les données réelles Binance/Bybit funding
8h archivées — ABSENTES de cette machine au moment de l'écriture (data/
derivatives_backfill/ n'existe pas ici ; la machine de recherche qbee@
100.127.59.114 est inaccessible depuis cet environnement, voir
research/forensics/stress_gate_c78874b/README.md). Ce script est donc prêt
à être exécuté sur des données réelles mais n'a PAS produit de verdict
Phase 2/3/4 : voir __main__ pour le comportement explicite en absence de
données.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

Z_WIN, Z_MIN = 270, 180          # identique à backtest_funding_extreme.py
EXTREME_Q = 0.95
FUNDING_HOURS = (0, 8, 16)       # cadence funding Binance/Bybit


class PanelIntegrityError(RuntimeError):
    """Levée pour toute violation d'intégrité (doublon, jambe manquante mal
    gérée, etc.) — jamais contournée silencieusement."""


def causal_expanding_quantile(s: pd.Series, q: float = EXTREME_Q,
                              window: int = Z_WIN, min_periods: int = Z_MIN
                              ) -> pd.Series:
    """Seuil causal : threshold[t] = quantile(q) des `window` observations
    STRICTEMENT antérieures à t (min `min_periods`). Le `.shift(1)` exclut
    l'observation courante avant même de fenêtrer — c'est le point qui
    distingue ce module du rapport historique (jamais audité comme causal)."""
    return s.shift(1).rolling(window=window, min_periods=min_periods).quantile(q)


def build_panel(funding_binance: pd.Series, funding_bybit: pd.Series
                ) -> pd.DataFrame:
    """Panel causal à partir de deux séries de funding 8h indexées par
    timestamp UTC (un seul actif à la fois — appeler par actif, empiler
    ensuite). Fail-closed : un timestamp absent d'une venue est EXCLU, jamais
    forward-fillé ni imputé. Un timestamp dupliqué sur une venue est une
    erreur d'intégrité (jamais moyenné/dernier-gagne silencieusement)."""
    for name, s in (("binance", funding_binance), ("bybit", funding_bybit)):
        if len(s) == 0:
            continue          # jambe totalement absente : rien à valider, le
                               # join exact ci-dessous produira un panel vide
        if s.index.has_duplicates:
            dups = s.index[s.index.duplicated()].unique().tolist()
            raise PanelIntegrityError(
                f"timestamps dupliqués sur {name}: {dups[:5]}"
                f"{' ...' if len(dups) > 5 else ''}")
        bad_hours = set(s.index.hour) - set(FUNDING_HOURS)
        if bad_hours:
            raise PanelIntegrityError(
                f"{name}: timestamps hors cadence funding 8h : {bad_hours}")

    # jointure EXACTE (inner) sur le timestamp — aucune tolérance temporelle,
    # aucun forward-fill : un timestamp qui n'existe que sur une venue est
    # exclu du panel, pas imputé.
    df = pd.DataFrame({"funding_binance": funding_binance,
                       "funding_bybit": funding_bybit}).dropna(how="any")
    df = df.sort_index()
    df["dispersion"] = (df["funding_binance"] - df["funding_bybit"]).abs()
    df["stress_threshold"] = causal_expanding_quantile(df["dispersion"])
    df["is_stress"] = df["dispersion"] >= df["stress_threshold"]
    return df


def forward_drawdown(price: pd.Series, horizon_periods: int) -> pd.Series:
    """Label (PAS un signal) : creux forward sur `horizon_periods` barres
    suivantes, exécution barre suivante ([t+1, t+horizon]). Utilise le futur
    par construction — c'est autorisé pour un label, interdit pour un seuil
    de signal (voir causal_expanding_quantile)."""
    n = len(price)
    out = np.full(n, np.nan)
    values = price.to_numpy()
    for i in range(n - horizon_periods):
        window = values[i + 1: i + 1 + horizon_periods]
        out[i] = window.min() / values[i] - 1.0
    return pd.Series(out, index=price.index, name="forward_drawdown")


def panel_manifest(df: pd.DataFrame, *, extra: Optional[dict] = None) -> dict:
    """Manifeste reproductible : hash du contenu (pas juste la forme), pour
    qu'un checkout propre puisse vérifier qu'il retrouve le MÊME panel."""
    payload = df.sort_index().to_csv().encode()
    m = {
        "n_rows": int(len(df)),
        "columns": list(df.columns),
        "index_span": [str(df.index.min()), str(df.index.max())] if len(df) else None,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "z_win": Z_WIN, "z_min": Z_MIN, "extreme_q": EXTREME_Q,
    }
    if extra:
        m.update(extra)
    return m


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    fund_dir = root / "data" / "derivatives_backfill"
    if not fund_dir.exists():
        print(f"BLOCKED_BY_DATA : {fund_dir} absent sur cette machine — "
              "aucune donnée Binance/Bybit funding réelle disponible ici. "
              "Phase 1 (intégrité, tests/test_stress_gate_dispersion_v2.py) "
              "est vérifiable sans données réelles ; Phase 2 (test "
              "événementiel primaire) requiert cette machine de recherche "
              "ou l'accès à qbee@100.127.59.114 (inaccessible depuis cet "
              "environnement, voir research/forensics/stress_gate_c78874b/). "
              "Aucun chiffre Phase 2/3/4 n'a été produit.")
        raise SystemExit(1)
    raise NotImplementedError(
        "chargement des données réelles non implémenté ici — écrire une "
        "fois les chemins réels de data/derivatives_backfill connus sur la "
        "machine d'exécution, PAS avant (pas de code mort non testable)")
