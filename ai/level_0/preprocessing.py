"""
level_0/preprocessing.py — PREPROCESSING COMMUN
=================================================

Scaling, extraction de features, splits chronologiques.
Tous les composants utilisent ces fonctions — pas de duplication.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List, Tuple

from sklearn.preprocessing import StandardScaler

from ai.level_0.constants import (
    TRAIN_END_YEAR, VAL_YEAR, TEST_FROM_YEAR, DATETIME_COL,
    TARGET_COL, ATR_COL, RV_COL, CLOSE_COL,
)
from ai.level_0.features import validate_features


def chronological_split(
    df: pd.DataFrame,
    test_from_year: int = TEST_FROM_YEAR,
    train_end_year: int = TRAIN_END_YEAR,
    val_year: int = VAL_YEAR,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Split chronologique strict — aucun mélange temporel possible.

    train : années ≤ train_end_year
    val   : année  == val_year
    test  : années ≥ test_from_year

    Les splits ne doivent jamais se chevaucher.
    """
    if DATETIME_COL not in df.columns:
        raise RuntimeError(f"Colonne '{DATETIME_COL}' manquante pour le split.")

    years = df[DATETIME_COL].dt.year.values
    train_mask = years <= train_end_year
    val_mask   = years == val_year
    test_mask  = years >= test_from_year

    assert not (train_mask & val_mask).any(),  "Chevauchement train/val"
    assert not (val_mask   & test_mask).any(), "Chevauchement val/test"
    assert not (train_mask & test_mask).any(), "Chevauchement train/test"

    print(
        f"   Split  train ≤{train_end_year}: {train_mask.sum():,}  "
        f"val={val_year}: {val_mask.sum():,}  "
        f"test ≥{test_from_year}: {test_mask.sum():,}"
    )
    return train_mask, val_mask, test_mask


def get_X(df: pd.DataFrame, mask: np.ndarray,
          feature_list: List[str]) -> np.ndarray:
    """
    Extrait la matrice de features pour un masque donné.
    Lève une erreur si des colonnes sont manquantes ou contiennent des NaN.
    """
    validate_features(df, feature_list, context="get_X")
    X = df.loc[mask, feature_list].values.astype(np.float32)
    if np.isnan(X).any():
        raise RuntimeError(
            "NaN détectés dans la matrice de features après extraction. "
            "Appliquer dropna avant get_X."
        )
    return X


def fit_scaler(X_train: np.ndarray) -> StandardScaler:
    """
    Ajuste un StandardScaler sur les données d'entraînement uniquement.
    Ne jamais ajuster sur val ou test.
    """
    sc = StandardScaler()
    sc.fit(X_train)
    return sc


def load_csv(path_arg: str, feature_list: List[str]) -> pd.DataFrame:
    """
    Charge un CSV enrichi, valide les colonnes requises et nettoie les NaN.

    Arguments
    ---------
    path_arg     : chemin vers le CSV ou dossier de CSVs
    feature_list : features à valider (ex : FEATURES_LONG)
    """
    from pathlib import Path
    p = Path(path_arg)

    if p.is_dir():
        files = sorted(p.glob("*features*.csv")) or sorted(p.glob("*.csv"))
        if not files:
            raise RuntimeError(f"Aucun CSV dans {p}")
        frames = [pd.read_csv(f, low_memory=False) for f in files]
        df = pd.concat(frames, ignore_index=True)
    else:
        df = pd.read_csv(p, low_memory=False)

    df[DATETIME_COL] = pd.to_datetime(df[DATETIME_COL], utc=True)
    df = df.sort_values(DATETIME_COL).reset_index(drop=True)

    required_cols = feature_list + [DATETIME_COL, CLOSE_COL, TARGET_COL,
                                    ATR_COL, RV_COL]
    required_cols = list(dict.fromkeys(required_cols))
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"Colonnes manquantes dans le CSV : {missing}\n"
            f"Vérifier le pipeline de feature engineering."
        )

    numeric_cols = [c for c in required_cols if c != DATETIME_COL]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    n_before = len(df)
    df = df.dropna(subset=numeric_cols).reset_index(drop=True)
    n_dropped = n_before - len(df)
    if n_dropped > 0:
        print(f"   {n_dropped:,} barres supprimées (NaN) sur {n_before:,}")

    print(
        f"   {len(df):,} barres  |  "
        f"{df[DATETIME_COL].iloc[0].date()} → {df[DATETIME_COL].iloc[-1].date()}"
    )
    return df
