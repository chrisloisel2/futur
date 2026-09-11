#!/usr/bin/env python3
"""
venue_precedence.py -- fonctions PURES : ce marche existait-il ailleurs AVANT que Binance ouvre son perpetuel ?

La question n'est pas "y a-t-il un signal" mais "le jeton etait-il deja price ailleurs". La reponse a quatre
formes, et l'une d'elles est l'ignorance, qui doit rester visible :

    OTHER_VENUE_FIRST   une place datee a liste l'actif avant le lancement Binance
    BINANCE_FIRST       toutes les places connues l'ont liste apres (ou ne le listent pas)
    UNKNOWN_PRECEDENCE  un marche existe ailleurs mais aucune date n'est publiee : on ne peut pas trancher
    NO_OTHER_VENUE      aucune place de la liste ne cote cet actif

Aucun prix, aucun ordre, aucun verdict alpha.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

OTHER_VENUE_FIRST, BINANCE_FIRST, UNKNOWN_PRECEDENCE, NO_OTHER_VENUE = "OTHER_VENUE_FIRST", "BINANCE_FIRST", "UNKNOWN_PRECEDENCE", "NO_OTHER_VENUE"
CONFIDENCE = ("high", "medium", "low", "none")
_MULT = re.compile(r"^(1000000|100000|10000|1000|1M|10)(?=[A-Z])")


def normalise_base(asset: str) -> str:
    """Binance cote parfois un multiple (1000PEPE) la ou les autres cotent l'unite (PEPE).
    Le prefixe multiplicateur n'est pas une identite d'actif."""
    a = (asset or "").upper()
    return _MULT.sub("", a) or a


def _dt(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def index_by_base(instruments: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for i in instruments:
        out.setdefault(normalise_base(i.get("base")), []).append(i)
    return out


def decide(asset: str, binance_launch_ts: str, by_base: Dict[str, List[Dict[str, Any]]],
           announcement_evidence: Optional[str] = None) -> Dict[str, Any]:
    """Precedence d'un actif. `announcement_evidence` est une annonce officielle anterieure trouvee dans le
    tape (P5) : une preuve plus faible qu'une date d'instrument, mais une preuve."""
    base = normalise_base(asset); launch = _dt(binance_launch_ts)
    matches = by_base.get(base, [])
    before, after, undated = [], [], []
    for i in matches:
        t = _dt(i.get("first_listed_ts"))
        if t is None:
            undated.append(i)
        elif launch and t < launch:
            before.append((t, i))
        else:
            after.append((t, i))
    before.sort(key=lambda x: x[0])
    res: Dict[str, Any] = {"asset": asset, "normalised_base": base, "binance_launch_ts": binance_launch_ts,
                           "venues_matched": sorted({i["venue"] for i in matches}), "n_instruments": len(matches),
                           "n_dated_before": len(before), "n_dated_after": len(after), "n_undated": len(undated),
                           "undated_venues": sorted({i["venue"] for i in undated}),
                           "announcement_evidence": announcement_evidence}
    if before:
        t, i = before[0]
        res.update({"classification": OTHER_VENUE_FIRST, "confidence": "high",
                    "first_elsewhere_ts": i["first_listed_ts"], "first_elsewhere_venue": i["venue"],
                    "first_elsewhere_market": i["market_type"], "first_elsewhere_symbol": i["symbol"],
                    "lead_days": round((launch - t).total_seconds() / 86400, 2) if launch else None,
                    "evidence": "%s %s %s listed %s (%s)" % (i["venue"], i["market_type"], i["symbol"], i["first_listed_ts"], i["first_listed_source"])})
        return res
    if undated:
        res.update({"classification": UNKNOWN_PRECEDENCE, "confidence": "medium" if announcement_evidence else "low",
                    "first_elsewhere_ts": None, "first_elsewhere_venue": None, "first_elsewhere_market": None, "first_elsewhere_symbol": None, "lead_days": None,
                    "evidence": "market exists on %s but no listing date is published%s" % (", ".join(res["undated_venues"]), "; tape shows an earlier announcement: " + announcement_evidence if announcement_evidence else "")})
        return res
    if matches:
        res.update({"classification": BINANCE_FIRST, "confidence": "high" if not announcement_evidence else "medium",
                    "first_elsewhere_ts": min(i["first_listed_ts"] for _, i in after) if after else None,
                    "first_elsewhere_venue": None, "first_elsewhere_market": None, "first_elsewhere_symbol": None, "lead_days": None,
                    "evidence": "every dated listing elsewhere is after the Binance launch" + ("; but the tape shows an earlier announcement: " + announcement_evidence if announcement_evidence else "")})
        return res
    res.update({"classification": NO_OTHER_VENUE if not announcement_evidence else UNKNOWN_PRECEDENCE,
                "confidence": "high" if not announcement_evidence else "low",
                "first_elsewhere_ts": None, "first_elsewhere_venue": None, "first_elsewhere_market": None, "first_elsewhere_symbol": None, "lead_days": None,
                "evidence": "no instrument on any collected venue" + ("; the tape shows an earlier announcement: " + announcement_evidence if announcement_evidence else "")})
    return res


def summarise(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    from collections import Counter
    c = Counter(d["classification"] for d in decisions)
    known = c[OTHER_VENUE_FIRST] + c[BINANCE_FIRST] + c[NO_OTHER_VENUE]
    leads = sorted(d["lead_days"] for d in decisions if d.get("lead_days") is not None)
    return {"n": len(decisions), "by_classification": dict(c), "known": known, "unknown": c[UNKNOWN_PRECEDENCE],
            "by_confidence": dict(Counter(d["confidence"] for d in decisions)),
            "first_venue": dict(Counter(d["first_elsewhere_venue"] for d in decisions if d.get("first_elsewhere_venue"))),
            "lead_days": {"min": leads[0], "median": leads[len(leads) // 2], "max": leads[-1]} if leads else None}
