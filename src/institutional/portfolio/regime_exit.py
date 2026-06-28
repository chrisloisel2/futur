"""
src/institutional/portfolio/regime_exit.py
─────────────────────────────────────────────────────────────────────────────
Forced exit on regime flip (Phase 43) — logique ALPHA.

Bloquer les nouvelles entrées ne suffit pas : une position LONG ouverte en fin
de BULL doit SORTIR dès que le régime quitte {BULL, RECOVERY}. Le carry reste
indépendant (géré par le funding gate). Le hedge lié à un long fermé est fermé.
"""
from __future__ import annotations

from dataclasses import dataclass

ALLOWED_LONG_REGIMES = {"BULL", "RECOVERY"}
# HYSTÉRÉSIS : on ENTRE en BULL/RECOVERY mais on ne SORT que sur régime
# franchement hostile. Sortir aussi sur NEUTRAL provoque un whipsaw
# (BULL↔NEUTRAL horaire) qui détruit l'alpha (mesuré : B1 −22.6% vs B0 −4.6%).
# NEUTRAL = on tient ; c'est le DD governor intra-position qui protège.
HOSTILE_EXIT_REGIMES = {"BEAR", "CRASH", "UNKNOWN"}

EXIT_REASONS = (
    "REGIME_FLIP_EXIT", "DRAWDOWN_GOVERNOR_EXIT",
    "STOP_LOSS_EXIT", "TAKE_PROFIT_EXIT", "TIME_EXIT",
)


@dataclass
class RegimeExitDecision:
    timestamp: str
    position_id: str
    asset: str
    old_regime: str
    new_regime: str
    should_exit: bool
    reason: str


def should_exit_long_on_regime_flip(position_type: str, current_regime: str) -> bool:
    """True si une position LONG doit sortir (régime franchement hostile only)."""
    if position_type != "DIRECTIONAL_LONG":
        return False
    return current_regime in HOSTILE_EXIT_REGIMES
