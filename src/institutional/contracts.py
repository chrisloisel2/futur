"""
src/institutional/contracts.py
─────────────────────────────────────────────────────────────────────────────
Contrats de données institutionnels — interface entre tous les engines.

Aucun module ne doit importer directement depuis un autre engine.
TRM_EVENT_ENGINE et INSTITUTIONAL_ENGINE communiquent uniquement via ces contrats.

Schéma de version : contracts sont versionnés indépendamment du code.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from typing import Literal
except ImportError:  # pragma: no cover - Python <3.8 fallback
    Literal = None  # type: ignore

import pandas as pd


SIGNAL_FRAME_VERSION = "1.0.0"
PORTFOLIO_STATE_VERSION = "1.0.0"
RISK_STATE_VERSION = "1.0.0"
OPPORTUNITY_VERSION = "1.0.0"

SIGNAL_DIRECTIONS = frozenset(("long", "short", "flat"))
ENGINE_NAMES = frozenset(("TRM_EVENT_ENGINE", "INSTITUTIONAL_ENGINE", "META_PORTFOLIO"))


# ─── SignalFrame ──────────────────────────────────────────────────────────────

@dataclass
class SignalFrame:
    """
    Contrat standard produit par tout signal engine.
    Toutes les colonnes sont obligatoires — aucune valeur NaN tolérée.

    TRM_EVENT_ENGINE  → engine_name="TRM_EVENT_ENGINE"
    INSTITUTIONAL_ENGINE → engine_name="INSTITUTIONAL_ENGINE"
    """
    timestamp: pd.Timestamp
    asset: str                      # "BTCUSDT", "ETHUSDT", …
    engine_name: str                # see ENGINE_NAMES
    signal_name: str                # "trend_following_v1", "carry_funding_v2", …
    direction: str                  # "long" | "short" | "flat"
    raw_score: float                # score brut modèle (non borné)
    calibrated_score: float         # proba calibrée ∈ [0, 1]
    confidence: float               # confiance estimée ∈ [0, 1]
    expected_return: float          # E[r] sur horizon_minutes (fraction)
    expected_vol: float             # σ annualisée estimée (fraction, > 0)
    horizon_minutes: int            # horizon de prédiction
    max_holding_minutes: int        # durée max de la position
    stop_distance: float            # stop-loss distance (fraction du prix)
    take_profit_distance: float     # take-profit distance (fraction du prix)
    model_version: str
    feature_version: str
    label_version: str
    run_id: str

    def validate(self) -> None:
        if self.direction not in SIGNAL_DIRECTIONS:
            raise ValueError(f"direction={self.direction!r} must be in {SIGNAL_DIRECTIONS}")
        if not (0.0 <= self.calibrated_score <= 1.0):
            raise ValueError(f"calibrated_score={self.calibrated_score} hors [0,1]")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence={self.confidence} hors [0,1]")
        if self.expected_vol <= 0:
            raise ValueError(f"expected_vol={self.expected_vol} doit être > 0")
        if self.horizon_minutes <= 0:
            raise ValueError(f"horizon_minutes={self.horizon_minutes} doit être > 0")
        if not self.asset:
            raise ValueError("asset vide")
        if not self.engine_name:
            raise ValueError("engine_name vide")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SignalFrame":
        d = d.copy()
        d["timestamp"] = pd.Timestamp(d["timestamp"])
        return cls(**d)

    @classmethod
    def make_flat(
        cls,
        timestamp: pd.Timestamp,
        asset: str,
        engine_name: str,
        signal_name: str,
        run_id: str = "unknown",
        model_version: str = "unknown",
        feature_version: str = "unknown",
        label_version: str = "unknown",
    ) -> "SignalFrame":
        return cls(
            timestamp=timestamp,
            asset=asset,
            engine_name=engine_name,
            signal_name=signal_name,
            direction="flat",
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


SIGNAL_FRAME_COLUMNS = [
    "timestamp", "asset", "engine_name", "signal_name", "direction",
    "raw_score", "calibrated_score", "confidence", "expected_return",
    "expected_vol", "horizon_minutes", "max_holding_minutes",
    "stop_distance", "take_profit_distance", "model_version",
    "feature_version", "label_version", "run_id",
]


def validate_signal_frame_df(df: pd.DataFrame) -> None:
    """Vérifie qu'un DataFrame respecte le schéma SignalFrame."""
    missing = [c for c in SIGNAL_FRAME_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"SignalFrame colonnes manquantes : {missing}")
    if df["calibrated_score"].between(0, 1).all() is False:
        raise ValueError("calibrated_score hors [0, 1]")
    if df["expected_vol"].le(0).any():
        raise ValueError("expected_vol <= 0 détecté")
    if not df["direction"].isin(SIGNAL_DIRECTIONS).all():
        raise ValueError("direction invalide détectée")


# ─── Position ─────────────────────────────────────────────────────────────────

@dataclass
class Position:
    asset: str
    size: float                         # en unité d'actif (négatif = short)
    notional_usd: float                 # |size| × prix courant
    entry_price: float
    current_price: float
    unrealized_pnl: float
    weight: float                       # fraction de l'equity portefeuille
    engine_name: str
    signal_name: str
    open_timestamp: pd.Timestamp
    stop_price: float
    take_profit_price: float
    max_holding_until: Optional[pd.Timestamp] = None

    def update_price(self, price: float) -> None:
        self.current_price = price
        self.notional_usd = abs(self.size) * price
        self.unrealized_pnl = self.size * (price - self.entry_price)

    def is_long(self) -> bool:
        return self.size > 0

    def is_short(self) -> bool:
        return self.size < 0


# ─── PortfolioState ───────────────────────────────────────────────────────────

@dataclass
class PortfolioState:
    timestamp: pd.Timestamp
    cash: float
    equity: float
    positions: Dict[str, Position] = field(default_factory=dict)
    unrealized_pnl: float = 0.0
    realized_pnl_today: float = 0.0
    realized_pnl_total: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    leverage: float = 0.0
    n_positions: int = 0
    version: str = PORTFOLIO_STATE_VERSION

    def refresh(self) -> None:
        """Recalcule les métriques agrégées depuis les positions."""
        self.unrealized_pnl = sum(p.unrealized_pnl for p in self.positions.values())
        self.equity = self.cash + self.unrealized_pnl
        self.gross_exposure = sum(abs(p.notional_usd) for p in self.positions.values())
        longs = sum(p.notional_usd for p in self.positions.values() if p.size > 0)
        shorts = sum(p.notional_usd for p in self.positions.values() if p.size < 0)
        self.net_exposure = longs + shorts
        self.leverage = self.gross_exposure / max(self.equity, 1e-9)
        self.n_positions = len(self.positions)

    def per_engine_exposure(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for p in self.positions.values():
            out[p.engine_name] = out.get(p.engine_name, 0.0) + abs(p.notional_usd)
        return out

    def per_asset_exposure(self) -> Dict[str, float]:
        return {k: abs(v.notional_usd) for k, v in self.positions.items()}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "cash": self.cash,
            "equity": self.equity,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl_today": self.realized_pnl_today,
            "realized_pnl_total": self.realized_pnl_total,
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "leverage": self.leverage,
            "n_positions": self.n_positions,
            "positions": {
                k: {
                    **{f: getattr(v, f) for f in [
                        "asset", "size", "notional_usd", "entry_price",
                        "current_price", "unrealized_pnl", "weight",
                        "engine_name", "signal_name", "stop_price", "take_profit_price"
                    ]},
                    "open_timestamp": v.open_timestamp.isoformat(),
                    "max_holding_until": v.max_holding_until.isoformat() if v.max_holding_until else None,
                }
                for k, v in self.positions.items()
            }
        }

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))


# ─── RiskState ────────────────────────────────────────────────────────────────

@dataclass
class RiskState:
    timestamp: pd.Timestamp = field(default_factory=lambda: pd.Timestamp.utcnow())
    day_pnl: float = 0.0
    week_pnl: float = 0.0
    month_pnl: float = 0.0
    realized_drawdown: float = 0.0
    peak_equity: float = 0.0
    unrealized_pnl: float = 0.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    current_exposure: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    per_asset_exposure: Dict[str, float] = field(default_factory=dict)
    per_engine_exposure: Dict[str, float] = field(default_factory=dict)
    last_trade_timestamp: Optional[pd.Timestamp] = None
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    cooldown_until: Optional[pd.Timestamp] = None
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    version: str = RISK_STATE_VERSION

    def update_drawdown(self, current_equity: float) -> None:
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        self.realized_drawdown = (current_equity - self.peak_equity) / max(self.peak_equity, 1e-9)

    def record_trade(self, pnl: float, timestamp: pd.Timestamp) -> None:
        self.total_trades += 1
        self.day_pnl += pnl
        self.week_pnl += pnl
        self.month_pnl += pnl
        self.last_trade_timestamp = timestamp
        if pnl > 0:
            self.total_wins += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.total_losses += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0

    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_wins / self.total_trades

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "day_pnl": self.day_pnl,
            "week_pnl": self.week_pnl,
            "month_pnl": self.month_pnl,
            "realized_drawdown": self.realized_drawdown,
            "peak_equity": self.peak_equity,
            "unrealized_pnl": self.unrealized_pnl,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins": self.consecutive_wins,
            "current_exposure": self.current_exposure,
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "per_asset_exposure": self.per_asset_exposure,
            "per_engine_exposure": self.per_engine_exposure,
            "last_trade_timestamp": (
                self.last_trade_timestamp.isoformat() if self.last_trade_timestamp else None
            ),
            "kill_switch_active": self.kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RiskState":
        d = d.copy()
        d["timestamp"] = pd.Timestamp(d.get("timestamp") or pd.Timestamp.utcnow())
        if d.get("last_trade_timestamp"):
            d["last_trade_timestamp"] = pd.Timestamp(d["last_trade_timestamp"])
        if d.get("cooldown_until"):
            d["cooldown_until"] = pd.Timestamp(d["cooldown_until"])
        d.pop("version", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "RiskState":
        if not Path(path).exists():
            return cls()
        d = json.loads(Path(path).read_text())
        return cls.from_dict(d)


# ─── DataQualityReport ────────────────────────────────────────────────────────

@dataclass
class DataQualityReport:
    asset: str
    source: str
    timeframe: str
    missing_rate: float
    duplicate_count: int
    stale_intervals: int
    max_gap_minutes: float
    outlier_count: int
    first_timestamp: Optional[pd.Timestamp]
    last_timestamp: Optional[pd.Timestamp]
    rows: int
    valid_rows: int
    rejected_rows: int
    issues: List[str] = field(default_factory=list)

    # Seuils de validation (configurables)
    # max_gap 1500min = 25h pour tolérer les frontières d'années (données partitionnées par an)
    _missing_rate_limit: float = field(default=0.05, repr=False, compare=False)
    _max_gap_limit: float = field(default=1500.0, repr=False, compare=False)
    _reject_rate_limit: float = field(default=0.05, repr=False, compare=False)

    @property
    def is_valid(self) -> bool:
        return (
            self.missing_rate <= self._missing_rate_limit
            and self.duplicate_count == 0
            and self.max_gap_minutes <= self._max_gap_limit
            and (self.rejected_rows / max(self.rows, 1)) <= self._reject_rate_limit
        )

    def summary(self) -> str:
        status = "OK  " if self.is_valid else "WARN"
        return (
            f"[{status}] {self.asset}/{self.source}/{self.timeframe}"
            f"  rows={self.rows:>7}  valid={self.valid_rows:>7}"
            f"  missing={self.missing_rate:.2%}  max_gap={self.max_gap_minutes:>5.0f}m"
            f"  dupes={self.duplicate_count}  outliers={self.outlier_count}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset": self.asset,
            "source": self.source,
            "timeframe": self.timeframe,
            "missing_rate": self.missing_rate,
            "duplicate_count": self.duplicate_count,
            "stale_intervals": self.stale_intervals,
            "max_gap_minutes": self.max_gap_minutes,
            "outlier_count": self.outlier_count,
            "first_timestamp": self.first_timestamp.isoformat() if self.first_timestamp else None,
            "last_timestamp": self.last_timestamp.isoformat() if self.last_timestamp else None,
            "rows": self.rows,
            "valid_rows": self.valid_rows,
            "rejected_rows": self.rejected_rows,
            "is_valid": self.is_valid,
            "issues": self.issues,
        }


# ─── ExperimentRecord ─────────────────────────────────────────────────────────

@dataclass
class ExperimentRecord:
    experiment_id: str
    timestamp: pd.Timestamp
    engine_name: str
    signal_name: str
    assets: List[str]
    features_version: str
    labels_version: str
    model_type: str
    model_params: Dict[str, Any]
    train_period: Dict[str, str]      # {"start": "2021-01-01", "end": "2022-12-31"}
    validation_period: Dict[str, str]
    test_period: Dict[str, str]
    walk_forward_config: Dict[str, Any]
    cost_config: Dict[str, Any]
    risk_config: Dict[str, Any]
    metrics: Dict[str, float]
    robustness_tests: Dict[str, Any]
    decision: str   # REJECT | INCUBATE | PAPER | PROMOTE | LIVE_READY
    notes: str
    artifact_paths: Dict[str, str]
    code_hash: str = ""

    VALID_DECISIONS = frozenset(("REJECT", "INCUBATE", "PAPER", "PROMOTE", "LIVE_READY"))

    def validate(self) -> None:
        if self.decision not in self.VALID_DECISIONS:
            raise ValueError(f"decision={self.decision!r} invalide")
        if not self.experiment_id:
            raise ValueError("experiment_id vide")

    def compute_code_hash(self, code: str) -> None:
        self.code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, default=str))

    @classmethod
    def load(cls, path: Path) -> "ExperimentRecord":
        d = json.loads(Path(path).read_text())
        d["timestamp"] = pd.Timestamp(d["timestamp"])
        return cls(**d)


# ─── RobustnessScore ─────────────────────────────────────────────────────────

@dataclass
class RobustnessScore:
    pf_score: float               # [0, 1] — PF net hors-échantillon
    cost_score: float             # [0, 1] — résistance cost ×2
    shuffle_score: float          # [0, 1] — delta vs labels shuffled
    year_stability_score: float   # [0, 1] — aucune année dominante
    threshold_stability_score: float
    contribution_score: float     # [0, 1] — contribution marginale positive
    drawdown_score: float         # [0, 1] — contrôle DD
    trade_count_score: float      # [0, 1] — trades suffisants

    _WEIGHTS = (0.20, 0.20, 0.15, 0.15, 0.10, 0.10, 0.05, 0.05)

    @property
    def total_score(self) -> float:
        scores = (
            self.pf_score, self.cost_score, self.shuffle_score,
            self.year_stability_score, self.threshold_stability_score,
            self.contribution_score, self.drawdown_score, self.trade_count_score,
        )
        return sum(w * s for w, s in zip(self._WEIGHTS, scores))

    @property
    def verdict(self) -> str:
        s = self.total_score
        if s >= 0.85:
            return "LIVE_READY"
        elif s >= 0.75:
            return "PROMOTE"
        elif s >= 0.60:
            return "PAPER"
        elif s >= 0.45:
            return "INCUBATE"
        return "REJECT"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pf_score": self.pf_score,
            "cost_score": self.cost_score,
            "shuffle_score": self.shuffle_score,
            "year_stability_score": self.year_stability_score,
            "threshold_stability_score": self.threshold_stability_score,
            "contribution_score": self.contribution_score,
            "drawdown_score": self.drawdown_score,
            "trade_count_score": self.trade_count_score,
            "total_score": self.total_score,
            "verdict": self.verdict,
        }


# ─── Opportunity ──────────────────────────────────────────────────────────────
# Contrat UNIQUE produit par TOUS les moteurs alpha (usine à opportunités).
# Remplace la décision binaire "p_long" par un objet tradable standardisé :
# rendement attendu, risque attendu, durée, coût, corrélation, zone et raison.

# Échelle de promotion live progressive (jamais paper → full live directement).
EngineStatusT = ("DISABLED", "SHADOW", "PAPER", "MICRO_LIVE", "HALF_LIVE", "FULL_LIVE")
ENGINE_STATUSES = frozenset(EngineStatusT)

# Trois zones de décision (remplace p ≥ τ → trade / sinon rien).
DECISION_ZONES = frozenset(("A_TRADE", "B_SHADOW", "C_REJECT"))

# Directions autorisées : LONG, hedge lié (jamais short nu), ou cash.
OPPORTUNITY_DIRECTIONS = frozenset(("LONG", "SHORT_HEDGE", "CASH"))

# Fraction de la taille "normale" autorisée selon le statut live.
STATUS_SIZE_FRACTION: Dict[str, float] = {
    "DISABLED": 0.0,
    "SHADOW": 0.0,
    "PAPER": 0.0,        # paper = pas de capital réel
    "MICRO_LIVE": 0.05,  # 1-5% de la taille normale
    "HALF_LIVE": 0.50,
    "FULL_LIVE": 1.0,
}


class ReasonCode(str, Enum):
    """Codes de raison de décision — explique CHAQUE trade et CHAQUE non-trade."""
    REJECT_LOW_PROBA = "REJECT_LOW_PROBA"
    REJECT_BEAR_NO_LONG = "REJECT_BEAR_NO_LONG"
    REJECT_SUPPRESSOR = "REJECT_SUPPRESSOR"
    REJECT_KILLSWITCH = "REJECT_KILLSWITCH"
    REJECT_EXPOSURE_LIMIT = "REJECT_EXPOSURE_LIMIT"
    REJECT_COOLDOWN = "REJECT_COOLDOWN"
    REJECT_CORRELATION = "REJECT_CORRELATION"
    REJECT_NO_DATA = "REJECT_NO_DATA"
    # Audit forward 2026-09-04 : score_net = expected_return - expected_cost.
    # Une valeur NÉGATIVE est, par construction, une espérance négative APRÈS
    # coûts -- il n'existe aucun dimensionnement qui rende une telle décision
    # rentable. 37,5 % des décisions forward de la fenêtre 03/09->04/09
    # portaient score_net < 0 et étaient malgré tout étiquetées ACCEPT_SHADOW
    # puis exécutées par le portefeuille. Ce code est le refus explicite
    # correspondant, appliqué à la porte de CAPITAL (le ledger, lui, continue
    # d'enregistrer la décision telle que le moteur l'a produite).
    REJECT_NEGATIVE_EXPECTED_VALUE = "REJECT_NEGATIVE_EXPECTED_VALUE"
    ACCEPT_TRADE = "ACCEPT_TRADE"
    ACCEPT_SHADOW = "ACCEPT_SHADOW"


@dataclass
class Opportunity:
    """
    Objet standardisé émis par tout moteur alpha.

    Toute décision (trade, shadow, reject) est représentée par une Opportunity ;
    le DecisionLedger en écrit une par heure × asset × moteur, même les rejets.
    """
    timestamp: pd.Timestamp
    engine_id: str
    asset: str
    direction: str                  # OPPORTUNITY_DIRECTIONS
    status: str                     # ENGINE_STATUSES

    p_success: float                # proba de succès calibrée ∈ [0, 1]
    expected_return: float          # E[r] net attendu sur l'horizon (fraction)
    expected_vol: float             # σ attendue (fraction, > 0)
    expected_holding_hours: float   # durée attendue de détention (heures)
    expected_cost: float            # coût aller-retour estimé (fraction)

    score_raw: float                # score brut du modèle (non borné)
    score_net: float               # score net coûts (utilisé pour le tri)
    confidence: float               # confiance ∈ [0, 1]

    regime: str                     # régime marché au moment de la décision
    correlation_bucket: str         # ex. "majors", "alts_l1", "cash"

    max_position_fraction: float    # taille max suggérée (fraction equity)
    stop_loss: Optional[float]      # distance stop (fraction) ou None
    take_profit: Optional[float]    # distance TP (fraction) ou None

    decision_zone: str              # DECISION_ZONES
    reason: str                     # ReasonCode.value
    version: str = OPPORTUNITY_VERSION

    # ── validation ────────────────────────────────────────────────────────────
    def validate(self) -> None:
        if self.direction not in OPPORTUNITY_DIRECTIONS:
            raise ValueError(f"direction={self.direction!r} must be in {OPPORTUNITY_DIRECTIONS}")
        if self.status not in ENGINE_STATUSES:
            raise ValueError(f"status={self.status!r} must be in {ENGINE_STATUSES}")
        if self.decision_zone not in DECISION_ZONES:
            raise ValueError(f"decision_zone={self.decision_zone!r} must be in {DECISION_ZONES}")
        if not (0.0 <= self.p_success <= 1.0):
            raise ValueError(f"p_success={self.p_success} hors [0,1]")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence={self.confidence} hors [0,1]")
        if self.expected_vol <= 0:
            raise ValueError(f"expected_vol={self.expected_vol} doit être > 0")
        if not self.asset:
            raise ValueError("asset vide")
        if not self.engine_id:
            raise ValueError("engine_id vide")

    def is_actionable(self) -> bool:
        """True si zone A (trade réel/paper) ET statut autorisant du capital."""
        return self.decision_zone == "A_TRADE"

    def is_shadow(self) -> bool:
        return self.decision_zone == "B_SHADOW"

    def size_fraction(self) -> float:
        """Taille effective = max_position_fraction × fraction live du statut."""
        return self.max_position_fraction * STATUS_SIZE_FRACTION.get(self.status, 0.0)

    # ── (dé)sérialisation ───────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = pd.Timestamp(self.timestamp).isoformat()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Opportunity":
        d = d.copy()
        d["timestamp"] = pd.Timestamp(d["timestamp"])
        d.pop("version", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    # ── pont avec SignalFrame (moteurs institutional existants) ─────────────────
    @classmethod
    def from_signal_frame(
        cls,
        sf: "SignalFrame",
        status: str,
        decision_zone: str,
        reason: str,
        correlation_bucket: str,
        regime: str = "UNKNOWN",
        expected_cost: float = 0.001,
        max_position_fraction: float = 0.25,
        p_success: Optional[float] = None,
    ) -> "Opportunity":
        """Dérive une Opportunity d'un SignalFrame produit par un moteur institutional."""
        dir_map = {"long": "LONG", "short": "SHORT_HEDGE", "flat": "CASH"}
        p = float(sf.calibrated_score if p_success is None else p_success)
        return cls(
            timestamp=sf.timestamp,
            engine_id=sf.engine_name,
            asset=sf.asset,
            direction=dir_map.get(sf.direction, "CASH"),
            status=status,
            p_success=p,
            expected_return=float(sf.expected_return),
            expected_vol=float(sf.expected_vol),
            expected_holding_hours=float(sf.max_holding_minutes) / 60.0,
            expected_cost=float(expected_cost),
            score_raw=float(sf.raw_score),
            score_net=float(sf.expected_return - expected_cost),
            confidence=float(sf.confidence),
            regime=regime,
            correlation_bucket=correlation_bucket,
            max_position_fraction=float(max_position_fraction),
            stop_loss=float(sf.stop_distance) if sf.stop_distance else None,
            take_profit=float(sf.take_profit_distance) if sf.take_profit_distance else None,
            decision_zone=decision_zone,
            reason=reason,
        )


OPPORTUNITY_COLUMNS = [
    "timestamp", "engine_id", "asset", "direction", "status",
    "p_success", "expected_return", "expected_vol", "expected_holding_hours",
    "expected_cost", "score_raw", "score_net", "confidence", "regime",
    "correlation_bucket", "max_position_fraction", "stop_loss", "take_profit",
    "decision_zone", "reason",
]
