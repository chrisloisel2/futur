"""
trading-system/src/institutional/data/schemas.py
═══════════════════════════════════════════════════════════════════════════════
Contrat DataQualityReport — qualité des données par asset/source.

Importé par : feature_store, data loaders, validators.
Importe depuis : contracts (enums uniquement — pas de SignalFrame).

Règle fondamentale :
    Aucune feature ne peut être calculée avant que
    DataQualityReport.is_valid(thresholds) == True.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════


class QualityLevel(StrEnum):
    OK       = "OK"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


# ══════════════════════════════════════════════════════════════════════════════
# Types de support
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class QualityIssue:
    """Problème de qualité détecté sur une source."""

    level:   QualityLevel
    field:   str    # colonne ou propriété concernée
    message: str

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("QualityIssue.field ne peut pas être vide")
        if not self.message.strip():
            raise ValueError("QualityIssue.message ne peut pas être vide")

    def to_dict(self) -> dict[str, str]:
        return {
            "level":   str(self.level),
            "field":   self.field,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    """
    Seuils de validation pour DataQualityReport.

    Instancier avec les valeurs projet ou laisser les valeurs par défaut.
    """

    max_missing_rate: float = 0.05    # fraction NaN max dans les colonnes critiques
    max_gap_minutes:  float = 1_500.0  # 25h — tolérer les frontières d'années
    max_outlier_rate: float = 0.01    # fraction d'outliers tolérée
    max_reject_rate:  float = 0.05    # fraction de lignes rejetées max
    max_duplicate_count: int = 0      # 0 = aucun doublon toléré

    def __post_init__(self) -> None:
        for name, val in (
            ("max_missing_rate", self.max_missing_rate),
            ("max_gap_minutes",  self.max_gap_minutes),
            ("max_outlier_rate", self.max_outlier_rate),
            ("max_reject_rate",  self.max_reject_rate),
        ):
            if val < 0.0:
                raise ValueError(f"QualityThresholds.{name}={val!r} doit être ≥ 0")
        if self.max_duplicate_count < 0:
            raise ValueError(
                f"max_duplicate_count={self.max_duplicate_count!r} doit être ≥ 0"
            )


# ══════════════════════════════════════════════════════════════════════════════
# DataQualityReport
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """
    Rapport de qualité immuable pour une source de données.

    Produit par DataQualityChecker (non défini ici).
    Champs obligatoires : aucun optionnel (first_timestamp / last_timestamp
    peuvent être None si le DataFrame est vide).

    Invariants :
        0 ≤ valid_rows ≤ rows
        0 ≤ rejected_rows ≤ rows
        valid_rows + rejected_rows ≤ rows
        0.0 ≤ missing_rate ≤ 1.0
        max_gap_minutes ≥ 0.0
    """

    asset:             str
    source:            str                  # "futures" | "spot" | "enriched"
    timeframe:         str                  # "1h" | "1d" | "5m" …

    # ── Comptages ─────────────────────────────────────────────────────────────
    rows:              int
    valid_rows:        int
    rejected_rows:     int
    duplicate_count:   int
    stale_intervals:   int                  # barres consécutives sans mouvement

    # ── Taux / métriques ──────────────────────────────────────────────────────
    missing_rate:      float                # fraction NaN dans colonnes OHLCV
    max_gap_minutes:   float                # plus long trou temporel
    outlier_count:     int                  # log-returns extrêmes

    # ── Bornes temporelles ────────────────────────────────────────────────────
    first_timestamp:   datetime | None
    last_timestamp:    datetime | None

    # ── Issues détaillées ─────────────────────────────────────────────────────
    issues:            tuple[QualityIssue, ...]

    # ── Validation ────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        errors: list[str] = []

        for name, val in (("asset", self.asset), ("source", self.source), ("timeframe", self.timeframe)):
            if not val or not val.strip():
                errors.append(f"{name} ne peut pas être vide")

        if self.rows < 0:
            errors.append(f"rows={self.rows!r} doit être ≥ 0")
        if not (0 <= self.valid_rows <= self.rows):
            errors.append(
                f"valid_rows={self.valid_rows!r} doit être dans [0, rows={self.rows}]"
            )
        if not (0 <= self.rejected_rows <= self.rows):
            errors.append(
                f"rejected_rows={self.rejected_rows!r} doit être dans [0, rows={self.rows}]"
            )
        if self.duplicate_count < 0:
            errors.append(f"duplicate_count={self.duplicate_count!r} doit être ≥ 0")
        if not (0.0 <= self.missing_rate <= 1.0):
            errors.append(f"missing_rate={self.missing_rate!r} hors [0.0, 1.0]")
        if self.max_gap_minutes < 0.0:
            errors.append(f"max_gap_minutes={self.max_gap_minutes!r} doit être ≥ 0")
        if self.outlier_count < 0:
            errors.append(f"outlier_count={self.outlier_count!r} doit être ≥ 0")

        if errors:
            detail = "\n".join(f"  • {e}" for e in errors)
            raise ValueError(f"DataQualityReport invalide :\n{detail}")

    # ── Évaluation ────────────────────────────────────────────────────────────

    def is_valid(
        self,
        thresholds: QualityThresholds | None = None,
    ) -> bool:
        """
        True si toutes les métriques respectent les seuils.
        À appeler avant tout calcul de features.
        """
        t = thresholds or QualityThresholds()
        reject_rate = self.rejected_rows / max(self.rows, 1)
        return (
            self.missing_rate    <= t.max_missing_rate
            and self.duplicate_count <= t.max_duplicate_count
            and self.max_gap_minutes <= t.max_gap_minutes
            and reject_rate      <= t.max_reject_rate
        )

    def quality_level(
        self,
        thresholds: QualityThresholds | None = None,
    ) -> QualityLevel:
        """Niveau de qualité global : OK / WARNING / CRITICAL."""
        if self.is_valid(thresholds):
            return QualityLevel.OK

        t = thresholds or QualityThresholds()
        n_failures = sum([
            self.missing_rate    > t.max_missing_rate,
            self.duplicate_count > t.max_duplicate_count,
            self.max_gap_minutes > t.max_gap_minutes,
            (self.rejected_rows / max(self.rows, 1)) > t.max_reject_rate,
        ])
        return QualityLevel.CRITICAL if n_failures >= 2 else QualityLevel.WARNING

    def coverage_days(self) -> float | None:
        """Nombre de jours couverts par la source (None si timestamps absents)."""
        if self.first_timestamp is None or self.last_timestamp is None:
            return None
        delta = self.last_timestamp - self.first_timestamp
        return delta.total_seconds() / 86_400

    def summary(self, thresholds: QualityThresholds | None = None) -> str:
        lvl = self.quality_level(thresholds)
        return (
            f"[{lvl!s:8s}] {self.asset}/{self.source}/{self.timeframe}"
            f"  rows={self.rows:>9,}  valid={self.valid_rows:>9,}"
            f"  missing={self.missing_rate:.2%}"
            f"  max_gap={self.max_gap_minutes:>6.0f}m"
            f"  dupes={self.duplicate_count}"
        )

    # ── Sérialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, object]:
        return {
            "asset":             self.asset,
            "source":            self.source,
            "timeframe":         self.timeframe,
            "rows":              self.rows,
            "valid_rows":        self.valid_rows,
            "rejected_rows":     self.rejected_rows,
            "duplicate_count":   self.duplicate_count,
            "stale_intervals":   self.stale_intervals,
            "missing_rate":      self.missing_rate,
            "max_gap_minutes":   self.max_gap_minutes,
            "outlier_count":     self.outlier_count,
            "first_timestamp":   (
                self.first_timestamp.isoformat() if self.first_timestamp else None
            ),
            "last_timestamp":    (
                self.last_timestamp.isoformat() if self.last_timestamp else None
            ),
            "is_valid":          self.is_valid(),
            "quality_level":     str(self.quality_level()),
            "issues":            [i.to_dict() for i in self.issues],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Self:
        raw_issues = data.get("issues") or []
        issues = tuple(
            QualityIssue(
                level=QualityLevel(str(i["level"])),
                field=str(i["field"]),
                message=str(i["message"]),
            )
            for i in raw_issues  # type: ignore[union-attr]
        )
        return cls(
            asset=str(data["asset"]),
            source=str(data["source"]),
            timeframe=str(data["timeframe"]),
            rows=int(str(data["rows"])),
            valid_rows=int(str(data["valid_rows"])),
            rejected_rows=int(str(data["rejected_rows"])),
            duplicate_count=int(str(data["duplicate_count"])),
            stale_intervals=int(str(data["stale_intervals"])),
            missing_rate=float(str(data["missing_rate"])),
            max_gap_minutes=float(str(data["max_gap_minutes"])),
            outlier_count=int(str(data["outlier_count"])),
            first_timestamp=(
                datetime.fromisoformat(str(data["first_timestamp"]))
                if data.get("first_timestamp")
                else None
            ),
            last_timestamp=(
                datetime.fromisoformat(str(data["last_timestamp"]))
                if data.get("last_timestamp")
                else None
            ),
            issues=issues,
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(json.loads(raw))
