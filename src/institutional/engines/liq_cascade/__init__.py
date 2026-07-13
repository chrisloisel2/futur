"""Moteur événementiel LIQ_CASCADE — cascades de deleveraging 5-min (OI Vision).

Données : metrics Binance Vision 5-min (OI + ratios + taker), 2021-2026, multi-actifs.
Détection : chute d'OI rapide (z-score) + mouvement de prix → event de cascade.
Le feed liquidations live (Bybit/OKX, collecté depuis 2026-07-04) sert de
validation du proxy puis de déclencheur temps réel en production.
"""
from src.institutional.engines.liq_cascade.detector import (  # noqa: F401
    CascadeConfig, detect_cascades, load_metrics,
)
from src.institutional.engines.liq_cascade.dataset import (  # noqa: F401
    build_event_dataset,
)
