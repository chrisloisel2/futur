from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from domain.risk.scenario_result import ScenarioResult
from domain.state.risk_state import BookRiskSnapshot, RiskState
from domain.state.targets import TargetPosition, TargetPositions
from pipeline.risk.exposure import ExposureEngine
from pipeline.risk.correlation import CorrelationModel
from pipeline.risk.var_cvar import VaREngine, fractional_kelly
from pipeline.risk.scenario_engine import ScenarioEngine
from pipeline.risk.order_builder import OrdersPlanBuilder


@dataclass
class RiskControllerConfig:
    var_method: str = "parametric"
    alpha: float = 0.95


class RiskController:
    def __init__(self, config: Dict):
        self.config = RiskControllerConfig(**config.get("controller", {}))
        self.exposure_engine = ExposureEngine()
        self.var_engine = VaREngine(self.config.var_method)
        self.scenario_engine = ScenarioEngine(config.get("scenarios", {}))
        self.order_builder = OrdersPlanBuilder()
        self.corr_model = CorrelationModel(config.get("clusters", {}))
        self.kelly_cfg = config.get("kelly", {})
        self.killswitch_cfg = config.get("killswitch", {})

    def step(
        self,
        target_positions: TargetPositions,
        portfolio_state: Dict,
        books_state: Dict,
        states_by_symbol: Dict[str, pd.Series],
        configs: Dict,
    ) -> tuple[object, object]:
        targets = target_positions.targets
        exposures = self.exposure_engine.compute(targets, configs.get("clusters", {}))
        returns = pd.Series([s.get("rv_fwd_q50", 0) if isinstance(s, dict) else 0 for s in states_by_symbol.values()])
        var, cvar = self.var_engine.compute(returns, alpha=self.config.alpha)
        book_risk = {}
        for book in {t.book for t in targets}:
            book_targets = [t for t in targets if t.book == book]
            book_var, book_cvar = self.var_engine.compute(pd.Series([t.expected_utility for t in book_targets]), alpha=self.config.alpha)
            book_risk[book] = BookRiskSnapshot(
                var95_usd=book_var,
                cvar95_usd=book_cvar,
                gross_exposure_usd=sum(abs(t.notional_usd) for t in book_targets),
                net_exposure_usd=sum(t.notional_usd if t.side == "LONG" else -t.notional_usd for t in book_targets),
                dd=books_state.get(book, {}).get("dd", 0.0) if isinstance(books_state, dict) else 0.0,
                kelly_multiplier=fractional_kelly(0.5, 1.0, cap=self.kelly_cfg.get("kelly_cap", 0.5), shrink=self.kelly_cfg.get("shrinkage", 0.5)),
                downscale_factor=1.0,
            )
        scenario_results: List[ScenarioResult] = self.scenario_engine.run(targets, portfolio_state or {})
        killswitch_active = self._killswitch(portfolio_state)
        risk_state = RiskState(
            event_time=pd.Timestamp.utcnow(),
            killswitch_active=killswitch_active,
            risk_off_mode=False,
            portfolio_var95_usd=var,
            portfolio_cvar95_usd=cvar,
            portfolio_leverage=float(portfolio_state.get("leverage", 0)) if portfolio_state else 0.0,
            cluster_exposure_usd=exposures.cluster_exposure,
            book_risk=book_risk,
            scenario_results=scenario_results,
            caps_applied=[],
            actions_taken=[],
            reasons=[],
            run_id=getattr(target_positions, "run_id", None),
        )
        orders_plan = self.order_builder.build(targets, portfolio_state.get("positions", {}) if portfolio_state else {}, run_id=risk_state.run_id or "", risk_state_ref="portfolio")
        return risk_state, orders_plan

    def _killswitch(self, portfolio_state: Dict) -> bool:
        """
        Enhanced killswitch with strict conservative defaults.

        FIXED: Changed defaults from 100% DD and infinite daily loss to:
        - Max drawdown: 10% (was 100%)
        - Daily loss: 2% of capital (was infinite)
        - Max consecutive losses: 3 trades (new)
        - Max hourly loss: 1% of capital (new)

        Returns True if any condition breached → stops all trading
        """
        if not portfolio_state:
            return False

        # Get current portfolio metrics
        capital = float(portfolio_state.get("capital", 10_000))  # Default 10k
        dd = abs(float(portfolio_state.get("drawdown", 0)))
        daily_loss = abs(float(portfolio_state.get("daily_loss", 0)))
        hourly_loss = abs(float(portfolio_state.get("hourly_loss", 0)))
        consecutive_losses = int(portfolio_state.get("consecutive_losses", 0))

        # Killswitch thresholds (CONSERVATIVE)
        max_dd_pct = self.killswitch_cfg.get("max_drawdown_pct", 10.0)  # 10% max DD
        daily_loss_limit_pct = self.killswitch_cfg.get("daily_loss_limit_pct", 2.0)  # 2% daily
        hourly_loss_limit_pct = self.killswitch_cfg.get("hourly_loss_limit_pct", 1.0)  # 1% hourly
        max_consecutive_losses = self.killswitch_cfg.get("max_consecutive_losses", 3)  # 3 losses

        # Convert percentage limits to USD
        daily_loss_limit_usd = capital * (daily_loss_limit_pct / 100)
        hourly_loss_limit_usd = capital * (hourly_loss_limit_pct / 100)

        # Check each condition
        conditions = [
            (dd >= max_dd_pct / 100, f"drawdown_{dd*100:.1f}%_exceeds_{max_dd_pct}%"),
            (daily_loss >= daily_loss_limit_usd, f"daily_loss_${daily_loss:.0f}_exceeds_${daily_loss_limit_usd:.0f}"),
            (hourly_loss >= hourly_loss_limit_usd, f"hourly_loss_${hourly_loss:.0f}_exceeds_${hourly_loss_limit_usd:.0f}"),
            (consecutive_losses >= max_consecutive_losses, f"consecutive_losses_{consecutive_losses}_exceeds_{max_consecutive_losses}"),
        ]

        # Trigger killswitch if any condition met
        for triggered, reason in conditions:
            if triggered:
                from common.logging.setup import get_logger
                logger = get_logger(__name__)
                logger.critical({
                    "msg": "KILLSWITCH ACTIVATED",
                    "reason": reason,
                    "drawdown_pct": dd * 100,
                    "daily_loss_usd": daily_loss,
                    "hourly_loss_usd": hourly_loss,
                    "consecutive_losses": consecutive_losses,
                })
                return True

        return False
