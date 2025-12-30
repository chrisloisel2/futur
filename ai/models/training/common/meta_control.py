"""
Meta Control: Position sizing, leverage, and risk management.

Pipeline integration:
    Regime → Edge → MetaControl → Execution

MetaControl responsibilities:
- Regime-conditional position sizing
- Impulse event downscaling (multiplicative)
- Cooldown logic (after losses / impulse bursts)
- Leverage caps (max exposure limits)

CRITICAL: Impulse is handled as a MULTIPLIER, not a separate branch.
"""

from __future__ import annotations
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class MetaControlConfig:
    """Configuration for meta control."""
    # Regime multipliers
    regime_mult_calm: float = 1.0
    regime_mult_reversal: float = 0.7

    # Impulse downscale
    impulse_hard_mult: float = 0.3  # Hard cut when is_impulse=True
    impulse_soft_blend: bool = True  # Gradual blend based on impulse_score

    # Cooldown (after losses)
    cooldown_enabled: bool = True
    cooldown_loss_threshold: float = -0.005  # -50bps triggers cooldown
    cooldown_duration_seconds: int = 3600  # 1 hour
    cooldown_mult: float = 0.5

    # Leverage caps
    max_leverage: float = 3.0
    max_position_notional: Optional[float] = None  # e.g., 10000 USDT

    # Safety
    min_position_size: float = 0.001  # Min size in base currency


@dataclass
class MetaControlOutput:
    """Output from meta control."""
    position_size: float
    leverage: float
    regime: str
    impulse_active: bool
    impulse_score: float
    in_cooldown: bool
    multipliers: Dict[str, float]
    metadata: Dict[str, Any]


class MetaControl:
    """
    Meta-control layer for position sizing and risk management.

    Integrates:
    - Regime classifier (binary: calm/reversal)
    - Impulse detector (event-based)
    - Cooldown logic
    - Leverage caps
    """

    def __init__(
        self,
        config: Optional[MetaControlConfig] = None,
    ):
        """
        Args:
            config: MetaControlConfig instance (uses default if None)
        """
        self.config = config if config is not None else MetaControlConfig()

        # State
        self.cooldown_until: Optional[pd.Timestamp] = None
        self.last_loss_timestamp: Optional[pd.Timestamp] = None

    def compute_position_size(
        self,
        timestamp: pd.Timestamp,
        base_size: float,
        regime: str,
        impulse_score: float,
        is_impulse: bool,
        recent_pnl: Optional[float] = None,
    ) -> MetaControlOutput:
        """
        Compute position size with regime, impulse, and cooldown adjustments.

        Args:
            timestamp: Current timestamp
            base_size: Base position size (from edge/signal)
            regime: Current regime ('calm' or 'reversal')
            impulse_score: Impulse score ∈ [0, 1]
            is_impulse: Binary impulse flag
            recent_pnl: Recent PnL (for cooldown logic)

        Returns:
            MetaControlOutput with final position size and metadata
        """
        multipliers = {}

        # 1. Regime multiplier
        if regime == 'calm':
            regime_mult = self.config.regime_mult_calm
        elif regime == 'reversal':
            regime_mult = self.config.regime_mult_reversal
        else:
            logger.warning(f"Unknown regime: {regime}, defaulting to 1.0")
            regime_mult = 1.0

        multipliers['regime'] = regime_mult

        # 2. Impulse multiplier (CRITICAL: multiplicative with regime)
        if is_impulse:
            # Hard cut during impulse
            impulse_mult = self.config.impulse_hard_mult
        elif self.config.impulse_soft_blend:
            # Gradual blend: 1.0 when score=0, impulse_hard_mult when score=1
            impulse_mult = 1.0 - (1.0 - self.config.impulse_hard_mult) * impulse_score
        else:
            impulse_mult = 1.0

        multipliers['impulse'] = impulse_mult

        # 3. Cooldown (after losses)
        in_cooldown = False
        cooldown_mult = 1.0

        if self.config.cooldown_enabled and recent_pnl is not None:
            # Check if recent loss triggers cooldown
            if recent_pnl < self.config.cooldown_loss_threshold:
                self.cooldown_until = timestamp + pd.Timedelta(
                    seconds=self.config.cooldown_duration_seconds
                )
                self.last_loss_timestamp = timestamp
                logger.warning(
                    f"COOLDOWN TRIGGERED at {timestamp} (PnL={recent_pnl:.4f})"
                )

            # Check if in cooldown
            if self.cooldown_until is not None and timestamp < self.cooldown_until:
                in_cooldown = True
                cooldown_mult = self.config.cooldown_mult

        multipliers['cooldown'] = cooldown_mult

        # 4. Combine multipliers
        total_mult = regime_mult * impulse_mult * cooldown_mult
        multipliers['total'] = total_mult

        # 5. Apply to base size
        raw_size = base_size * total_mult

        # 6. Apply min/max constraints
        if raw_size < self.config.min_position_size:
            raw_size = 0.0  # Below min → no position

        if self.config.max_position_notional is not None:
            # Cap notional (requires price, but we can approximate)
            # For now, we just cap the size
            pass  # TODO: implement notional cap if needed

        final_size = raw_size

        # 7. Leverage (simple: regime-based)
        if regime == 'calm':
            leverage = min(self.config.max_leverage, 2.0)
        elif regime == 'reversal':
            leverage = min(self.config.max_leverage, 1.5)
        else:
            leverage = 1.0

        # Reduce leverage during impulse
        if is_impulse:
            leverage = min(leverage, 1.0)

        return MetaControlOutput(
            position_size=final_size,
            leverage=leverage,
            regime=regime,
            impulse_active=is_impulse,
            impulse_score=impulse_score,
            in_cooldown=in_cooldown,
            multipliers=multipliers,
            metadata={
                'base_size': base_size,
                'timestamp': timestamp,
                'cooldown_until': self.cooldown_until,
            },
        )

    def reset_cooldown(self):
        """Manually reset cooldown (e.g., after successful recovery)."""
        self.cooldown_until = None
        logger.info("Cooldown reset")

    def __repr__(self) -> str:
        return f"MetaControl(config={self.config})"


# Example usage
if __name__ == "__main__":
    from impulse_detector import ImpulseDetector

    # Setup
    meta_control = MetaControl()
    impulse_detector = ImpulseDetector(threshold=0.7)

    # Example: calm regime, no impulse
    timestamp = pd.Timestamp.now()
    output = meta_control.compute_position_size(
        timestamp=timestamp,
        base_size=1.0,
        regime='calm',
        impulse_score=0.2,
        is_impulse=False,
        recent_pnl=0.001,
    )
    print("Calm, no impulse:")
    print(f"  Size: {output.position_size:.3f}")
    print(f"  Multipliers: {output.multipliers}")
    print()

    # Example: reversal regime, impulse detected
    output = meta_control.compute_position_size(
        timestamp=timestamp,
        base_size=1.0,
        regime='reversal',
        impulse_score=0.85,
        is_impulse=True,
        recent_pnl=0.001,
    )
    print("Reversal + impulse:")
    print(f"  Size: {output.position_size:.3f} (regime_mult=0.7, impulse_mult=0.3)")
    print(f"  Multipliers: {output.multipliers}")
    print(f"  Total mult: {output.multipliers['total']:.3f}")
    print()

    # Example: cooldown triggered
    output = meta_control.compute_position_size(
        timestamp=timestamp,
        base_size=1.0,
        regime='calm',
        impulse_score=0.1,
        is_impulse=False,
        recent_pnl=-0.010,  # -100bps loss
    )
    print("Cooldown triggered (loss):")
    print(f"  Size: {output.position_size:.3f}")
    print(f"  In cooldown: {output.in_cooldown}")
    print(f"  Multipliers: {output.multipliers}")
