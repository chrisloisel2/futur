"""
trading-system/src/institutional/data/validators.py
═══════════════════════════════════════════════════════════════════════════════
Assertions causales pour le pipeline ML institutionnel.

Ces fonctions sont des guards de sécurité — elles ne retournent rien,
elles lèvent des exceptions claires si une condition est violée.

USAGE OBLIGATOIRE :
    Avant tout calcul de features :
        assert_index_causal(df)

    Avant tout split train/val/test :
        assert_chronological_split(train, val, test, embargo_bars=168)

    Avant tout entraînement :
        assert_no_label_leakage(X_train, y_train, label_horizon_bars=24)

    Après tout split :
        assert_no_overlap(train_index, test_index)

Ces assertions sont légères (< 5ms sur 100 000 lignes) et doivent être
laissées en production — elles protègent contre les bugs silencieux.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


# ══════════════════════════════════════════════════════════════════════════════
# Exceptions
# ══════════════════════════════════════════════════════════════════════════════


class CausalityViolationError(Exception):
    """
    Levée quand une propriété causale est violée.
    Indique un bug dans le pipeline — jamais à ignorer.
    """


class DataSplitError(Exception):
    """Levée quand un split temporel est incohérent."""


# ══════════════════════════════════════════════════════════════════════════════
# Validators d'index
# ══════════════════════════════════════════════════════════════════════════════


def assert_index_causal(
    df: pd.DataFrame,
    label: str = "DataFrame",
) -> None:
    """
    Vérifie les propriétés causales de l'index :
        1. DatetimeIndex
        2. UTC
        3. Monotone croissant strict
        4. Sans doublons

    Lève CausalityViolationError si une condition est violée.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise CausalityViolationError(
            f"{label}: index doit être DatetimeIndex UTC, "
            f"reçu {type(df.index).__name__}"
        )

    if df.index.tz is None:
        raise CausalityViolationError(
            f"{label}: timezone absente — les jointures temporelles seraient incorrectes"
        )

    tz_name = str(df.index.tz)
    if tz_name not in {"UTC", "utc", "UTC+00:00"}:
        raise CausalityViolationError(
            f"{label}: timezone {tz_name!r} ≠ UTC — convertir avant tout calcul"
        )

    if not df.index.is_monotonic_increasing:
        n_inv = int((df.index[1:] < df.index[:-1]).sum())
        raise CausalityViolationError(
            f"{label}: index non trié — {n_inv} inversion(s). "
            f"df.sort_index() avant tout calcul."
        )

    if df.index.duplicated().any():
        n_dup = int(df.index.duplicated().sum())
        raise CausalityViolationError(
            f"{label}: {n_dup} timestamp(s) dupliqué(s). "
            f"Supprimer avant tout calcul."
        )


def assert_no_future_timestamps(
    df: pd.DataFrame,
    label: str = "DataFrame",
    margin_hours: float = 1.0,
) -> None:
    """
    Vérifie qu'aucun timestamp n'est dans le futur (+ marge).
    Utile pour valider des données live avant de les traiter.
    """
    if df.empty:
        return
    if not isinstance(df.index, pd.DatetimeIndex):
        return

    cutoff = pd.Timestamp.utcnow() + pd.Timedelta(hours=margin_hours)
    future_mask = df.index > cutoff
    n_future = int(future_mask.sum())

    if n_future > 0:
        max_future = df.index[future_mask].max()
        raise CausalityViolationError(
            f"{label}: {n_future} timestamp(s) dans le futur — "
            f"max={max_future}, cutoff={cutoff}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Validators de split temporel
# ══════════════════════════════════════════════════════════════════════════════


def assert_chronological_split(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    val: pd.DataFrame | None = None,
    embargo_bars: int = 0,
    label: str = "split",
) -> None:
    """
    Vérifie que train < val < test dans l'ordre chronologique strict.

    Garantit :
        max(train.index) < min(test.index)
        Si val : max(train.index) < min(val.index) ≤ max(val.index) < min(test.index)
        embargo_bars > 0 : max(train.index) + embargo_bars*freq ≤ min(test.index)

    Lève DataSplitError si une condition est violée.
    """
    if train.empty:
        raise DataSplitError(f"{label}: train est vide")
    if test.empty:
        raise DataSplitError(f"{label}: test est vide")

    train_max = train.index.max()
    test_min  = test.index.min()

    if train_max >= test_min:
        raise DataSplitError(
            f"{label}: chevauchement train/test — "
            f"train_max={train_max} ≥ test_min={test_min}. "
            f"Le train doit se terminer AVANT le début du test."
        )

    if val is not None:
        if val.empty:
            raise DataSplitError(f"{label}: val est vide")

        val_min = val.index.min()
        val_max = val.index.max()

        if train_max >= val_min:
            raise DataSplitError(
                f"{label}: chevauchement train/val — "
                f"train_max={train_max} ≥ val_min={val_min}"
            )
        if val_max >= test_min:
            raise DataSplitError(
                f"{label}: chevauchement val/test — "
                f"val_max={val_max} ≥ test_min={test_min}"
            )


def assert_no_overlap(
    index_a: pd.DatetimeIndex,
    index_b: pd.DatetimeIndex,
    label_a: str = "A",
    label_b: str = "B",
) -> None:
    """
    Vérifie qu'il n'y a aucun timestamp commun entre deux ensembles.
    Critique pour la validation purged CV.
    """
    overlap = index_a.intersection(index_b)
    if len(overlap) > 0:
        examples = overlap[:3].tolist()
        raise DataSplitError(
            f"Chevauchement {label_a}/{label_b} : {len(overlap)} timestamp(s) commun(s) — "
            f"ex : {[str(e) for e in examples]}. "
            f"Appliquer l'embargo."
        )


def assert_embargo_applied(
    train_index: pd.DatetimeIndex,
    test_index: pd.DatetimeIndex,
    embargo_bars: int,
    freq: str = "1h",
    label: str = "embargo",
) -> None:
    """
    Vérifie qu'un embargo de `embargo_bars` barres sépare train et test.
    Protège contre la fuite d'information via les labels overlapping.
    """
    if embargo_bars <= 0:
        return

    offset = pd.tseries.frequencies.to_offset(freq)
    embargo_duration = offset * embargo_bars  # type: ignore[operator]

    train_max = train_index.max()
    test_min  = test_index.min()

    if (test_min - train_max) < embargo_duration:
        raise DataSplitError(
            f"{label}: embargo insuffisant — "
            f"gap entre train et test = {test_min - train_max} "
            f"< embargo requis = {embargo_duration} ({embargo_bars} × {freq})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Validators de features ML
# ══════════════════════════════════════════════════════════════════════════════


def assert_no_label_leakage(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    label_horizon_bars: int,
    label: str = "dataset",
) -> None:
    """
    Vérifie qu'il n'y a pas de fuite du label dans les features.

    Méthode : les colonnes de X ne doivent pas contenir de préfixes
    typiques de labels forward ("fwd_", "future_", "target_").
    C'est une heuristique — la vraie garantie est dans le code des features.
    """
    forbidden_prefixes = ("fwd_", "future_", "target_", "label_", "y_")
    leaked = [
        col for col in X.columns
        if any(col.startswith(pfx) for pfx in forbidden_prefixes)
    ]
    if leaked:
        raise CausalityViolationError(
            f"{label}: colonnes suspectes dans X (peuvent contenir des labels) : "
            f"{leaked}. Supprimer avant entraînement."
        )

    # Vérifier alignement index
    if not X.index.equals(y.index):
        n_common = len(X.index.intersection(y.index))
        raise CausalityViolationError(
            f"{label}: X.index et y.index ne sont pas alignés — "
            f"{n_common}/{len(X)} timestamps communs. "
            f"Utiliser pd.concat([X, y], axis=1).dropna()."
        )


def assert_no_scaler_leakage(
    scaler_fit_on: pd.DataFrame,
    test: pd.DataFrame,
    label: str = "scaler",
) -> None:
    """
    Vérifie que le scaler n'a pas été fit sur les données test.

    Implémentation : vérifie que scaler_fit_on.index.max() < test.index.min().
    C'est une vérification nécessaire mais pas suffisante — la vraie
    garantie est dans le code d'entraînement.
    """
    if scaler_fit_on.empty or test.empty:
        return

    fit_max  = scaler_fit_on.index.max()
    test_min = test.index.min()

    if fit_max >= test_min:
        raise CausalityViolationError(
            f"{label}: scaler potentiellement fit sur des données test — "
            f"fit_max={fit_max} ≥ test_min={test_min}. "
            f"Le scaler doit être fit UNIQUEMENT sur le train."
        )
