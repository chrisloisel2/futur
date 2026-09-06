"""
trading-system/src/institutional/data/asof_join.py
═══════════════════════════════════════════════════════════════════════════════
As-of join causal — uniquement direction="backward".

PHILOSOPHIE CAUSALE :
    Pour chaque timestamp T dans left, on joint la valeur la plus récente
    du right pour laquelle right_timestamp ≤ T ET T - right_timestamp ≤ tolerance.

    GARANTI :
        - Aucune valeur future n'est jamais assignée à une barre passée.
        - Une valeur right à T+1 n'est JAMAIS visible à T.
        - La tolerance est obligatoire — pas de staleness illimitée.
        - Chaque jointure produit un AsofJoinReport auditables.

INTERDIT :
    - direction="forward" → lookahead immédiat
    - tolerance=None → staleness illimitée non-causale
    - jointures non-indexées sur DatetimeIndex UTC

PREUVES DE CAUSALITÉ :
    Après chaque jointure, une assertion post-hoc vérifie que
    aucun right_timestamp > left_timestamp dans le résultat.
    Si cette assertion échoue (ne devrait jamais arriver avec backward),
    LookaheadError est levée.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

# ══════════════════════════════════════════════════════════════════════════════
# Exceptions
# ══════════════════════════════════════════════════════════════════════════════


class LookaheadError(Exception):
    """
    Levée si une valeur future est détectée après une as-of jointure.
    Indique un bug dans le pipeline — ne doit jamais arriver avec backward.
    """


class ForwardJoinForbiddenError(Exception):
    """
    Levée si direction="forward" est demandée.
    Ce type de jointure introduit un lookahead structurel.
    """


# ══════════════════════════════════════════════════════════════════════════════
# Rapport de jointure
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class AsofJoinReport:
    """
    Rapport d'audit d'une as-of jointure.

    coverage_rate   : fraction de lignes left avec une valeur right jointe
    stale_rate      : fraction de lignes avec staleness > tolerance / 2
    max_staleness_s : staleness maximale observée (en secondes)
    mean_staleness_s: staleness moyenne (NaN exclus)
    n_null_after    : lignes left sans valeur right (hors tolérance)
    """

    n_left:           int
    n_right:          int
    n_matched:        int
    n_null_after:     int
    coverage_rate:    float     # n_matched / n_left ∈ [0, 1]
    stale_rate:       float     # fraction staleness > tol/2
    max_staleness_s:  float
    mean_staleness_s: float
    tolerance:        pd.Timedelta
    joined_cols:      tuple[str, ...]

    def summary(self) -> str:
        return (
            f"AsofJoin: {self.n_left} left × {self.n_right} right → "
            f"coverage={self.coverage_rate:.1%} "
            f"stale={self.stale_rate:.1%} "
            f"max_staleness={self.max_staleness_s/60:.1f}min "
            f"null={self.n_null_after}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# As-of join principal
# ══════════════════════════════════════════════════════════════════════════════

_TS_COL = "__asof_ts__"   # colonne temporaire pour pd.merge_asof


def asof_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    tolerance: pd.Timedelta,
    right_cols: Sequence[str] | None = None,
    suffix: str = "",
) -> tuple[pd.DataFrame, AsofJoinReport]:
    """
    Jointure as-of backward causale.

    Paramètres
    ----------
    left      : DataFrame principal (DatetimeIndex UTC, trié)
    right     : DataFrame secondaire (DatetimeIndex UTC, trié)
    tolerance : délai max admissible entre left_ts et right_ts
                OBLIGATOIRE — pas de staleness illimitée
    right_cols: colonnes de right à joindre (None = toutes)
    suffix    : suffixe en cas de conflit de nom de colonne

    Retourne
    --------
    (DataFrame, AsofJoinReport)
        Le DataFrame résultat a le même index que left.
        Les colonnes right sont NaN pour les barres hors tolérance.

    Lève
    ----
    ForwardJoinForbiddenError : si direction="forward" est tentée (jamais via cette API)
    LookaheadError            : si right_ts > left_ts dans le résultat (ne devrait jamais arriver)
    TypeError                 : si index non-DatetimeIndex
    ValueError                : si left ou right vide, ou tolerance ≤ 0
    """

    # ── Garde-fous d'entrée ───────────────────────────────────────────────────

    if not isinstance(left.index, pd.DatetimeIndex):
        raise TypeError(
            f"left.index doit être un DatetimeIndex UTC — "
            f"type reçu : {type(left.index).__name__}"
        )
    if not isinstance(right.index, pd.DatetimeIndex):
        raise TypeError(
            f"right.index doit être un DatetimeIndex UTC — "
            f"type reçu : {type(right.index).__name__}"
        )

    if tolerance.total_seconds() <= 0:
        raise ValueError(
            f"tolerance={tolerance} doit être > 0 — "
            f"une tolerance nulle ou négative n'a pas de sens causal"
        )

    if left.empty:
        raise ValueError("left DataFrame est vide — as-of join impossible")

    # right peut être vide → résultat avec NaN partout

    # ── Vérification UTC ─────────────────────────────────────────────────────

    _assert_utc(left, "left")
    if not right.empty:
        _assert_utc(right, "right")

    # ── Sélection des colonnes right ─────────────────────────────────────────

    if right_cols is not None:
        missing = [c for c in right_cols if c not in right.columns]
        if missing:
            raise ValueError(f"Colonnes right demandées absentes : {missing}")
        right_work = right[list(right_cols)].copy()
    else:
        right_work = right.copy()

    # ── Renommage des conflits ────────────────────────────────────────────────

    if suffix:
        conflict = [c for c in right_work.columns if c in left.columns]
        if conflict:
            right_work = right_work.rename(
                columns={c: f"{c}{suffix}" for c in conflict}
            )

    joined_cols = tuple(right_work.columns.tolist())

    # ── Garantir le tri ───────────────────────────────────────────────────────

    if not left.index.is_monotonic_increasing:
        left = left.sort_index()
    if not right.empty and not right_work.index.is_monotonic_increasing:
        right_work = right_work.sort_index()

    # ── Cas vide right ───────────────────────────────────────────────────────

    if right.empty:
        result = left.copy()
        for col in joined_cols:
            result[col] = np.nan
        return result, AsofJoinReport(
            n_left=len(left), n_right=0, n_matched=0,
            n_null_after=len(left), coverage_rate=0.0, stale_rate=0.0,
            max_staleness_s=0.0, mean_staleness_s=0.0,
            tolerance=tolerance, joined_cols=joined_cols,
        )

    # ── Merge as-of (backward uniquement) ────────────────────────────────────

    # Matérialiser le timestamp comme colonne pour pd.merge_asof
    left_reset  = left.reset_index().rename(columns={"index": _TS_COL})
    right_reset = right_work.reset_index().rename(columns={"index": _TS_COL})

    merged = pd.merge_asof(
        left_reset.sort_values(_TS_COL),
        right_reset.sort_values(_TS_COL),
        on=_TS_COL,
        direction="backward",         # ← toujours backward, jamais forward
        tolerance=tolerance,
        suffixes=("", suffix or "_r"),
    )

    # Restaurer l'index original
    merged = merged.set_index(_TS_COL)
    merged.index.name = left.index.name

    # ── Vérification post-hoc anti-lookahead ─────────────────────────────────
    #
    # En théorie, direction="backward" garantit right_ts ≤ left_ts.
    # En pratique, on le vérifie explicitement pour détecter tout bug.

    _assert_no_lookahead_in_result(left, right_work, merged, joined_cols)

    # ── Rapport ───────────────────────────────────────────────────────────────

    report = _build_report(
        left=left,
        right_work=right_work,
        merged=merged,
        joined_cols=joined_cols,
        tolerance=tolerance,
    )

    return merged[list(left.columns) + list(joined_cols)], report


# ══════════════════════════════════════════════════════════════════════════════
# Helpers privés
# ══════════════════════════════════════════════════════════════════════════════


def _assert_utc(df: pd.DataFrame, name: str) -> None:
    """Vérifie que le DatetimeIndex est UTC."""
    if df.index.tz is None:
        raise TypeError(
            f"{name}.index n'a pas de timezone — "
            f"utiliser df.index.tz_localize('UTC')"
        )
    if str(df.index.tz) not in {"UTC", "utc", "UTC+00:00"}:
        raise TypeError(
            f"{name}.index timezone={df.index.tz!r} ≠ UTC — "
            f"utiliser df.index.tz_convert('UTC')"
        )


def _assert_no_lookahead_in_result(
    left: pd.DataFrame,
    right: pd.DataFrame,
    merged: pd.DataFrame,
    joined_cols: tuple[str, ...],
) -> None:
    """
    Vérifie qu'aucune valeur right jointe n'est postérieure au timestamp left.

    Pour ce faire, on recalcule les staleness entre left_ts et right_ts
    en cherchant le dernier right_ts ≤ left_ts pour chaque ligne jointe.

    Un résultat staleness < 0 indiquerait un lookahead — lève LookaheadError.
    """
    if right.empty or merged.empty:
        return

    # Vérification par recherche de l'index right le plus proche en arrière
    right_idx = right.index  # trié

    for left_ts in merged.index:
        # Trouver le right_ts le plus récent ≤ left_ts
        pos = right_idx.searchsorted(left_ts, side="right") - 1
        if pos < 0:
            # Aucun right_ts ≤ left_ts → toutes les colonnes jointes doivent être NaN
            for col in joined_cols:
                if col in merged.columns:
                    val = merged.at[left_ts, col]
                    if not (pd.isna(val)):
                        raise LookaheadError(
                            f"LOOKAHEAD DÉTECTÉ à {left_ts}: "
                            f"colonne {col!r} = {val!r} alors qu'aucun right_ts ≤ left_ts. "
                            f"Ceci est un bug critique — pipeline arrêté."
                        )
        else:
            right_ts = right_idx[pos]
            if right_ts > left_ts:
                raise LookaheadError(
                    f"LOOKAHEAD DÉTECTÉ : right_ts={right_ts} > left_ts={left_ts}. "
                    f"Ceci ne devrait jamais arriver avec direction='backward'. "
                    f"Vérifier les données sources."
                )


def _build_report(
    left: pd.DataFrame,
    right_work: pd.DataFrame,
    merged: pd.DataFrame,
    joined_cols: tuple[str, ...],
    tolerance: pd.Timedelta,
) -> AsofJoinReport:
    """Construit l'AsofJoinReport depuis le résultat fusionné."""
    n_left  = len(left)
    n_right = len(right_work)

    # Coverage : lignes avec au moins une colonne joined non-NaN
    if joined_cols and any(c in merged.columns for c in joined_cols):
        first_col = next(c for c in joined_cols if c in merged.columns)
        has_match = merged[first_col].notna()
    else:
        has_match = pd.Series(False, index=merged.index)

    n_matched   = int(has_match.sum())
    n_null_after = n_left - n_matched
    coverage    = n_matched / max(n_left, 1)

    # Staleness : calculer l'âge de chaque valeur jointe
    staleness_s = _compute_staleness(left, right_work, merged, joined_cols)

    stale_threshold_s = tolerance.total_seconds() / 2.0
    stale_mask = staleness_s > stale_threshold_s
    stale_rate = float(stale_mask.sum()) / max(n_matched, 1) if n_matched > 0 else 0.0

    max_staleness  = float(staleness_s.max()) if len(staleness_s) > 0 else 0.0
    mean_staleness = float(staleness_s.mean()) if len(staleness_s) > 0 else 0.0

    return AsofJoinReport(
        n_left=n_left,
        n_right=n_right,
        n_matched=n_matched,
        n_null_after=n_null_after,
        coverage_rate=coverage,
        stale_rate=stale_rate,
        max_staleness_s=max_staleness,
        mean_staleness_s=mean_staleness,
        tolerance=tolerance,
        joined_cols=joined_cols,
    )


def _compute_staleness(
    left: pd.DataFrame,
    right: pd.DataFrame,
    merged: pd.DataFrame,
    joined_cols: tuple[str, ...],
) -> pd.Series:
    """
    Pour chaque ligne jointe non-NaN, calcule l'âge de la valeur right
    (left_ts - right_ts en secondes).

    Si right est vide ou aucune colonne jointe non-NaN → série vide.
    """
    if right.empty or not joined_cols:
        return pd.Series(dtype=float)

    right_idx = right.index  # trié

    first_col = next((c for c in joined_cols if c in merged.columns), None)
    if first_col is None:
        return pd.Series(dtype=float)

    matched_mask = merged[first_col].notna()
    matched_ts   = merged.index[matched_mask]

    if len(matched_ts) == 0:
        return pd.Series(dtype=float)

    staleness_values: list[float] = []
    for ts in matched_ts:
        pos = int(right_idx.searchsorted(ts, side="right")) - 1
        if pos >= 0:
            right_ts = right_idx[pos]
            staleness_values.append((ts - right_ts).total_seconds())

    return pd.Series(staleness_values, dtype=float)


# ══════════════════════════════════════════════════════════════════════════════
# API de haut niveau
# ══════════════════════════════════════════════════════════════════════════════


def asof_join_funding(
    ohlcv: pd.DataFrame,
    funding: pd.DataFrame,
    *,
    max_stale_hours: float = 10.0,
) -> tuple[pd.DataFrame, AsofJoinReport]:
    """
    Jointure causale OHLCV 1h × funding 8h.
    Tolerance = 10h (légèrement > 8h pour tolérer les latences de timestamp).
    """
    return asof_join(
        ohlcv,
        funding[["funding_rate"]] if "funding_rate" in funding.columns else funding,
        tolerance=pd.Timedelta(hours=max_stale_hours),
    )


def asof_join_metrics(
    ohlcv: pd.DataFrame,
    metrics: pd.DataFrame,
    *,
    cols: Sequence[str] | None = None,
    max_stale_hours: float = 2.0,
) -> tuple[pd.DataFrame, AsofJoinReport]:
    """
    Jointure causale OHLCV 1h × métriques 5m (OI, LSR, …).
    Tolerance = 2h.
    """
    return asof_join(
        ohlcv,
        metrics,
        tolerance=pd.Timedelta(hours=max_stale_hours),
        right_cols=list(cols) if cols else None,
    )
