"""
trading-system/src/institutional/data/checker.py
═══════════════════════════════════════════════════════════════════════════════
DataQualityChecker — gate hostile avant tout calcul.

PHILOSOPHIE :
    Aucune feature, aucun label, aucun backtest ne peut démarrer tant que
    DataQualityReport.is_valid() n'est pas True.
    Le checker produit toujours un rapport (jamais de raise) — c'est l'appelant
    qui décide d'interrompre via PipelineBlockedError.

CHECKS IMPLÉMENTÉS (20) :
    Index :
        1.  timestamps non triés
        2.  timestamps dupliqués
        3.  timezone absente
        4.  timezone non-UTC
        5.  fréquence incohérente

    Colonnes :
        6.  colonnes obligatoires absentes

    Numériques :
        7.  valeurs NaN
        8.  valeurs Inf / -Inf

    Prix :
        9.  prix ≤ 0
        10. high < low
        11. high < open
        12. high < close
        13. low > open
        14. low > close

    Volume :
        15. volume < 0
        16. volume NaN

    Temporels :
        17. gaps > seuil
        18. stale data (fermetures identiques consécutives)

    Statistiques :
        19. outliers extrêmes (z-score log-return)
        20. max_gap absolu

Python 3.11+ requis. Pandas + NumPy requis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import numpy as np
import pandas as pd

from institutional.data.schemas import (
    DataQualityReport,
    QualityIssue,
    QualityLevel,
)

# ══════════════════════════════════════════════════════════════════════════════
# Exceptions
# ══════════════════════════════════════════════════════════════════════════════


class PipelineBlockedError(Exception):
    """
    Levée quand DataQualityReport échoue la validation.
    Bloque explicitement le calcul de features / labels / backtest.
    """

    def __init__(self, report: DataQualityReport) -> None:
        self.report = report
        super().__init__(
            f"Pipeline bloqué — {report.asset}/{report.source}: "
            f"{report.summary()}\n"
            f"Issues : {[str(i.level) + ':' + i.message for i in report.issues]}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

_OHLCV_COLS: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume")
_PRICE_COLS: Final[tuple[str, ...]] = ("open", "high", "low", "close")
_UTC_NAMES:  Final[frozenset[str]]  = frozenset({"UTC", "utc", "UTC+00:00", "+00:00"})


@dataclass(frozen=True, slots=True)
class CheckerConfig:
    """
    Paramètres du DataQualityChecker.

    Tous les seuils sont configurables — les valeurs par défaut correspondent
    à un usage sur données crypto futures 1h.
    """

    expected_freq: str = "1h"
    required_cols: tuple[str, ...] = _OHLCV_COLS

    # Seuils NaN
    nan_rate_warning:  float = 0.01   # > 1 % → WARNING
    nan_rate_critical: float = 0.05   # > 5 % → CRITICAL (bloque)

    # Gaps : tolérance = gap_multiplier × expected_freq
    gap_multiplier: float = 1.5       # gap > 1.5 × freq → issue

    # Stale data : fermetures identiques consécutives
    max_stale_bars: int = 5           # > 5 barres identiques → issue

    # Outliers : z-score log-return
    outlier_zscore: float = 6.0       # |z| > 6 → outlier

    # Saut de prix max (log) avant d'être flagué outlier
    max_price_jump_log: float = 0.50  # 50 % → outlier

    # Fréquence : tolérance d'inférence
    freq_inference_quantile: float = 0.05  # utiliser 5e percentile des deltas

    def __post_init__(self) -> None:
        if self.nan_rate_warning > self.nan_rate_critical:
            raise ValueError(
                "nan_rate_warning doit être ≤ nan_rate_critical"
            )
        if self.outlier_zscore <= 0:
            raise ValueError("outlier_zscore doit être > 0")
        if self.gap_multiplier < 1.0:
            raise ValueError("gap_multiplier doit être ≥ 1.0")
        if self.max_stale_bars < 1:
            raise ValueError("max_stale_bars doit être ≥ 1")


# ══════════════════════════════════════════════════════════════════════════════
# DataQualityChecker
# ══════════════════════════════════════════════════════════════════════════════


class DataQualityChecker:
    """
    Vérifie exhaustivement un DataFrame OHLCV.

    Retourne toujours un DataQualityReport (jamais de raise).
    Pour bloquer le pipeline en cas d'échec : appeler check_or_raise().

    Usage :
        checker = DataQualityChecker(CheckerConfig(expected_freq="1h"))
        report  = checker.check(df, asset="BTCUSDT", source="futures")

        if not report.is_valid():
            raise PipelineBlockedError(report)

        # ou plus court :
        checker.check_or_raise(df, asset="BTCUSDT", source="futures")
    """

    def __init__(self, config: CheckerConfig | None = None) -> None:
        self.config = config or CheckerConfig()
        self._expected_offset = pd.tseries.frequencies.to_offset(
            self.config.expected_freq
        )
        # Durée en secondes de la fréquence attendue
        self._expected_seconds: float = (
            self._expected_offset.nanos / 1e9
            if hasattr(self._expected_offset, "nanos")
            else self._freq_to_seconds(self.config.expected_freq)
        )

    # ── API publique ──────────────────────────────────────────────────────────

    def check(
        self,
        df: pd.DataFrame,
        asset: str,
        source: str,
    ) -> DataQualityReport:
        """
        Exécute tous les checks et retourne un DataQualityReport complet.
        N'interrompt jamais le processus — c'est à l'appelant de décider.
        """
        issues: list[QualityIssue] = []
        n_rows = len(df)

        # 1–4 : Index / timestamps
        dup_count = self._check_index_sorted(df, issues)
        dup_count += self._check_index_duplicates(df, issues)
        self._check_timezone(df, issues)

        # 5 : Fréquence
        self._check_frequency(df, issues)

        # 6 : Colonnes requises
        self._check_required_columns(df, issues)

        # 7–8 : NaN / Inf
        missing_rate, nan_by_col = self._check_nan(df, issues)
        self._check_inf(df, issues)

        # 9–14 : Prix
        n_invalid_price = self._check_prices(df, issues)
        self._check_ohlc_consistency(df, issues)

        # 15–16 : Volume
        self._check_volume(df, issues)

        # 17 : Gaps
        max_gap_minutes, n_gaps = self._check_gaps(df, issues)

        # 18 : Stale data
        stale_intervals = self._check_stale(df, issues)

        # 19–20 : Outliers
        outlier_count = self._check_outliers(df, issues)

        # Comptage final
        valid_rows = max(0, n_rows - dup_count - n_invalid_price)
        rejected_rows = n_rows - valid_rows

        first_ts: datetime | None = None
        last_ts: datetime | None = None
        if n_rows > 0 and isinstance(df.index, pd.DatetimeIndex):
            raw_min = df.index.min()
            raw_max = df.index.max()
            if raw_min is not pd.NaT:
                first_ts = raw_min.to_pydatetime()
            if raw_max is not pd.NaT:
                last_ts = raw_max.to_pydatetime()

        return DataQualityReport(
            asset=asset,
            source=source,
            timeframe=self.config.expected_freq,
            rows=n_rows,
            valid_rows=valid_rows,
            rejected_rows=rejected_rows,
            duplicate_count=dup_count,
            stale_intervals=stale_intervals,
            missing_rate=missing_rate,
            max_gap_minutes=max_gap_minutes,
            outlier_count=outlier_count,
            first_timestamp=first_ts,
            last_timestamp=last_ts,
            issues=tuple(issues),
        )

    def check_or_raise(
        self,
        df: pd.DataFrame,
        asset: str,
        source: str,
    ) -> DataQualityReport:
        """
        Comme check(), mais lève PipelineBlockedError si is_valid() == False.
        C'est la forme idiomatique pour bloquer le pipeline.
        """
        report = self.check(df, asset, source)
        if not report.is_valid():
            raise PipelineBlockedError(report)
        return report

    # ── Checks privés ─────────────────────────────────────────────────────────

    def _check_index_sorted(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> int:
        """Vérifie que l'index est trié croissant."""
        if not isinstance(df.index, pd.DatetimeIndex):
            issues.append(QualityIssue(
                level=QualityLevel.CRITICAL,
                field="index",
                message=(
                    f"Index de type {type(df.index).__name__!r} "
                    f"— DatetimeIndex UTC obligatoire"
                ),
            ))
            return 0

        if not df.index.is_monotonic_increasing:
            n_inv = int((df.index[1:] < df.index[:-1]).sum())
            issues.append(QualityIssue(
                level=QualityLevel.CRITICAL,
                field="index",
                message=(
                    f"Index non trié : {n_inv} inversion(s) détectée(s) — "
                    f"trier avant tout calcul"
                ),
            ))
            return n_inv
        return 0

    def _check_index_duplicates(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> int:
        """Détecte les timestamps dupliqués."""
        if not isinstance(df.index, pd.DatetimeIndex):
            return 0
        dup_mask = df.index.duplicated(keep="first")
        n_dup = int(dup_mask.sum())
        if n_dup > 0:
            examples = df.index[dup_mask][:3].tolist()
            issues.append(QualityIssue(
                level=QualityLevel.CRITICAL,
                field="index",
                message=(
                    f"{n_dup} timestamp(s) dupliqué(s) — "
                    f"exemples : {[str(e) for e in examples]}"
                ),
            ))
        return n_dup

    def _check_timezone(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> None:
        """Vérifie que le timezone est présent et UTC."""
        if not isinstance(df.index, pd.DatetimeIndex):
            return

        if df.index.tz is None:
            issues.append(QualityIssue(
                level=QualityLevel.CRITICAL,
                field="index.tz",
                message=(
                    "Timezone absente — les jointures as-of seraient incorrectes. "
                    "Utiliser df.index.tz_localize('UTC')"
                ),
            ))
            return

        tz_name = str(df.index.tz)
        if tz_name not in _UTC_NAMES:
            issues.append(QualityIssue(
                level=QualityLevel.CRITICAL,
                field="index.tz",
                message=(
                    f"Timezone {tz_name!r} ≠ UTC — "
                    f"convertir avec df.index.tz_convert('UTC')"
                ),
            ))

    def _check_frequency(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> None:
        """Détecte une fréquence incohérente avec expected_freq."""
        if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 3:
            return

        deltas_s = (
            df.index.to_series()
            .diff()
            .dropna()
            .dt.total_seconds()
        )
        # Utiliser le 5e percentile (robuste aux gaps)
        p05 = float(deltas_s.quantile(self.config.freq_inference_quantile))

        if p05 <= 0:
            return

        ratio = p05 / self._expected_seconds
        # Tolérance : 50 % autour de la valeur attendue
        if not (0.5 <= ratio <= 1.5):
            issues.append(QualityIssue(
                level=QualityLevel.WARNING,
                field="index.freq",
                message=(
                    f"Fréquence inférée ≈ {p05:.0f}s ≠ attendue "
                    f"{self._expected_seconds:.0f}s ({self.config.expected_freq}) "
                    f"— ratio={ratio:.2f}"
                ),
            ))

    def _check_required_columns(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> None:
        """Vérifie la présence des colonnes obligatoires."""
        missing = [c for c in self.config.required_cols if c not in df.columns]
        if missing:
            issues.append(QualityIssue(
                level=QualityLevel.CRITICAL,
                field="columns",
                message=f"Colonnes obligatoires absentes : {missing}",
            ))

    def _check_nan(
        self,
        df: pd.DataFrame,
        issues: list[QualityIssue],
    ) -> tuple[float, dict[str, int]]:
        """Détecte les valeurs NaN par colonne. Retourne (missing_rate, counts)."""
        numeric = df.select_dtypes(include="number")
        if numeric.empty:
            return 0.0, {}

        nan_counts: dict[str, int] = {}
        for col in numeric.columns:
            n = int(numeric[col].isna().sum())
            if n > 0:
                nan_counts[col] = n

        total_cells = max(numeric.size, 1)
        total_nan   = sum(nan_counts.values())
        rate        = total_nan / total_cells

        if rate > 0:
            col_summary = {c: v for c, v in nan_counts.items() if v > 0}
            level = (
                QualityLevel.CRITICAL
                if rate > self.config.nan_rate_critical
                else QualityLevel.WARNING
            )
            issues.append(QualityIssue(
                level=level,
                field="nan",
                message=f"{total_nan} NaN ({rate:.2%}) — colonnes : {col_summary}",
            ))

        return rate, nan_counts

    def _check_inf(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> None:
        """Détecte les valeurs Inf / -Inf."""
        numeric = df.select_dtypes(include="number")
        if numeric.empty:
            return

        inf_mask = np.isinf(numeric.values)
        n_inf    = int(inf_mask.sum())

        if n_inf > 0:
            cols_with_inf = [
                col for col in numeric.columns
                if np.isinf(numeric[col].values).any()
            ]
            issues.append(QualityIssue(
                level=QualityLevel.CRITICAL,
                field="inf",
                message=(
                    f"{n_inf} valeur(s) Inf/-Inf — colonnes : {cols_with_inf} — "
                    f"jamais acceptables dans un pipeline ML"
                ),
            ))

    def _check_prices(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> int:
        """Vérifie que les prix sont strictement positifs. Retourne n_invalid."""
        available = [c for c in _PRICE_COLS if c in df.columns]
        if not available:
            return 0

        n_invalid = 0
        for col in available:
            col_data = df[col].dropna()
            bad = (col_data <= 0).sum()
            if bad > 0:
                n_invalid += int(bad)
                issues.append(QualityIssue(
                    level=QualityLevel.CRITICAL,
                    field=col,
                    message=(
                        f"{bad} prix ≤ 0 dans {col!r} — "
                        f"min={float(col_data.min()):.6f}"
                    ),
                ))
        return n_invalid

    def _check_ohlc_consistency(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> None:
        """
        Vérifie les invariants OHLC :
            high ≥ open, high ≥ close, high ≥ low
            low  ≤ open, low  ≤ close
        """
        cols = {c for c in ("open", "high", "low", "close") if c in df.columns}
        if len(cols) < 4:
            return

        o, h, l, c = df["open"], df["high"], df["low"], df["close"]
        valid = df[["open", "high", "low", "close"]].notna().all(axis=1)

        checks: list[tuple[str, pd.Series, str]] = [
            ("high",  h[valid] < l[valid],  "high < low"),
            ("high",  h[valid] < o[valid],  "high < open"),
            ("high",  h[valid] < c[valid],  "high < close"),
            ("low",   l[valid] > o[valid],  "low > open"),
            ("low",   l[valid] > c[valid],  "low > close"),
        ]

        for field_name, mask, description in checks:
            n_bad = int(mask.sum())
            if n_bad > 0:
                idx_example = df.index[valid][mask].tolist()[:2]
                issues.append(QualityIssue(
                    level=QualityLevel.CRITICAL,
                    field=field_name,
                    message=(
                        f"{n_bad} barre(s) avec {description} — "
                        f"ex : {[str(i) for i in idx_example]}"
                    ),
                ))

    def _check_volume(
        self, df: pd.DataFrame, issues: list[QualityIssue]
    ) -> None:
        """Vérifie que le volume est non-négatif et non-NaN."""
        if "volume" not in df.columns:
            return

        vol = df["volume"]

        n_nan = int(vol.isna().sum())
        if n_nan > 0:
            issues.append(QualityIssue(
                level=QualityLevel.WARNING,
                field="volume",
                message=f"Volume NaN sur {n_nan} barre(s)",
            ))

        n_neg = int((vol.dropna() < 0).sum())
        if n_neg > 0:
            issues.append(QualityIssue(
                level=QualityLevel.CRITICAL,
                field="volume",
                message=(
                    f"{n_neg} volume(s) < 0 — "
                    f"min={float(vol.dropna().min()):.4f}"
                ),
            ))

    def _check_gaps(
        self,
        df: pd.DataFrame,
        issues: list[QualityIssue],
    ) -> tuple[float, int]:
        """
        Détecte les trous temporels.
        Retourne (max_gap_minutes, n_gaps_exceeding_threshold).
        """
        if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 2:
            return 0.0, 0

        deltas_s = (
            df.index.to_series()
            .diff()
            .dropna()
            .dt.total_seconds()
        )

        max_gap_s   = float(deltas_s.max())
        max_gap_min = max_gap_s / 60.0

        threshold_s = self._expected_seconds * self.config.gap_multiplier
        n_gaps      = int((deltas_s > threshold_s).sum())

        if n_gaps > 0:
            gap_idx = deltas_s[deltas_s > threshold_s].index.tolist()[:3]
            issues.append(QualityIssue(
                level=QualityLevel.WARNING,
                field="index.gaps",
                message=(
                    f"{n_gaps} gap(s) > {threshold_s/60:.1f}min "
                    f"(max={max_gap_min:.1f}min) — "
                    f"ex : {[str(i) for i in gap_idx]}"
                ),
            ))

        return max_gap_min, n_gaps

    def _check_stale(
        self,
        df: pd.DataFrame,
        issues: list[QualityIssue],
    ) -> int:
        """
        Détecte les périodes de stale data :
        fermetures identiques pendant > max_stale_bars barres consécutives.
        """
        if "close" not in df.columns or len(df) < 2:
            return 0

        close = df["close"].dropna()
        if len(close) < 2:
            return 0

        # Identifier les runs de valeurs identiques
        changed = (close != close.shift(1)).astype(int)
        run_id  = changed.cumsum()

        run_lengths = close.groupby(run_id).transform("count")
        max_run     = int(run_lengths.max())

        n_stale_intervals = int((run_lengths > self.config.max_stale_bars).sum())

        if max_run > self.config.max_stale_bars:
            issues.append(QualityIssue(
                level=QualityLevel.WARNING,
                field="close",
                message=(
                    f"Stale data : {max_run} barres consécutives avec close identique "
                    f"(seuil={self.config.max_stale_bars}) — "
                    f"possibles barres vides ou données manquantes"
                ),
            ))

        return n_stale_intervals

    def _check_outliers(
        self,
        df: pd.DataFrame,
        issues: list[QualityIssue],
    ) -> int:
        """
        Détecte les sauts de prix extrêmes (outliers log-return).
        Utilise un z-score rolling ou global.
        """
        if "close" not in df.columns or len(df) < 3:
            return 0

        close = df["close"].dropna()
        if len(close) < 3:
            return 0

        log_ret = np.log(close / close.shift(1)).dropna()
        if len(log_ret) == 0:
            return 0

        mean = float(log_ret.mean())
        std  = float(log_ret.std())

        if std == 0:
            return 0

        z_scores = (log_ret - mean) / std
        n_outliers_z = int((z_scores.abs() > self.config.outlier_zscore).sum())

        # Aussi détecter les sauts bruts > max_price_jump_log
        n_outliers_raw = int((log_ret.abs() > self.config.max_price_jump_log).sum())

        n_outliers = max(n_outliers_z, n_outliers_raw)

        if n_outliers > 0:
            worst_jump = float(log_ret.abs().max())
            issues.append(QualityIssue(
                level=QualityLevel.WARNING,
                field="close",
                message=(
                    f"{n_outliers} outlier(s) de prix — "
                    f"saut max={worst_jump:.2%} "
                    f"(seuil z={self.config.outlier_zscore}, "
                    f"seuil log={self.config.max_price_jump_log:.0%})"
                ),
            ))

        return n_outliers

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _freq_to_seconds(freq: str) -> float:
        """Convertit une chaîne de fréquence pandas en secondes."""
        mapping: dict[str, float] = {
            "1T": 60, "5T": 300, "15T": 900, "30T": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400,
            "1d": 86400, "1D": 86400, "1W": 604800,
        }
        if freq in mapping:
            return mapping[freq]
        # Fallback : utiliser pandas
        try:
            offset = pd.tseries.frequencies.to_offset(freq)
            return offset.nanos / 1e9  # type: ignore[union-attr]
        except Exception:
            return 3600.0  # default 1h
