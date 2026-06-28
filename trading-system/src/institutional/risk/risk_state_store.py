"""
trading-system/src/institutional/risk/risk_state_store.py
═══════════════════════════════════════════════════════════════════════════════
Contrats RiskState et RiskStateStore.

RiskState   : état de risque mutable et persistant.
RiskStateStore : couche de persistance avec écritures atomiques.

Importe depuis : institutional.contracts (Verdict, EngineID — jamais SignalFrame)
N'importe PAS depuis : portfolio, data, experiments.

Invariants RiskState :
    peak_equity ≥ 0
    realized_drawdown ≤ 0      (négatif ou nul)
    0 ≤ total_wins ≤ total_trades
    consecutive_losses ≥ 0
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Self

from institutional.contracts import Verdict


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════


class KillReason(StrEnum):
    NONE           = "none"
    MAX_DRAWDOWN   = "max_drawdown"
    DAILY_LOSS     = "daily_loss"
    WEEKLY_LOSS    = "weekly_loss"
    CONSECUTIVE    = "consecutive_losses"
    STALE_DATA     = "stale_data"
    MANUAL         = "manual"
    VAR_BREACH     = "var_breach"


# ══════════════════════════════════════════════════════════════════════════════
# RiskState
# ══════════════════════════════════════════════════════════════════════════════


_STATE_VERSION = "1.1.0"


@dataclass(slots=True)
class RiskState:
    """
    État de risque persistant du Risk Engine.

    Mis à jour après chaque trade et sauvegardé sur disque par RiskStateStore.
    Chargé au démarrage du système pour reprendre sans perte d'historique.

    Champs clés :
        day_pnl / week_pnl / month_pnl : PnL cumulé par période
        realized_drawdown : (equity - peak_equity) / peak_equity ≤ 0
        kill_switch_active / kill_reason : état du kill switch
        cooldown_until : timestamp jusqu'où les trades sont bloqués
    """

    # ── Timestamps ────────────────────────────────────────────────────────────
    timestamp:           datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # ── PnL par période ───────────────────────────────────────────────────────
    day_pnl:             float = 0.0
    week_pnl:            float = 0.0
    month_pnl:           float = 0.0

    # ── Equity / Drawdown ─────────────────────────────────────────────────────
    peak_equity:         float = 0.0
    unrealized_pnl:      float = 0.0
    realized_drawdown:   float = 0.0   # ≤ 0

    # ── Exposition ────────────────────────────────────────────────────────────
    gross_exposure:      float = 0.0
    net_exposure:        float = 0.0
    per_asset_exposure:  dict[str, float] = field(default_factory=dict)
    per_engine_exposure: dict[str, float] = field(default_factory=dict)

    # ── Séquences ─────────────────────────────────────────────────────────────
    consecutive_losses:  int   = 0
    consecutive_wins:    int   = 0
    total_trades:        int   = 0
    total_wins:          int   = 0
    total_losses:        int   = 0

    # ── Kill switch ───────────────────────────────────────────────────────────
    kill_switch_active:  bool        = False
    kill_reason:         KillReason  = KillReason.NONE
    cooldown_until:      datetime | None = None

    # ── Dernier trade ─────────────────────────────────────────────────────────
    last_trade_timestamp: datetime | None = None

    # ── Version ───────────────────────────────────────────────────────────────
    version: str = field(default=_STATE_VERSION, init=False)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def record_trade(self, *, pnl: float, timestamp: datetime) -> None:
        """
        Enregistre le résultat d'un trade fermé.
        Met à jour les PnL, les séquences et le timestamp.
        """
        self.total_trades       += 1
        self.day_pnl            += pnl
        self.week_pnl           += pnl
        self.month_pnl          += pnl
        self.last_trade_timestamp = timestamp
        self.timestamp           = timestamp

        if pnl > 0.0:
            self.total_wins       += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.total_losses        += 1
            self.consecutive_losses  += 1
            self.consecutive_wins     = 0

    def update_drawdown(self, current_equity: float) -> None:
        """Recalcule le drawdown depuis le peak. current_equity > 0 requis."""
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        self.realized_drawdown = (
            (current_equity - self.peak_equity) / self.peak_equity
            if self.peak_equity > 0
            else 0.0
        )

    def reset_daily(self, timestamp: datetime) -> None:
        self.day_pnl = 0.0
        self.timestamp = timestamp

    def reset_weekly(self, timestamp: datetime) -> None:
        self.week_pnl = 0.0
        self.timestamp = timestamp

    def reset_monthly(self, timestamp: datetime) -> None:
        self.month_pnl = 0.0
        self.timestamp = timestamp

    def activate_kill_switch(
        self,
        reason: KillReason,
        *,
        cooldown_until: datetime | None = None,
    ) -> None:
        self.kill_switch_active = True
        self.kill_reason        = reason
        self.cooldown_until     = cooldown_until

    def deactivate_kill_switch(self) -> None:
        self.kill_switch_active = False
        self.kill_reason        = KillReason.NONE
        self.cooldown_until     = None

    # ── Propriétés calculées ──────────────────────────────────────────────────

    @property
    def win_rate(self) -> float:
        return self.total_wins / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def is_in_cooldown(self) -> bool:
        if self.cooldown_until is None:
            return False
        return datetime.now(timezone.utc) < self.cooldown_until

    # ── Sérialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, object]:
        return {
            "version":              self.version,
            "timestamp":            self.timestamp.isoformat(),
            "day_pnl":              self.day_pnl,
            "week_pnl":             self.week_pnl,
            "month_pnl":            self.month_pnl,
            "peak_equity":          self.peak_equity,
            "unrealized_pnl":       self.unrealized_pnl,
            "realized_drawdown":    self.realized_drawdown,
            "gross_exposure":       self.gross_exposure,
            "net_exposure":         self.net_exposure,
            "per_asset_exposure":   self.per_asset_exposure,
            "per_engine_exposure":  self.per_engine_exposure,
            "consecutive_losses":   self.consecutive_losses,
            "consecutive_wins":     self.consecutive_wins,
            "total_trades":         self.total_trades,
            "total_wins":           self.total_wins,
            "total_losses":         self.total_losses,
            "kill_switch_active":   self.kill_switch_active,
            "kill_reason":          str(self.kill_reason),
            "cooldown_until":       (
                self.cooldown_until.isoformat() if self.cooldown_until else None
            ),
            "last_trade_timestamp": (
                self.last_trade_timestamp.isoformat()
                if self.last_trade_timestamp else None
            ),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Self:
        state = cls()
        state.timestamp          = datetime.fromisoformat(str(data["timestamp"]))
        state.day_pnl            = float(str(data["day_pnl"]))
        state.week_pnl           = float(str(data["week_pnl"]))
        state.month_pnl          = float(str(data["month_pnl"]))
        state.peak_equity        = float(str(data["peak_equity"]))
        state.unrealized_pnl     = float(str(data["unrealized_pnl"]))
        state.realized_drawdown  = float(str(data["realized_drawdown"]))
        state.gross_exposure     = float(str(data["gross_exposure"]))
        state.net_exposure       = float(str(data["net_exposure"]))
        state.per_asset_exposure = dict(data.get("per_asset_exposure") or {})  # type: ignore[arg-type]
        state.per_engine_exposure = dict(data.get("per_engine_exposure") or {})  # type: ignore[arg-type]
        state.consecutive_losses = int(str(data["consecutive_losses"]))
        state.consecutive_wins   = int(str(data["consecutive_wins"]))
        state.total_trades       = int(str(data["total_trades"]))
        state.total_wins         = int(str(data["total_wins"]))
        state.total_losses       = int(str(data["total_losses"]))
        state.kill_switch_active = bool(data["kill_switch_active"])
        state.kill_reason        = KillReason(str(data["kill_reason"]))
        state.cooldown_until     = (
            datetime.fromisoformat(str(data["cooldown_until"]))
            if data.get("cooldown_until") else None
        )
        state.last_trade_timestamp = (
            datetime.fromisoformat(str(data["last_trade_timestamp"]))
            if data.get("last_trade_timestamp") else None
        )
        return state

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(json.loads(raw))


# ══════════════════════════════════════════════════════════════════════════════
# RiskStateStore
# ══════════════════════════════════════════════════════════════════════════════


class RiskStateStore:
    """
    Couche de persistance pour RiskState.

    Utilise des écritures atomiques (write-to-temp + rename) pour éviter
    les fichiers corrompus en cas d'interruption.

    Usage :
        store = RiskStateStore(path=Path("state/risk_state.json"))
        state = store.load()          # charge ou crée un état vide
        state.record_trade(...)
        store.save(state)             # écriture atomique
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> RiskState:
        """
        Charge l'état depuis le fichier JSON.
        Retourne un RiskState vide si le fichier n'existe pas.
        Lève ValueError si le fichier est corrompu.
        """
        if not self._path.exists():
            return RiskState()

        raw = self._path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"RiskStateStore : fichier corrompu {self._path} → {exc}"
            ) from exc

        return RiskState.from_dict(data)

    def save(self, state: RiskState) -> None:
        """
        Sauvegarde atomique : write-to-temp + os.replace.
        Garanti cohérent même en cas de crash durant l'écriture.
        """
        payload = state.to_json()
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".risk_state_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def reset(self) -> RiskState:
        """Remet à zéro l'état (crée un nouveau RiskState vide et le sauvegarde)."""
        state = RiskState()
        self.save(state)
        return state
