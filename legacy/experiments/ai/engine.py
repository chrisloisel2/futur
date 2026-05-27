from __future__ import annotations

from dataclasses import asdict
from typing import Any

from core.settings import configure_project_imports


configure_project_imports()

from level_7.RiskController import RiskConfig, RiskController, RiskState  # noqa: E402


def build_legacy_risk_config(
    *,
    equity: float,
    risk_per_trade: float,
    cooldown_bars: int,
    daily_loss_limit_pct: float,
    max_consecutive_losses: int,
    rr: float = 1.5,
    atr_key: str = "atr_14",
    rv_key: str = "rv_24",
    stop_atr_mult: float = 2.0,
) -> RiskConfig:
    return RiskConfig(
        equity=equity,
        risk_per_trade=risk_per_trade,
        rr=rr,
        cooldown_bars=cooldown_bars,
        daily_loss_limit_pct=daily_loss_limit_pct,
        max_consecutive_losses=max_consecutive_losses,
        atr_key=atr_key,
        rv_key=rv_key,
        stop_atr_mult=stop_atr_mult,
    )


class CanonicalRiskEngine:
    def __init__(self, config: RiskConfig, state: RiskState | None = None):
        self.config = config
        self.controller = RiskController(cfg=config, state=state)

    @classmethod
    def from_pipeline_side(cls, cfg: Any, side: str) -> "CanonicalRiskEngine":
        if side == "long":
            config = build_legacy_risk_config(
                equity=cfg.initial_equity,
                risk_per_trade=cfg.risk_per_trade_long,
                cooldown_bars=cfg.cooldown_bars_long,
                daily_loss_limit_pct=cfg.daily_loss_limit_pct,
                max_consecutive_losses=cfg.max_consecutive_losses_long,
            )
        elif side == "short":
            config = build_legacy_risk_config(
                equity=cfg.initial_equity,
                risk_per_trade=cfg.risk_per_trade_short,
                cooldown_bars=cfg.cooldown_bars_short,
                daily_loss_limit_pct=cfg.daily_loss_limit_pct,
                max_consecutive_losses=cfg.max_consecutive_losses_short,
            )
        else:
            raise ValueError(f"side doit être 'long' ou 'short', reçu {side!r}")
        return cls(config=config)

    def to_dict(self) -> dict[str, object]:
        return asdict(self.config)

