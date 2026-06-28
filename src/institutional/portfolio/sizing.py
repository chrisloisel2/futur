"""
src/institutional/portfolio/sizing.py
─────────────────────────────────────────────────────────────────────────────
Sizing multi-cap (cf. brief Étape 5).

On ne dimensionne plus uniquement par Kelly offline. La taille finale est le
MIN de tous les plafonds, avec un Kelly fractionnaire fortement shrinké par la
confiance live (peu de trades live → petite taille) et par le régime.

    f_used = min(0.25, 0.33 · f_kelly · confidence_shrink · regime_shrink)
    confidence_shrink = min(1, sqrt(N_live / 100))
    regime_shrink ∈ {1.0 validé, 0.5 peu observé, 0.0 interdit}

    size = min(engine_cap, asset_cap, correlation_cap, drawdown_cap,
               vol_target_cap, fractional_kelly_cap)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SizingCaps:
    engine_cap: float = 0.35
    asset_cap: float = 0.25
    correlation_bucket_cap: float = 0.40
    gross_cap: float = 0.75
    max_kelly: float = 0.25
    kelly_multiplier: float = 0.33


def confidence_shrink(n_live: int) -> float:
    """min(1, sqrt(N_live/100)) — 3 trades live → 0.17 (taille très réduite)."""
    return min(1.0, math.sqrt(max(n_live, 0) / 100.0))


def regime_shrink(regime_state: str) -> float:
    """1.0 régime validé, 0.5 peu observé, 0.0 interdit."""
    return {"validated": 1.0, "rare": 0.5, "forbidden": 0.0}.get(regime_state, 0.5)


def fractional_kelly(
    f_kelly: float,
    n_live: int,
    regime_state: str = "validated",
    caps: SizingCaps = SizingCaps(),
) -> float:
    f = caps.kelly_multiplier * max(0.0, f_kelly) * confidence_shrink(n_live) * regime_shrink(regime_state)
    return min(caps.max_kelly, f)


def multi_cap_size(
    *,
    f_kelly: float,
    n_live: int,
    regime_state: str,
    vol_target_cap: float,
    drawdown_cap: float,
    engine_exposure: float,
    bucket_exposure: float,
    gross_exposure: float,
    caps: SizingCaps = SizingCaps(),
) -> float:
    """Taille finale = min de tous les plafonds. Toujours ≥ 0."""
    f_kelly_cap = fractional_kelly(f_kelly, n_live, regime_state, caps)
    size = min(
        caps.engine_cap - engine_exposure,
        caps.asset_cap,
        caps.correlation_bucket_cap - bucket_exposure,
        caps.gross_cap - gross_exposure,
        max(0.0, vol_target_cap),
        max(0.0, drawdown_cap),
        f_kelly_cap,
    )
    return max(0.0, float(size))


def kelly_from_stats(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Kelly complet f = (p·b − q)/b, b = avg_win/avg_loss. Borné [0,1]."""
    if avg_loss <= 0:
        return 0.0
    b = avg_win / avg_loss
    if b <= 0:
        return 0.0
    q = 1.0 - win_rate
    return max(0.0, min(1.0, (win_rate * b - q) / b))
