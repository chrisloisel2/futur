"""
src/institutional/risk/risk_engine.py
─────────────────────────────────────────────────────────────────────────────
Risk Engine institutionnel.

Appelé avant CHAQUE ordre en backtest, paper trading et live.
Retourne une décision ALLOW/BLOCK avec raison détaillée.

Persistent state via RiskState (contracts.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.institutional.contracts import (
    PortfolioState, RiskState, SignalFrame,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    # Limites journalières
    max_day_loss_pct: float = 0.03        # -3% equity/jour → halt
    # Limites hebdomadaires
    max_week_loss_pct: float = 0.07       # -7% equity/semaine → halt
    # Drawdown
    max_drawdown_pct: float = 0.10        # -10% depuis peak → réduction
    kill_drawdown_pct: float = 0.15       # -15% depuis peak → kill switch

    # Exposition
    max_gross_exposure_pct: float = 1.00  # 100% equity max
    max_net_exposure_pct: float = 1.00
    max_single_asset_pct: float = 0.40    # 40% max sur un seul actif
    max_single_engine_pct: float = 0.60   # 60% max pour un engine

    # Pertes consécutives
    max_consecutive_losses: int = 5       # 5 pertes → cooldown
    cooldown_bars_after_losses: int = 24  # 24h de cooldown

    # Données
    max_stale_data_minutes: float = 120.0

    # Kill switch
    kill_switch_manual: bool = False      # override manuel


@dataclass
class RiskDecision:
    allow: bool
    reason: str
    size_multiplier: float = 1.0          # 0.5 = réduire taille de 50%
    blocked_reasons: List[str] = None

    def __post_init__(self):
        if self.blocked_reasons is None:
            self.blocked_reasons = []

    @classmethod
    def block(cls, reason: str) -> "RiskDecision":
        return cls(allow=False, reason=reason, size_multiplier=0.0, blocked_reasons=[reason])

    @classmethod
    def reduce(cls, reason: str, mult: float) -> "RiskDecision":
        return cls(allow=True, reason=reason, size_multiplier=mult, blocked_reasons=[reason])

    @classmethod
    def ok(cls) -> "RiskDecision":
        return cls(allow=True, reason="all_clear", size_multiplier=1.0)


class RiskEngine:
    """
    Moteur de risque institutionnel.

    Usage
    -----
    engine = RiskEngine(config, state_path=Path("state/risk_state.json"))
    decision = engine.check(signal, portfolio_state, current_timestamp)
    if decision.allow:
        size = compute_size(signal) * decision.size_multiplier
        execute(size)
    """

    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        state_path: Optional[Path] = None,
    ):
        self.config = config or RiskConfig()
        self.state_path = state_path or Path("state/institutional_risk_state.json")
        self._state = RiskState.load(self.state_path)
        logger.info(f"[RiskEngine] Loaded state: {self._state.to_dict()}")

    @property
    def state(self) -> RiskState:
        return self._state

    def check(
        self,
        signal: SignalFrame,
        portfolio: PortfolioState,
        timestamp: pd.Timestamp,
        equity: float,
        data_freshness_minutes: float = 0.0,
    ) -> RiskDecision:
        """
        Vérifie toutes les conditions de risque avant un ordre.
        Retourne une RiskDecision (allow/block/reduce).
        """
        cfg = self.config
        reasons = []

        # 1. Kill switch manuel
        if cfg.kill_switch_manual or self._state.kill_switch_active:
            return RiskDecision.block(
                f"kill_switch_active: {self._state.kill_switch_reason}"
            )

        # 2. Cooldown
        if self._state.cooldown_until and timestamp < self._state.cooldown_until:
            return RiskDecision.block(
                f"cooldown until {self._state.cooldown_until}"
            )

        # 3. Perte journalière
        if equity > 0:
            day_loss_pct = self._state.day_pnl / equity
            if day_loss_pct < -cfg.max_day_loss_pct:
                return RiskDecision.block(
                    f"day_loss {day_loss_pct:.2%} > limit {cfg.max_day_loss_pct:.2%}"
                )

        # 4. Perte hebdomadaire
        if equity > 0 and self._state.week_pnl / equity < -cfg.max_week_loss_pct:
            return RiskDecision.block(
                f"week_loss > {cfg.max_week_loss_pct:.2%}"
            )

        # 5. Drawdown
        if self._state.peak_equity > 0:
            dd = (equity - self._state.peak_equity) / self._state.peak_equity
            if dd < -cfg.kill_drawdown_pct:
                self._state.kill_switch_active = True
                self._state.kill_switch_reason = f"max_drawdown {dd:.2%}"
                self.save_state()
                return RiskDecision.block(f"kill_switch: max_drawdown {dd:.2%}")
            elif dd < -cfg.max_drawdown_pct:
                reasons.append(f"drawdown_warning {dd:.2%}")
                return RiskDecision.reduce(f"drawdown {dd:.2%}", mult=0.50)

        # 6. Pertes consécutives
        if self._state.consecutive_losses >= cfg.max_consecutive_losses:
            cooldown_until = timestamp + pd.Timedelta(hours=cfg.cooldown_bars_after_losses)
            self._state.cooldown_until = cooldown_until
            self.save_state()
            return RiskDecision.block(
                f"consecutive_losses={self._state.consecutive_losses} >= {cfg.max_consecutive_losses}"
            )

        # 7. Exposition totale
        if portfolio.gross_exposure / max(equity, 1) > cfg.max_gross_exposure_pct:
            return RiskDecision.block(
                f"gross_exposure {portfolio.gross_exposure/equity:.2%} > "
                f"{cfg.max_gross_exposure_pct:.2%}"
            )

        # 8. Exposition par actif
        asset_exp = portfolio.per_asset_exposure()
        if signal.asset in asset_exp:
            asset_pct = asset_exp[signal.asset] / max(equity, 1)
            if asset_pct > cfg.max_single_asset_pct:
                return RiskDecision.block(
                    f"asset_exposure {signal.asset} {asset_pct:.2%} > "
                    f"{cfg.max_single_asset_pct:.2%}"
                )

        # 9. Exposition par engine
        engine_exp = portfolio.per_engine_exposure()
        if signal.engine_name in engine_exp:
            engine_pct = engine_exp[signal.engine_name] / max(equity, 1)
            if engine_pct > cfg.max_single_engine_pct:
                return RiskDecision.block(
                    f"engine_exposure {signal.engine_name} {engine_pct:.2%} > "
                    f"{cfg.max_single_engine_pct:.2%}"
                )

        # 10. Fraîcheur des données
        if data_freshness_minutes > cfg.max_stale_data_minutes:
            return RiskDecision.block(
                f"stale_data {data_freshness_minutes:.0f}min > "
                f"{cfg.max_stale_data_minutes:.0f}min"
            )

        if reasons:
            return RiskDecision(allow=True, reason="; ".join(reasons), size_multiplier=0.75)

        return RiskDecision.ok()

    def record_trade_result(
        self,
        pnl: float,
        timestamp: pd.Timestamp,
        current_equity: float,
    ) -> None:
        """Enregistre le résultat d'un trade et met à jour le state."""
        self._state.record_trade(pnl, timestamp)
        self._state.update_drawdown(current_equity)
        self._state.timestamp = timestamp
        self.save_state()

    def reset_daily(self, timestamp: pd.Timestamp) -> None:
        """Reset des métriques journalières (appelé au début de chaque jour)."""
        self._state.day_pnl = 0.0
        self._state.timestamp = timestamp
        self.save_state()

    def reset_weekly(self, timestamp: pd.Timestamp) -> None:
        self._state.week_pnl = 0.0
        self._state.timestamp = timestamp
        self.save_state()

    def save_state(self) -> None:
        self._state.save(self.state_path)

    def get_summary(self) -> Dict[str, Any]:
        return self._state.to_dict()
