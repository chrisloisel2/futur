"""
src/institutional/engines/registry.py
─────────────────────────────────────────────────────────────────────────────
Registre central des moteurs alpha — instancie un moteur par id, ou la flotte
par défaut. Les statuts par défaut respectent la règle "tout part SHADOW/PAPER".
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List

from src.institutional.engines.base import AlphaEngine

logger = logging.getLogger(__name__)


def _trm_trend_long(**kw) -> AlphaEngine:
    from src.institutional.engines.trm_trend_long import TRMTrendLongEngine
    return TRMTrendLongEngine(**kw)


def _trm_trend_inst(**kw) -> AlphaEngine:
    from src.institutional.engines.trm_trend_inst import TRMTrendInstEngine
    return TRMTrendInstEngine(**kw)


def _pullback_long(**kw) -> AlphaEngine:
    from src.institutional.engines.pullback_long import PullbackLongEngine
    return PullbackLongEngine(**kw)


def _liquidation_rebound(**kw) -> AlphaEngine:
    from src.institutional.engines.liquidation_rebound import LiquidationReboundEngine
    return LiquidationReboundEngine(**kw)


def _carry_basis(**kw) -> AlphaEngine:
    from src.institutional.engines.carry_basis import CarryBasisEngine
    return CarryBasisEngine(**kw)


def _cross_sectional(**kw) -> AlphaEngine:
    from src.institutional.engines.cross_sectional_long import CrossSectionalLongEngine
    return CrossSectionalLongEngine(**kw)


ENGINE_FACTORIES: Dict[str, Callable[..., AlphaEngine]] = {
    "TRM_TREND_LONG": _trm_trend_long,
    "TRM_TREND_INST": _trm_trend_inst,
    "PULLBACK_LONG": _pullback_long,
    "LIQUIDATION_REBOUND": _liquidation_rebound,
    "CARRY_BASIS": _carry_basis,
    "CROSS_SECTIONAL_LONG": _cross_sectional,
}


def build_engine(engine_id: str, **kwargs) -> AlphaEngine:
    if engine_id not in ENGINE_FACTORIES:
        raise KeyError(f"Moteur inconnu: {engine_id!r}. Options: {list(ENGINE_FACTORIES)}")
    return ENGINE_FACTORIES[engine_id](**kwargs)


def available_engines() -> List[str]:
    """Retourne les moteurs réellement instanciables (import OK)."""
    ok = []
    for eid, factory in ENGINE_FACTORIES.items():
        try:
            factory()
            ok.append(eid)
        except Exception as e:  # moteur pas encore implémenté/entraîné
            logger.debug("moteur %s indisponible: %s", eid, e)
    return ok
