"""
src/institutional/engines/base.py
─────────────────────────────────────────────────────────────────────────────
Interface commune à TOUS les moteurs alpha.

Un moteur :
  - a un engine_id et un status (DISABLED..FULL_LIVE)
  - sait produire, sur une fenêtre [start, end] et un asset, une liste
    d'Opportunity (une par barre horaire : zone A/B/C, jamais de silence)
  - n'importe jamais un autre moteur

Le backtester portefeuille demande à chaque moteur ses Opportunity sur la
fenêtre de test, puis le méta-allocateur arbitre le capital.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from src.institutional.contracts import Opportunity

logger = logging.getLogger(__name__)


# Buckets de corrélation par défaut (raffinés par correlation_model.py).
DEFAULT_CORRELATION_BUCKETS: Dict[str, str] = {
    "BTCUSDT": "majors", "ETHUSDT": "majors",
    "SOLUSDT": "alts_l1", "BNBUSDT": "alts_l1", "AVAXUSDT": "alts_l1",
    "ADAUSDT": "alts_l1", "DOTUSDT": "alts_l1",
    "XRPUSDT": "alts_other", "DOGEUSDT": "alts_other", "LINKUSDT": "alts_other",
}


def correlation_bucket_for(asset: str) -> str:
    return DEFAULT_CORRELATION_BUCKETS.get(asset, "other")


@dataclass
class EngineConfig:
    """Configuration runtime commune (surchargée par les config.yaml moteur)."""
    engine_id: str
    status: str = "SHADOW"
    horizon_hours: float = 8.0
    cost_bps: float = 10.0
    assets: List[str] = field(default_factory=list)
    max_position_fraction: float = 0.25
    params: Dict = field(default_factory=dict)


class AlphaEngine(abc.ABC):
    """Classe de base de tout moteur alpha."""

    def __init__(self, config: EngineConfig):
        self.config = config

    @property
    def engine_id(self) -> str:
        return self.config.engine_id

    @property
    def status(self) -> str:
        return self.config.status

    @property
    def horizon_hours(self) -> float:
        return self.config.horizon_hours

    @property
    def assets(self) -> List[str]:
        return self.config.assets

    @property
    def cost_fraction(self) -> float:
        return self.config.cost_bps / 10000.0

    @abc.abstractmethod
    def generate(self, asset: str, start: str, end: str) -> List[Opportunity]:
        """Retourne une Opportunity par barre horaire sur [start, end]."""
        raise NotImplementedError

    def thresholds_for(self, asset: str):
        """(tau_a, tau_b) du moteur pour cet asset — par défaut, table zones globale."""
        from src.institutional.portfolio.zones import get_thresholds
        thr = get_thresholds(asset)
        return thr.tau_a, thr.tau_b

    def generate_all(self, start: str, end: str) -> List[Opportunity]:
        """Concatène les Opportunity de tous les assets du moteur."""
        out: List[Opportunity] = []
        for asset in self.assets:
            try:
                out.extend(self.generate(asset, start, end))
            except Exception as e:  # un asset KO ne casse pas le moteur
                logger.warning("[%s] generate(%s) échec : %s", self.engine_id, asset, e)
        return out

    # helper exposé aux sous-classes
    @staticmethod
    def bucket(asset: str) -> str:
        return correlation_bucket_for(asset)
