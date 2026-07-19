"""
ai/level_0/augmentation.py — DATA AUGMENTATION POUR LABELS FINANCIERS
======================================================================

Problème résolu :
  Les modèles (TRM specialists) souffrent d'un manque de données positives
  (y_long=1) pour les folds précoces (2020: 896 positifs, 2021: 2332).
  Avec moins de 3000 positifs, les spécialistes ont AUC ≈ 0.50-0.60.
  Au-delà de 8000 positifs, AUC monte à 0.80+.

Solution — deux techniques complémentaires :

  1. SMOTE financier (Synthetic Minority Over-sampling TEchnique)
     Crée des exemples synthétiques en interpolant entre des exemples positifs
     réels dans l'espace de features. Préserve la structure des données.

  2. Time-window jitter
     Pour chaque signal (barre avec y_long=1), ajoute les barres adjacentes
     (t-1, t+1) avec un return légèrement bruité. Simule le fait qu'un signal
     "vrai" aurait pu être détecté 1h avant ou après.

Garanties anti-leakage :
  - Les exemples synthétiques sont uniquement dans l'espace des features
  - Le label future_ret_4h des exemples synthétiques est interpolé (pas forward-looking)
  - Les exemples synthétiques sont marqués is_synthetic=1 pour le debug

Usage dans walk_forward_4h.py :
  from ai.level_0.augmentation import augment_positives
  train_combined = augment_positives(train_combined, multiplier=3)
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors

from ai.level_0.constants import TARGET_COL as _DEFAULT_TARGET_COL


def _smote_for_positives(
    X_pos: np.ndarray,
    y_pos: np.ndarray,
    k: int = 5,
    n_synthetic: int = None,
    noise_pct: float = 0.05,
    rng: np.random.Generator = None,
) -> np.ndarray:
    """
    SMOTE classique sur l'ensemble des exemples positifs.

    Pour chaque exemple positif, trouve k voisins les plus proches et crée
    des exemples synthétiques en interpolant entre le point et ses voisins.

    noise_pct : bruit gaussien ajouté sur l'interpolation (0.05 = 5% de std)
    """
    if rng is None:
        rng = np.random.default_rng(42)

    n_real    = len(X_pos)
    if n_real < k + 1:
        return X_pos.copy()

    if n_synthetic is None:
        n_synthetic = n_real  # doubler par défaut

    # KNN sur les positifs uniquement
    knn = NearestNeighbors(n_neighbors=k + 1, metric="euclidean", n_jobs=-1)
    knn.fit(X_pos)
    _, neighbors = knn.kneighbors(X_pos)  # shape (n_real, k+1)
    neighbors = neighbors[:, 1:]          # exclure soi-même

    X_synth = []
    n_per_sample = max(1, n_synthetic // n_real)

    for i in range(n_real):
        for _ in range(n_per_sample):
            # Choisir un voisin aléatoire
            j   = neighbors[i, rng.integers(0, k)]
            lam = rng.uniform(0, 1)
            # Interpoler
            x_new = X_pos[i] + lam * (X_pos[j] - X_pos[i])
            # Ajouter bruit gaussien léger
            noise = rng.standard_normal(x_new.shape) * noise_pct * np.std(X_pos, axis=0)
            x_new = x_new + noise
            X_synth.append(x_new)

    return np.array(X_synth, dtype=np.float32)


def augment_positives(
    df: pd.DataFrame,
    features: List[str],
    label_col: str = "y_long",
    target_col: str = None,
    multiplier: int = 3,
    k_neighbors: int = 5,
    noise_pct: float = 0.04,
    min_pos_for_augment: int = 50,
    max_pos_threshold: int = 5_000,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Augmente les exemples positifs dans df par SMOTE jusqu'à `multiplier` fois.

    Arguments
    ---------
    df              : DataFrame d'entraînement (multi-actif combiné)
    features        : liste de features (FEATURES_LONG)
    label_col       : colonne de label (y_long)
    target_col      : colonne de rendement forward (pour interpolation)
    multiplier      : facteur d'augmentation (3 = tripler les positifs)
    k_neighbors     : voisins pour SMOTE
    noise_pct       : bruit gaussien (fraction de std)
    min_pos_for_augment : ne pas augmenter si déjà assez de positifs
    max_pos_threshold   : cap — ne pas créer plus de max_pos_threshold positifs total

    Retourne
    --------
    DataFrame avec les exemples synthétiques ajoutés (même colonnes + is_synthetic).
    Les exemples synthétiques ont y_long=1 et TARGET_COL interpolé.
    """
    if target_col is None:
        target_col = _DEFAULT_TARGET_COL
    y = df[label_col].values.astype(np.int32)
    pos_mask = y == 1
    n_pos    = int(pos_mask.sum())

    if n_pos < min_pos_for_augment:
        return df.copy()

    if n_pos >= max_pos_threshold:
        return df.copy()   # assez de données, pas besoin d'augmenter

    # Calculer le nombre de synthétiques à créer
    n_target  = min(n_pos * multiplier, max_pos_threshold) - n_pos
    if n_target <= 0:
        return df.copy()

    avail_feats = [f for f in features if f in df.columns]
    df_pos      = df.loc[pos_mask].copy()
    X_pos       = df_pos[avail_feats].values.astype(np.float32)

    # Remplacer NaN et Inf par 0 (valeur neutre pour le KNN)
    X_pos = np.nan_to_num(X_pos, nan=0.0, posinf=0.0, neginf=0.0)

    # Normaliser pour le KNN (évite que les features à grande échelle dominent)
    feat_std   = X_pos.std(axis=0)
    feat_std   = np.where(feat_std < 1e-9, 1.0, feat_std)
    X_pos_norm = X_pos / feat_std

    rng     = np.random.default_rng(seed)
    X_synth = _smote_for_positives(
        X_pos_norm, y[pos_mask],
        k=min(k_neighbors, n_pos - 1),
        n_synthetic=n_target,
        noise_pct=noise_pct,
        rng=rng,
    )
    # Dénormaliser
    X_synth = X_synth * feat_std

    # Construire le DataFrame synthétique
    synth_rows = []
    ret_pos = df_pos[target_col].values if target_col in df_pos.columns else np.zeros(n_pos)

    for i, x_syn in enumerate(X_synth):
        # Label = 1 (positif synthétique)
        # Return interpolé depuis les deux voisins les plus proches
        dists  = np.linalg.norm(X_pos - x_syn, axis=1)
        nearest = int(np.argmin(dists))
        ret_syn = float(ret_pos[nearest]) + rng.normal(0, 0.002)   # bruit 0.2%

        row = {f: float(x_syn[j]) for j, f in enumerate(avail_feats)}
        row[label_col]  = 1
        row[target_col] = ret_syn
        row["is_synthetic"] = 1
        synth_rows.append(row)

    df_synth = pd.DataFrame(synth_rows)

    # Aligner les colonnes avec df original
    for col in df.columns:
        if col not in df_synth.columns:
            if col == label_col:
                df_synth[col] = 1
            elif col == "y_short":
                df_synth[col] = 0
            elif col == "tradeable_net":
                df_synth[col] = 1
            else:
                df_synth[col] = 0.0

    df_out = pd.concat([df, df_synth[df.columns.tolist()]], ignore_index=True)
    df_out["is_synthetic"] = df_out.get("is_synthetic", pd.Series(0, index=df_out.index)).fillna(0).astype(int)

    n_new = len(df_synth)
    print(f"   SMOTE augmentation : {n_pos} → {n_pos + n_new} positifs "
          f"({n_new} synthétiques  ×{multiplier:.1f})")

    return df_out


# ─── Regime-aware oversampling (v3) ──────────────────────────────────────────

def regime_aware_augment(
    df: pd.DataFrame,
    features: List[str],
    label_col: str = "y_long",
    target_col: Optional[str] = None,
    regime_col: str = "regime_long",
    global_target_pos: int = 3000,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Oversampling par régime de marché — v3.

    Principe :
      Les signaux long en phase BEAR (NO_LONG) et NEUTRAL sont rares mais
      précieux pour la généralisation. On les oversample plus agressivement
      que les signaux en phase LONGABLE (bull) pour rééquilibrer.

      Multipliers par régime :
        LONGABLE  : ×2  — déjà bien représenté
        NEUTRAL   : ×4  — sous-représenté
        NO_LONG   : ×6  — très rare, très utile pour la robustesse

      Résultat : le modèle voit autant d'exemples bear que bull →
      meilleures prédictions lors des transitions de régime.

    Paramètres
    ----------
    df               : DataFrame d'entraînement complet
    features         : liste des features d'entrée
    label_col        : colonne de label binaire (y_long)
    target_col       : colonne de rendement forward (pour interpolation)
    regime_col       : colonne de régime ('LONGABLE', 'NEUTRAL', 'NO_LONG')
    global_target_pos: nombre total de positifs cible après augmentation
    seed             : graine aléatoire
    """
    if target_col is None:
        target_col = _DEFAULT_TARGET_COL

    if label_col not in df.columns:
        return df.copy()

    # Multipliers par régime — calibrés pour équilibrer la distribution
    REGIME_MULT: dict = {
        "LONGABLE":  2,
        "NEUTRAL":   3,   # réduit de 4 → 3
        "NO_LONG":   2,   # réduit de 6 → 2 (sur-trading 2025 corrigé)
        "EXPANSION": 2,
        "RECOVERY":  2,
    }
    DEFAULT_MULT = 2

    y = df[label_col].values.astype(np.int32)
    n_pos_total = int((y == 1).sum())

    if n_pos_total < 30:
        return df.copy()

    avail = [f for f in features if f in df.columns]
    rng   = np.random.default_rng(seed)

    parts = [df.copy()]
    regime_stats: list = []

    # Identifier les régimes disponibles
    if regime_col in df.columns:
        regimes = df[regime_col].fillna("NEUTRAL").unique().tolist()
    else:
        # Fallback sans colonne de régime : SMOTE global standard
        regimes = ["_ALL_"]

    for regime in regimes:
        if regime == "_ALL_":
            mask = pd.Series([True] * len(df), index=df.index)
        else:
            mask = df[regime_col].fillna("NEUTRAL") == regime

        df_r   = df[mask]
        y_r    = y[mask.values]
        n_pos_r = int((y_r == 1).sum())

        if n_pos_r < 10:
            continue

        # Multiplicateur pour ce régime
        mult = REGIME_MULT.get(regime, DEFAULT_MULT)

        # Ne pas dépasser le budget global
        n_already = sum(len(p) for p in parts)
        budget_left = max(0, global_target_pos - n_pos_total)
        n_synth = min(n_pos_r * (mult - 1), budget_left)
        if n_synth <= 0:
            continue

        df_pos_r = df_r[y_r == 1]
        X_pos    = df_pos_r[avail].fillna(0.0).values.astype(np.float32)
        X_pos    = np.nan_to_num(X_pos, nan=0.0, posinf=0.0, neginf=0.0)

        feat_std = X_pos.std(axis=0)
        feat_std = np.where(feat_std < 1e-9, 1.0, feat_std)
        X_norm   = X_pos / feat_std

        k = min(5, n_pos_r - 1)
        if k < 1:
            continue

        X_synth = _smote_for_positives(
            X_norm, y_r[y_r == 1],
            k=k, n_synthetic=int(n_synth),
            noise_pct=0.04, rng=rng,
        )
        X_synth = X_synth * feat_std

        ret_pos = (df_pos_r[target_col].values
                   if target_col in df_pos_r.columns
                   else np.zeros(n_pos_r))

        synth_rows = []
        for i, x_syn in enumerate(X_synth):
            dists   = np.linalg.norm(X_pos - x_syn, axis=1)
            nearest = int(np.argmin(dists))
            ret_syn = float(ret_pos[nearest]) + rng.normal(0, 0.002)
            row = {f: float(x_syn[j]) for j, f in enumerate(avail)}
            row[label_col]       = 1
            row[target_col]      = ret_syn
            row["is_synthetic"]  = 1
            if regime_col in df.columns:
                row[regime_col]  = regime
            synth_rows.append(row)

        if synth_rows:
            df_synth_r = pd.DataFrame(synth_rows)
            for col in df.columns:
                if col not in df_synth_r.columns:
                    df_synth_r[col] = 0.0
            parts.append(df_synth_r[df.columns.tolist()])

        regime_stats.append(
            f"{regime}:{n_pos_r}→{n_pos_r + len(synth_rows)} (×{mult})"
        )

    if len(parts) > 1:
        df_out = pd.concat(parts, ignore_index=True)
        df_out["is_synthetic"] = df_out.get(
            "is_synthetic", pd.Series(0, index=df_out.index)
        ).fillna(0).astype(int)
        n_new_total = len(df_out) - len(df)
        print(f"   Regime-aware SMOTE : {n_pos_total}→{n_pos_total+n_new_total} positifs")
        if regime_stats:
            print(f"   Régimes : {' | '.join(regime_stats)}")
        return df_out

    return df.copy()
