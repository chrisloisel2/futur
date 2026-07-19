"""CROWDING_REVERSAL — capitulation de POSITIONNEMENT (état de foule, 24h).

Mécanisme distinct de LIQ_CASCADE (event 30-min) : ici on détecte l'état
accumulé — top-traders qui capitulent (ratio z bas) + OI purgé sur 24h —
et on joue le rebond contrarian à horizon 24h. Données : metrics 5-min Vision.
"""
from src.institutional.engines.crowding_reversal.detector import (  # noqa: F401
    WashoutConfig, detect_washouts,
)
