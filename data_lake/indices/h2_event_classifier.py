#!/usr/bin/env python3
"""
h2_event_classifier.py -- fonctions PURES : de quoi chaque evenement H2 est-il fait, et qu'est-ce qui l'empeche
d'entrer dans un test serieux.

Huit classes. Les quatre premieres sont des EMPECHEMENTS (la donnee manque ou est douteuse) et sont evaluees
en premier ; les quatre dernieres decrivent la STRUCTURE du marche et ne sont atteintes que par un evenement
sans empechement. Un evenement propre est donc un evenement dont la classe decrit le marche et non un trou.

    BAD_TIMESTAMP               l'heure de l'evenement n'est pas fiable
    INSUFFICIENT_MARKET_STATE   l'etat de marche autour de l'evenement est incomplet
    PROVIDER_NEEDED             ce qui manque n'existe pas gratuitement
    INSUFFICIENT_EXECUTION_DATA le cout reel d'executer n'est pas connu
    UNKNOWN_PRECEDENCE          on ignore si le jeton etait deja price ailleurs
    OTHER_VENUE_FIRST           il l'etait
    BINANCE_SPOT_FIRST          le spot Binance existait avant le perpetuel
    TRUE_BINANCE_PERP_FIRST     le perpetuel Binance est le premier marche, partout

Aucun prix, aucun rendement, aucun verdict alpha.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

BAD_TIMESTAMP = "BAD_TIMESTAMP"
INSUFFICIENT_MARKET_STATE = "INSUFFICIENT_MARKET_STATE"
PROVIDER_NEEDED = "PROVIDER_NEEDED"
INSUFFICIENT_EXECUTION_DATA = "INSUFFICIENT_EXECUTION_DATA"
UNKNOWN_PRECEDENCE = "UNKNOWN_PRECEDENCE"
OTHER_VENUE_FIRST = "OTHER_VENUE_FIRST"
BINANCE_SPOT_FIRST = "BINANCE_SPOT_FIRST"
TRUE_BINANCE_PERP_FIRST = "TRUE_BINANCE_PERP_FIRST"

BLOCKING = (BAD_TIMESTAMP, INSUFFICIENT_MARKET_STATE, PROVIDER_NEEDED, INSUFFICIENT_EXECUTION_DATA, UNKNOWN_PRECEDENCE)
STRUCTURAL = (OTHER_VENUE_FIRST, BINANCE_SPOT_FIRST, TRUE_BINANCE_PERP_FIRST)
ALL_CLASSES = BLOCKING + STRUCTURAL

#: un ecart au-dela duquel l'heure annoncee et la premiere barre ne decrivent plus le meme instant
TIMESTAMP_DISAGREEMENT_MAX_MIN = 15


def _dt(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def classify(ev: Dict[str, Any], require_actual_fees: bool = True) -> Dict[str, Any]:
    """ev porte ce que les phases precedentes ont etabli pour un evenement. Rien n'est devine :
    un champ absent est un empechement, pas une valeur par defaut favorable."""
    reasons: List[str] = []

    announced, first_bar = _dt(ev.get("announced_start_ts")), _dt(ev.get("first_bar_ts"))
    gap = None
    if announced and first_bar:
        gap = round((first_bar - announced).total_seconds() / 60, 1)
    if not first_bar:
        reasons.append("no first traded bar: the event has no time")
    elif gap is not None and abs(gap) > TIMESTAMP_DISAGREEMENT_MAX_MIN:
        reasons.append("announced time and first traded bar disagree by %+.0f min" % gap)
    elif announced is None:
        reasons.append("no announced opening time in the body to corroborate the first bar")
    if reasons:
        return _out(ev, BAD_TIMESTAMP, reasons, gap)

    if not ev.get("market_state_core_complete"):
        reasons.append("core market state incomplete (mark, index reference or trades missing for part of the window)")
        if ev.get("missing_requires_provider"):
            return _out(ev, PROVIDER_NEEDED, reasons + ["what is missing is not published free"], gap)
        return _out(ev, INSUFFICIENT_MARKET_STATE, reasons, gap)
    if ev.get("missing_requires_provider"):
        return _out(ev, PROVIDER_NEEDED, ["depth or index reference for this window is not published free"], gap)

    if require_actual_fees and not ev.get("actual_fee_known"):
        reasons.append("the fee actually charged on this account is unknown (published schedule only)")
    if not ev.get("capacity_known"):
        reasons.append("capacity has not been measured (depth archives exist; nothing has been derived from them)")
    if reasons:
        return _out(ev, INSUFFICIENT_EXECUTION_DATA, reasons, gap)

    prec = ev.get("venue_precedence")
    if prec in (None, "", "UNKNOWN_PRECEDENCE"):
        return _out(ev, UNKNOWN_PRECEDENCE, ["no venue has a published listing date for this asset"], gap)
    if prec == "OTHER_VENUE_FIRST":
        return _out(ev, OTHER_VENUE_FIRST, ["a dated listing elsewhere precedes the Binance launch"], gap)
    if ev.get("binance_spot_existed_before"):
        return _out(ev, BINANCE_SPOT_FIRST, ["Binance spot traded before the perpetual"], gap)
    return _out(ev, TRUE_BINANCE_PERP_FIRST, ["no earlier market found on Binance or on any dated venue"], gap)


def _out(ev: Dict[str, Any], cls: str, reasons: List[str], gap: Optional[float]) -> Dict[str, Any]:
    return {"event_id": ev.get("event_id"), "symbol": ev.get("symbol"), "asset": ev.get("asset"), "launch_ts": ev.get("first_bar_ts"),
            "classification": cls, "blocking": cls in BLOCKING, "reasons": reasons,
            "timestamp_gap_min": gap, "coverage_score": ev.get("coverage_score"), "venue_precedence": ev.get("venue_precedence")}


def eligible(classified: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [c for c in classified if not c["blocking"]]


def summarise(classified: List[Dict[str, Any]]) -> Dict[str, Any]:
    from collections import Counter
    c = Counter(x["classification"] for x in classified)
    return {"n": len(classified), "by_class": {k: c.get(k, 0) for k in ALL_CLASSES if c.get(k)},
            "eligible": len(eligible(classified)), "blocked": sum(1 for x in classified if x["blocking"]),
            "dominant_blocker": (Counter(x["classification"] for x in classified if x["blocking"]).most_common(1) or [(None, 0)])[0][0]}
