"""Moteur WHALE_LSR_SCREEN — screen positioning "whale" (top-position) LSR.

Données : data/positioning/{SYM}_top_position.parquet (Binance fapi
topLongShortPositionRatio, 5 minutes, 47 symboles, archiveur
futur-positioning.service/.timer).

CE N'EST PAS un signal directionnel tradeable : c'est un SCREEN (voir
classify_screen() ci-dessous). Le signal source est short-shaped
(sous-performance relative attendue quand le ratio whale est en extrême
haut) et SHORT est institutionnellement REJETÉ (SHORT_REJECTED, règle du
projet) -- ce module n'émet donc jamais de champ "direction"/"SHORT", ce
n'est jamais une Opportunity. Voir
reports/live_alpha_lab/WHALE_LSR_SCREEN_V1/freeze_spec.json pour le detail
complet (mécanisme, seuil figé, provenance, limites).
"""
from src.institutional.engines.whale_lsr_screen.screen import (  # noqa: F401
    MIN_PERIODS_BARS,
    ROLLING_WINDOW,
    Z_EXTREME_LONG_THRESHOLD,
    Z_EXTREME_SHORT_THRESHOLD,
    classify_screen,
    compute_rolling_zscore,
)
