"""
src/institutional/portfolio/constraints.py
─────────────────────────────────────────────────────────────────────────────
Contraintes d'exposition portefeuille (cf. brief Étape 5).

Nouvelle logique : "je prends plusieurs trades si leur risque marginal est
accepté par le portefeuille" — plus "je ne prends qu'un trade pour éviter le
risque".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.institutional.contracts import ReasonCode


@dataclass
class PortfolioConstraints:
    max_open_positions: int = 4
    max_positions_per_asset: int = 1
    max_positions_per_bucket: int = 2
    max_gross_exposure: float = 0.75
    max_single_asset_exposure: float = 0.25
    max_single_engine_exposure: float = 0.35

    def check(
        self,
        *,
        asset: str,
        engine_id: str,
        bucket: str,
        n_open: int,
        open_assets: set,
        bucket_count: Dict[str, int],
        engine_exposure: Dict[str, float],
        gross_exposure: float,
    ) -> Tuple[bool, Optional[ReasonCode]]:
        """Retourne (autorisé, reason_si_refus)."""
        if n_open >= self.max_open_positions:
            return False, ReasonCode.REJECT_EXPOSURE_LIMIT
        if self.max_positions_per_asset and asset in open_assets:
            return False, ReasonCode.REJECT_EXPOSURE_LIMIT
        if bucket_count.get(bucket, 0) >= self.max_positions_per_bucket:
            return False, ReasonCode.REJECT_CORRELATION
        if engine_exposure.get(engine_id, 0.0) >= self.max_single_engine_exposure:
            return False, ReasonCode.REJECT_EXPOSURE_LIMIT
        if gross_exposure >= self.max_gross_exposure:
            return False, ReasonCode.REJECT_EXPOSURE_LIMIT
        return True, None

    def headroom(
        self,
        *,
        engine_id: str,
        bucket: str,
        engine_exposure: Dict[str, float],
        bucket_exposure: Dict[str, float],
        gross_exposure: float,
    ) -> float:
        """Fraction d'equity encore allouable compte tenu de tous les caps."""
        return max(0.0, min(
            self.max_single_asset_exposure,
            self.max_single_engine_exposure - engine_exposure.get(engine_id, 0.0),
            self.max_gross_exposure - gross_exposure,
        ))
