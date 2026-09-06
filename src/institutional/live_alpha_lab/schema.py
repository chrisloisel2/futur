"""
src/institutional/live_alpha_lab/schema.py
─────────────────────────────────────────────────────────────────────────────
TABLE CANONIQUE : quelle colonne porte l'instant de l'événement, et laquelle
porte le symbole, pour chaque alpha du lab.

Pourquoi ce fichier existe
──────────────────────────
La même table vivait en TROIS copies indépendantes :
  - scripts/apply_provenance_tags.py   (12 entrées, à jour)
  - scripts/compute_live_alpha_lab_scoreboard.py (12, à jour depuis le
    2026-09-05 — son propre commentaire note que l'absence de
    LIQ_CASCADE_REPEAT_SYSTEMIC_V1 et AMIHUD avait laissé « un angle mort de
    monitoring sur précisément les deux alphas issus de la validation »)
  - src/institutional/live_alpha_lab/trade_trace.py (9 entrées — PÉRIMÉE :
    il manquait BTC_LEAD_ALT_CASCADE_V1, LIQ_CASCADE_REPEAT_SYSTEMIC_V1 et
    AMIHUD_ILLIQUIDITY_PREMIUM_V1)

Le mode de panne est toujours le même et toujours silencieux : un alpha absent
de la copie qu'on interroge n'est pas signalé, il disparaît simplement de la
mesure. Trois copies, c'est trois occasions d'ajouter un alpha à deux d'entre
elles. La discipline « jamais deviné, toujours mappé explicitement » était
bonne ; c'est la duplication qui la rendait fragile.

Ajouter un alpha ici, et NULLE PART AILLEURS.
"""
from __future__ import annotations

from typing import Dict, Optional

TIME_COL_BY_ALPHA: Dict[str, str] = {
    "LIQ_CASCADE_REPEAT_V1": "event_time",
    "LIQ_CASCADE_REPEAT_SYSTEMIC_V1": "event_time",
    "LIQ_CASCADE_FAR_FROM_LOW_V1": "event_time",
    "BTC_LEAD_ALT_CASCADE_V1": "event_time",
    "SHORT_COVERING_CONTINUATION_V1": "timestamp",
    "WHALE_LSR_SCREEN_V1": "timestamp",
    "FUNDING_BASIS_DISAGREEMENT_V1": "date",
    "FUNDING_BASIS_DISAGREEMENT_V2": "date",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": "event_time",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2": "event_time",
    "VOL_FORECAST_LAYER_V1": "event_time",
    "AMIHUD_ILLIQUIDITY_PREMIUM_V1": "event_time",
}

# None = univers mono-symbole (VOL_FORECAST_LAYER_V1, BTC seul) : pas de
# decluster cross-symbole applicable. Distinct d'une entrée ABSENTE, qui
# signifie « pas encore mappé » et doit se lire comme une lacune.
SYMBOL_COL_BY_ALPHA: Dict[str, Optional[str]] = {
    "LIQ_CASCADE_REPEAT_V1": "symbol",
    "LIQ_CASCADE_REPEAT_SYSTEMIC_V1": "symbol",
    "LIQ_CASCADE_FAR_FROM_LOW_V1": "symbol",
    "BTC_LEAD_ALT_CASCADE_V1": "symbol",
    "SHORT_COVERING_CONTINUATION_V1": "asset",
    "WHALE_LSR_SCREEN_V1": "symbol",
    "FUNDING_BASIS_DISAGREEMENT_V1": "symbol",
    "FUNDING_BASIS_DISAGREEMENT_V2": "symbol",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": "symbol",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2": "symbol",
    "VOL_FORECAST_LAYER_V1": None,
    "AMIHUD_ILLIQUIDITY_PREMIUM_V1": "symbol",
}
