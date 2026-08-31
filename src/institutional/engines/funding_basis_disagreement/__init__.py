"""Moteur FUNDING_BASIS_DISAGREEMENT — désaccord funding vs. basis trimestriel.

Voir configs/live_alpha_registry.yaml (alpha_id: FUNDING_BASIS_DISAGREEMENT_V1)
et reports/edge_discovery/alpha_hunt_2026-08-30/w4_calendar_basis/REPORT.md
(mécanisme M7). Mode A (SIGNAL SHADOW) uniquement -- pas d'exécution multi-leg
(aucun simulateur de mismatch de jambe n'existe encore, voir freeze_spec.json).
"""
from src.institutional.engines.funding_basis_disagreement.panel import (  # noqa: F401
    MIN_DTE, build_panel,
)
from src.institutional.engines.funding_basis_disagreement.disagreement import (  # noqa: F401
    FROZEN_HORIZON_DAYS, FROZEN_THRESHOLDS, classify_regime, select_tradeable,
)
