"""
trading-system/src/institutional/contracts.py
═══════════════════════════════════════════════════════════════════════════════
Contrats fondamentaux — couche zéro de l'INSTITUTIONAL_ENGINE.

RÈGLES D'IMPORT :
    Ce module n'importe rien d'autre que la stdlib.
    Tous les autres modules institutionnels importent depuis ici.
    Jamais l'inverse.

CONTRATS :
    Direction, EngineID, Verdict  — enums StrEnum
    SignalFrame                   — interface engine → portfolio (frozen)
    RobustnessScore               — score de robustesse anti-overfit (frozen)

Python 3.11+ requis (StrEnum, match, Self, slots=True natif).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Final, Self


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════


class Direction(StrEnum):
    """Direction d'un signal de trading."""

    LONG  = "long"
    SHORT = "short"
    FLAT  = "flat"


class EngineID(StrEnum):
    """Identifiants des moteurs de signaux autorisés."""

    TRM           = "TRM_EVENT_ENGINE"
    INSTITUTIONAL = "INSTITUTIONAL_ENGINE"
    META          = "META_PORTFOLIO_ENGINE"


class Verdict(StrEnum):
    """
    Verdict de validation d'une expérience ou d'un signal.

    Ordre croissant de maturité :
        REJECT < INCUBATE < PAPER < PROMOTE < LIVE_READY
    """

    REJECT     = "REJECT"
    INCUBATE   = "INCUBATE"
    PAPER      = "PAPER"
    PROMOTE    = "PROMOTE"
    LIVE_READY = "LIVE_READY"
    PENDING    = "PENDING"


# ══════════════════════════════════════════════════════════════════════════════
# Constantes
# ══════════════════════════════════════════════════════════════════════════════

SIGNAL_FRAME_COLUMNS: Final[tuple[str, ...]] = (
    "timestamp",
    "asset",
    "engine_name",
    "signal_name",
    "direction",
    "raw_score",
    "calibrated_score",
    "confidence",
    "expected_return",
    "expected_vol",
    "horizon_minutes",
    "max_holding_minutes",
    "stop_distance",
    "take_profit_distance",
    "model_version",
    "feature_version",
    "label_version",
    "run_id",
)

_ROBUSTNESS_WEIGHTS: Final[tuple[float, ...]] = (
    0.20,  # pf_score
    0.20,  # cost_score
    0.15,  # shuffle_score
    0.15,  # year_stability_score
    0.10,  # threshold_stability_score
    0.10,  # contribution_score
    0.05,  # drawdown_score
    0.05,  # trade_count_score
)


# ══════════════════════════════════════════════════════════════════════════════
# SignalFrame
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class SignalFrame:
    """
    Contrat d'interface entre un Signal Engine et le Portfolio Engine.

    Immuable (frozen=True). Toute modification produit un nouvel objet.
    Toutes les colonnes sont obligatoires — aucun champ optionnel.

    Invariants :
        calibrated_score  ∈ [0.0, 1.0]
        confidence        ∈ [0.0, 1.0]
        expected_vol      > 0.0
        horizon_minutes   > 0
        max_holding_minutes ≥ horizon_minutes
        stop_distance     > 0.0
        take_profit_distance > 0.0

    Compatibilité :
        engine_name = "TRM_EVENT_ENGINE"      → TRM_EVENT_ENGINE
        engine_name = "INSTITUTIONAL_ENGINE"  → INSTITUTIONAL_ENGINE
    """

    timestamp:            datetime
    asset:                str
    engine_name:          str       # EngineID ou chaîne libre pour extensibilité
    signal_name:          str
    direction:            Direction

    # ── Scores ────────────────────────────────────────────────────────────────
    raw_score:            float     # score brut modèle (non borné)
    calibrated_score:     float     # probabilité calibrée ∈ [0.0, 1.0]
    confidence:           float     # confiance estimée ∈ [0.0, 1.0]

    # ── Attendus ──────────────────────────────────────────────────────────────
    expected_return:      float     # E[r] sur horizon (fraction du prix)
    expected_vol:         float     # σ annualisée > 0.0

    # ── Timing ────────────────────────────────────────────────────────────────
    horizon_minutes:      int       # horizon de prédiction > 0
    max_holding_minutes:  int       # durée max de la position > 0

    # ── Niveaux de prix ───────────────────────────────────────────────────────
    stop_distance:        float     # stop-loss (fraction du prix) > 0.0
    take_profit_distance: float     # take-profit (fraction du prix) > 0.0

    # ── Versioning ────────────────────────────────────────────────────────────
    model_version:        str
    feature_version:      str
    label_version:        str
    run_id:               str

    # ── Validation ────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        errors: list[str] = []

        # Champs textuels non vides
        for fname, fval in (
            ("asset",         self.asset),
            ("engine_name",   self.engine_name),
            ("signal_name",   self.signal_name),
            ("model_version", self.model_version),
            ("run_id",        self.run_id),
        ):
            if not fval or not fval.strip():
                errors.append(f"{fname} ne peut pas être vide")

        # Bornes des probabilités
        if not (0.0 <= self.calibrated_score <= 1.0):
            errors.append(
                f"calibrated_score={self.calibrated_score!r} hors [0.0, 1.0]"
            )
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(
                f"confidence={self.confidence!r} hors [0.0, 1.0]"
            )

        # Strictement positifs
        for fname, fval in (
            ("expected_vol",         self.expected_vol),
            ("stop_distance",        self.stop_distance),
            ("take_profit_distance", self.take_profit_distance),
        ):
            if fval <= 0.0:
                errors.append(f"{fname}={fval!r} doit être > 0.0")

        # Entiers positifs
        for fname, fval in (
            ("horizon_minutes",     self.horizon_minutes),
            ("max_holding_minutes", self.max_holding_minutes),
        ):
            if fval <= 0:
                errors.append(f"{fname}={fval!r} doit être > 0")

        # Cohérence temporelle
        if (
            self.horizon_minutes > 0
            and self.max_holding_minutes > 0
            and self.max_holding_minutes < self.horizon_minutes
        ):
            errors.append(
                f"max_holding_minutes={self.max_holding_minutes} "
                f"< horizon_minutes={self.horizon_minutes}"
            )

        if errors:
            detail = "\n".join(f"  • {e}" for e in errors)
            raise ValueError(
                f"SignalFrame invalide ({len(errors)} erreur(s)) :\n{detail}"
            )

    # ── Sérialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp":            self.timestamp.isoformat(),
            "asset":                self.asset,
            "engine_name":          self.engine_name,
            "signal_name":          self.signal_name,
            "direction":            str(self.direction),
            "raw_score":            self.raw_score,
            "calibrated_score":     self.calibrated_score,
            "confidence":           self.confidence,
            "expected_return":      self.expected_return,
            "expected_vol":         self.expected_vol,
            "horizon_minutes":      self.horizon_minutes,
            "max_holding_minutes":  self.max_holding_minutes,
            "stop_distance":        self.stop_distance,
            "take_profit_distance": self.take_profit_distance,
            "model_version":        self.model_version,
            "feature_version":      self.feature_version,
            "label_version":        self.label_version,
            "run_id":               self.run_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Self:
        return cls(
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
            asset=str(data["asset"]),
            engine_name=str(data["engine_name"]),
            signal_name=str(data["signal_name"]),
            direction=Direction(str(data["direction"])),
            raw_score=float(str(data["raw_score"])),
            calibrated_score=float(str(data["calibrated_score"])),
            confidence=float(str(data["confidence"])),
            expected_return=float(str(data["expected_return"])),
            expected_vol=float(str(data["expected_vol"])),
            horizon_minutes=int(str(data["horizon_minutes"])),
            max_holding_minutes=int(str(data["max_holding_minutes"])),
            stop_distance=float(str(data["stop_distance"])),
            take_profit_distance=float(str(data["take_profit_distance"])),
            model_version=str(data["model_version"]),
            feature_version=str(data["feature_version"]),
            label_version=str(data["label_version"]),
            run_id=str(data["run_id"]),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(json.loads(raw))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @classmethod
    def make_flat(
        cls,
        *,
        timestamp: datetime,
        asset: str,
        engine_name: str | EngineID,
        signal_name: str,
        run_id: str,
        model_version: str = "unknown",
        feature_version: str = "unknown",
        label_version: str = "unknown",
    ) -> Self:
        """Construit un signal FLAT (aucune position à prendre)."""
        return cls(
            timestamp=timestamp,
            asset=asset,
            engine_name=str(engine_name),
            signal_name=signal_name,
            direction=Direction.FLAT,
            raw_score=0.0,
            calibrated_score=0.5,
            confidence=0.0,
            expected_return=0.0,
            expected_vol=0.20,
            horizon_minutes=60,
            max_holding_minutes=240,
            stop_distance=0.02,
            take_profit_distance=0.04,
            model_version=model_version,
            feature_version=feature_version,
            label_version=label_version,
            run_id=run_id,
        )

    def is_actionable(self) -> bool:
        """True si le signal déclenche une action (direction != FLAT)."""
        return self.direction != Direction.FLAT

    def replace(self, **changes: object) -> Self:
        """
        Retourne une copie avec les champs remplacés (respecte l'immuabilité).

        Exemple :
            new_sf = sf.replace(confidence=0.9, direction=Direction.LONG)
        """
        current = self.to_dict()
        for key, val in changes.items():
            if key not in current:
                raise KeyError(f"SignalFrame n'a pas de champ {key!r}")
            current[key] = str(val) if isinstance(val, Direction) else val
        return self.from_dict(current)


# ══════════════════════════════════════════════════════════════════════════════
# RobustnessScore
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class RobustnessScore:
    """
    Score de robustesse composite ∈ [0.0, 1.0] pour un signal ou portefeuille.

    Chaque composante ∈ [0.0, 1.0] représente la réussite d'un test anti-overfit.
    Le total est une somme pondérée → verdict REJECT / INCUBATE / PAPER / PROMOTE / LIVE_READY.

    Seuils du verdict :
        ≥ 0.85 → LIVE_READY
        ≥ 0.75 → PROMOTE
        ≥ 0.60 → PAPER
        ≥ 0.45 → INCUBATE
        < 0.45 → REJECT
    """

    pf_score:                  float  # PF hors-échantillon
    cost_score:                float  # résistance frais ×2
    shuffle_score:             float  # chute performance avec labels shufflés
    year_stability_score:      float  # aucune année ne domine
    threshold_stability_score: float  # PF graduel vs threshold (pas de falaise)
    contribution_score:        float  # contribution marginale positive
    drawdown_score:            float  # contrôle du drawdown
    trade_count_score:         float  # nombre de trades statistiquement valide

    def __post_init__(self) -> None:
        _names = (
            "pf_score", "cost_score", "shuffle_score",
            "year_stability_score", "threshold_stability_score",
            "contribution_score", "drawdown_score", "trade_count_score",
        )
        invalid = [
            f"{n}={getattr(self, n)!r}"
            for n in _names
            if not (0.0 <= getattr(self, n) <= 1.0)
        ]
        if invalid:
            raise ValueError(
                f"RobustnessScore : composantes hors [0.0, 1.0] → {', '.join(invalid)}"
            )

    @property
    def total_score(self) -> float:
        components = (
            self.pf_score, self.cost_score, self.shuffle_score,
            self.year_stability_score, self.threshold_stability_score,
            self.contribution_score, self.drawdown_score, self.trade_count_score,
        )
        return round(
            sum(w * s for w, s in zip(_ROBUSTNESS_WEIGHTS, components)),
            6,
        )

    @property
    def verdict(self) -> Verdict:
        s = self.total_score
        if s >= 0.85:
            return Verdict.LIVE_READY
        elif s >= 0.75:
            return Verdict.PROMOTE
        elif s >= 0.60:
            return Verdict.PAPER
        elif s >= 0.45:
            return Verdict.INCUBATE
        return Verdict.REJECT

    def to_dict(self) -> dict[str, object]:
        return {
            "pf_score":                  self.pf_score,
            "cost_score":                self.cost_score,
            "shuffle_score":             self.shuffle_score,
            "year_stability_score":      self.year_stability_score,
            "threshold_stability_score": self.threshold_stability_score,
            "contribution_score":        self.contribution_score,
            "drawdown_score":            self.drawdown_score,
            "trade_count_score":         self.trade_count_score,
            "total_score":               self.total_score,
            "verdict":                   str(self.verdict),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Self:
        return cls(
            pf_score=float(str(data["pf_score"])),
            cost_score=float(str(data["cost_score"])),
            shuffle_score=float(str(data["shuffle_score"])),
            year_stability_score=float(str(data["year_stability_score"])),
            threshold_stability_score=float(str(data["threshold_stability_score"])),
            contribution_score=float(str(data["contribution_score"])),
            drawdown_score=float(str(data["drawdown_score"])),
            trade_count_score=float(str(data["trade_count_score"])),
        )
