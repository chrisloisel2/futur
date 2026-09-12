#!/usr/bin/env python3
"""
mexc_volume_trust.py -- MEXC_VOLUME_TRUST_V1 : le volume MEXC d'avant Binance est-il celui d'un marche ou celui d'un
programme ? Fonctions PURES sur des bougies horaires deja stockees, strictement avant t0 ET avant l'annonce Binance.

Ce que ce module est : un jeu de covariables DESCRIPTIVES, non validees (aucun evenement de wash etiquete n'existe),
calculees sur une fenetre declaree a l'avance, rangees a l'interieur de MEXC, avec un drapeau dont la fraction est
pre-declaree (decile superieur) et une regle mecanique separee (plancher >= 0,8 ET CV <= 0,4).

Ce qu'il n'est pas : un score de confiance calibre (impossible sans verite terrain), un filtre par evenement (interdit :
un ban par evenement sur un score bruite serait un filtre dependant des donnees), un signal, un verdict.

Puissance declaree : sensible aux programmes a debit constant seulement ; aveugle aux bots qui imitent l'organique ;
taux de faux positifs inconnu. Voir FEATURE_POLICY pour la regle globale (par feature, jamais par evenement).
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from data_lake.indices.pre_binance_features import PostT0Leak, strictly_before

H = 3_600_000
VERSION = "MEXC_VOLUME_TRUST_V1"
WINDOW_H = 72                 #: fenetre horaire : les 72 h avant t0 ...
LISTING_BURN_IN_H = 24        #: ... mais jamais les 24 premieres heures de cotation MEXC (bougies de lancement)
MIN_HOURS = 36                #: sous ce support : NOT_COMPUTABLE, pas de valeur
ANTICIPATION_H = 24           #: derniere journee avant l'annonce, comparee au reste de la fenetre
ANTICIPATION_MIN_BASE_H = 24  #: base minimale pour le ratio d'anticipation
WINSOR_PCT = 0.99
INTEGRITY_TOL = 0.005         #: vwap implicite dans [low*(1-tol), high*(1+tol)]
TOP_FRACTION = 0.10           #: drapeau pre-declare : decile superieur du rang d'anomalie
MIN_RANKED = 10               #: sous 10 evenements MEASURED, aucun drapeau (un decile de 3 n'a pas de sens)
PROGRAMME_FLOOR = 0.8         #: regle mecanique : p10/mediane >= 0,8 ...
PROGRAMME_CV = 0.4            #: ... et CV <= 0,4 (suggere par un relecteur apres lecture de 4 tapes -- divulgue)
STATUSES = ("MEASURED", "NOT_COMPUTABLE", "INTEGRITY_FAIL", "NO_TAPE")
FEATURES = ("volume_floor", "volume_cv", "volume_range_coupling", "log10_volume_per_range")
REQUIRED = ("volume_floor", "volume_cv", "log10_volume_per_range")   #: le couplage peut etre indefini (serie constante) sans invalider l'evenement
#: sens de l'anomalie pour le rang : True = plus c'est haut, plus c'est anormal
ANOMALY_HIGH = {"volume_floor": True, "volume_cv": False, "volume_range_coupling": False, "log10_volume_per_range": True}

#: Regle GLOBALE (par feature, jamais par evenement). "conditioning" = peut etre une variable de conditionnement dans
#: une prereg ; "covariate" = descriptif / robustesse seulement ; "banned_cross_venue" = jamais compare entre places.
FEATURE_POLICY: List[Dict[str, str]] = [
    {"feature": "pre_binance_return_30d/14d/7d/3d/24h", "class": "price_only", "allowed_as": "conditioning", "why": "un prix ne se gonfle pas par wash ; bougies completes closes <= t0 (daily : <= dernier minuit UTC)"},
    {"feature": "pre_binance_volatility_7d, range_7d, max_drawdown_7d, pump_score", "class": "price_only", "allowed_as": "conditioning", "why": "derives de prix seulement"},
    {"feature": "pre_binance_volume_7d, volume_24h, liquidity_proxy", "class": "volume_derived", "allowed_as": "covariate", "why": "gonflables par un programme ; jamais variable de conditionnement ni d'exclusion pour MEXC_FIRST"},
    {"feature": "pre_binance_volume_* en USD absolu", "class": "volume_derived", "allowed_as": "banned_cross_venue", "why": "un niveau de volume n'est comparable ni entre places ni entre regimes de frais ; seuls des rangs intra-MEXC sont lisibles"},
    {"feature": "pre_binance_exhaustion_score", "class": "mixed", "allowed_as": "covariate", "why": "40 % du score est un terme de volume"},
    {"feature": "volume_floor, volume_cv, volume_range_coupling, log10_volume_per_range", "class": "wash_covariate", "allowed_as": "covariate", "why": "non valides ; rangs intra-MEXC ; controle de robustesse, jamais un filtre"},
    {"feature": "volume_anomaly_rank, wash_volume_suspect, programme_like", "class": "wash_covariate", "allowed_as": "covariate", "why": "fraction pre-declaree (decile) ; un ban par evenement serait un filtre dependant des donnees"},
    {"feature": "anticipation_ratio", "class": "anticipation", "allowed_as": "covariate", "why": "mesure une anticipation (information), pas un wash ; hors du drapeau par construction"},
    {"feature": "venue_volume_multiple, coupling_gap (meme actif, second venue)", "class": "same_asset_control", "allowed_as": "covariate", "why": "seule quantite controlee par l'actif ; descriptive"},
]
DECLARED_LIMITS = [
    "detects constant-rate volume programmes only (floor high, CV low, coupling weak); blind to bots that mimic organic flow",
    "no labelled wash event exists on any venue: sensitivity, specificity and false-positive rate are UNKNOWN",
    "event-specific inflation below ~10x is undetectable from candles (cross-sectional SD of log10 hourly volume ~0.8)",
    "volume-per-range is confounded with genuine depth and token size: high = deep OR washed",
    "the 24 other-venue-first events are a venue+market+fee+era difference, never a per-event control",
    "the mechanical thresholds (floor 0.8, CV 0.4) were suggested by a reviewer after reading 4 MEXC tapes; the top-decile rule is parameter-free",
]


def floor_hour(ms: int) -> int:
    return (int(ms) // H) * H


def window_bounds(t0_ms: int, publication_ms: Optional[int], listed_ms: Optional[int] = None) -> Tuple[int, int]:
    """W = [max(listed + 24 h, t0 - 72 h), floor_hour(min(publication, t0))). Une bougie est dans W si elle ouvre a
    ou apres le debut et CLOTURE au plus tard a la fin."""
    end = floor_hour(min(int(publication_ms), int(t0_ms)) if publication_ms else int(t0_ms))
    start = int(t0_ms) - WINDOW_H * H
    if listed_ms:
        start = max(start, int(listed_ms) + LISTING_BURN_IN_H * H)
    return start, end


def select_hours(rows: Sequence[Dict[str, Any]], t0_ms: int, start_ms: int, end_ms: int) -> Dict[str, Any]:
    """Refuse toute bougie qui cloture apres t0 (PostT0Leak), puis garde celles qui ouvrent >= start et clôturent <= end.
    Compte a part les heures retirees parce que post-annonce (closent apres end mais <= t0)."""
    rows = strictly_before(list(rows), t0_ms, H)
    kept = [r for r in rows if r["open_time_ms"] >= start_ms and r["open_time_ms"] + H <= end_ms]
    post_ann = sum(1 for r in rows if r["open_time_ms"] + H > end_ms)
    pre_burn = sum(1 for r in rows if r["open_time_ms"] < start_ms)
    return {"rows": kept, "n_hours_used": len(kept), "n_post_announcement_dropped": post_ann, "n_before_window_dropped": pre_burn}


def infer_multiplier(rows: Sequence[Dict[str, Any]], market: str) -> Optional[float]:
    """Spot : 1. Perp MEXC : `volume` est un nombre de contrats ; le multiplicateur est la puissance de 10 la plus
    proche de la mediane de quote_volume / (volume * close). Jamais lu ailleurs que pour la porte d'integrite."""
    if market != "perp":
        return 1.0
    xs = [r["quote_volume"] / (r["volume"] * r["close"]) for r in rows if r.get("volume") and r.get("quote_volume") and r.get("close")]
    if not xs:
        return None
    m = statistics.median(xs)
    return 10.0 ** round(math.log10(m)) if m > 0 else None


def integrity(rows: Sequence[Dict[str, Any]], multiplier: Optional[float], tol: float = INTEGRITY_TOL) -> Dict[str, int]:
    """Porte d'integrite : le VWAP implicite quote_volume / (volume * mult) doit tomber dans [low, high] a tol pres.
    Aucun trader ne peut la contourner ; une violation = donnee incoherente, pas un wash."""
    n = v = 0
    if not multiplier:
        return {"n_checked": 0, "n_violations": 0}
    for r in rows:
        if not r.get("volume") or r.get("quote_volume") is None or r["quote_volume"] <= 0:
            continue
        n += 1; vwap = r["quote_volume"] / (r["volume"] * multiplier)
        if not (r["low"] * (1 - tol) <= vwap <= r["high"] * (1 + tol)):
            v += 1
    return {"n_checked": n, "n_violations": v}


def _pct(xs: Sequence[float], p: float) -> float:
    s = sorted(xs); k = (len(s) - 1) * p; f = math.floor(k); c = math.ceil(k)
    return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)


def _ranks(xs: Sequence[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0] * len(xs); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y); mx, my = statistics.mean(rx), statistics.mean(ry)
    sxx = sum((a - mx) ** 2 for a in rx); syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(sxx * syy)


def features(rows: Sequence[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Les quatre covariables, sur quote_volume seulement (le volume de base d'un perp est un nombre de contrats)."""
    qv = [float(r["quote_volume"]) for r in rows if r.get("quote_volume") is not None]
    out: Dict[str, Optional[float]] = {k: None for k in FEATURES}; out["zero_range_share"] = None; out["n_qv"] = len(qv)
    if len(qv) < 3:
        return out
    med = statistics.median(qv)
    out["volume_floor"] = round(_pct(qv, 0.10) / med, 4) if med > 0 else None
    cap = _pct(qv, WINSOR_PCT); w = [min(v, cap) for v in qv]; mean = statistics.mean(w)
    out["volume_cv"] = round(statistics.stdev(w) / mean, 4) if mean > 0 and len(w) > 1 else None
    pairs = [(float(r["quote_volume"]), (r["high"] - r["low"]) / r["close"]) for r in rows if r.get("quote_volume") is not None and r.get("close")]
    out["volume_range_coupling"] = None if not pairs else (lambda s: round(s, 4) if s is not None else None)(spearman([p[0] for p in pairs], [p[1] for p in pairs]))
    nz = [q / rg for q, rg in pairs if rg > 0 and q > 0]
    out["zero_range_share"] = round(1 - len([1 for _, rg in pairs if rg > 0]) / len(pairs), 4) if pairs else None
    out["log10_volume_per_range"] = round(math.log10(statistics.median(nz)), 4) if nz else None
    return out


def anticipation_ratio(rows: Sequence[Dict[str, Any]], end_ms: int) -> Dict[str, Optional[float]]:
    """Volume horaire moyen des 24 h avant l'annonce / mediane horaire du reste de W. Anticipation, pas wash : hors drapeau."""
    last = [float(r["quote_volume"]) for r in rows if r["open_time_ms"] >= end_ms - ANTICIPATION_H * H and r.get("quote_volume") is not None]
    base = [float(r["quote_volume"]) for r in rows if r["open_time_ms"] < end_ms - ANTICIPATION_H * H and r.get("quote_volume") is not None]
    if len(base) < ANTICIPATION_MIN_BASE_H or not last or statistics.median(base) <= 0:
        return {"anticipation_ratio": None, "anticipation_hours": len(last), "anticipation_base_hours": len(base)}
    return {"anticipation_ratio": round(statistics.mean(last) / statistics.median(base), 4), "anticipation_hours": len(last), "anticipation_base_hours": len(base)}


def compute_event(hourly: Sequence[Dict[str, Any]], t0_ms: int, publication_ms: Optional[int], listed_ms: Optional[int], market: str) -> Dict[str, Any]:
    start, end = window_bounds(t0_ms, publication_ms, listed_ms)
    out: Dict[str, Any] = {"version": VERSION, "market": market, "window_start_ms": start, "window_end_ms": end, "status": None, "reason": None, "n_hours_used": 0,
                           "n_post_announcement_dropped": 0, "n_before_window_dropped": 0, "multiplier": None, "integrity": {"n_checked": 0, "n_violations": 0},
                           **{k: None for k in FEATURES}, "zero_range_share": None, "anticipation_ratio": None, "anticipation_hours": 0, "anticipation_base_hours": 0}
    if not hourly:
        out["status"], out["reason"] = "NO_TAPE", "no hourly tape stored"; return out
    sel = select_hours(hourly, t0_ms, start, end)                       # PostT0Leak remonte : jamais avale
    out.update({k: sel[k] for k in ("n_hours_used", "n_post_announcement_dropped", "n_before_window_dropped")})
    rows = sel["rows"]
    if len(rows) < MIN_HOURS:
        out["status"], out["reason"] = "NOT_COMPUTABLE", "n_hours %d < %d" % (len(rows), MIN_HOURS); return out
    mult = infer_multiplier(rows, market); out["multiplier"] = mult
    integ = integrity(rows, mult); out["integrity"] = integ
    if integ["n_violations"]:
        out["status"], out["reason"] = "INTEGRITY_FAIL", "%d/%d candles with implied vwap outside [low, high]" % (integ["n_violations"], integ["n_checked"]); return out
    out.update(features(rows)); out.update(anticipation_ratio(rows, end))
    missing = [k for k in REQUIRED if out[k] is None]
    if missing:
        out["status"], out["reason"] = "NOT_COMPUTABLE", "undefined: " + ",".join(missing); return out
    out["status"] = "MEASURED"; return out


def percentile_rank(values: Sequence[float], v: float, higher_is_anomalous: bool) -> float:
    """Fraction des valeurs 'moins anormales ou egales' (ex aequo comptes pour moitie) ; dans [0, 1]."""
    n = len(values); less = sum(1 for x in values if (x < v if higher_is_anomalous else x > v)); ties = sum(1 for x in values if x == v)
    return (less + 0.5 * ties) / n if n else float("nan")


def rank_events(results: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Rangs intra-MEXC des evenements MEASURED, rang d'anomalie moyen, drapeau decile superieur (fraction declaree
    AVANT tout calcul), regle mecanique separee. Les non-MEASURED gardent None partout : jamais 'suspect' par defaut."""
    meas = {e: r for e, r in results.items() if r.get("status") == "MEASURED"}
    cols = {k: [r[k] for r in meas.values()] for k in FEATURES}
    out: Dict[str, Dict[str, Any]] = {}
    for e, r in results.items():
        o = {k + "_rank": None for k in FEATURES}; o.update({"volume_anomaly_rank": None, "n_features_ranked": 0, "wash_volume_suspect": None, "programme_like": None})
        if e in meas:
            rk = {k: percentile_rank([v for v in cols[k] if v is not None], r[k], ANOMALY_HIGH[k]) for k in FEATURES if r[k] is not None}
            o.update({k + "_rank": round(v, 4) for k, v in rk.items()}); o["volume_anomaly_rank"] = round(statistics.mean(rk.values()), 4); o["n_features_ranked"] = len(rk)
            o["programme_like"] = bool(r["volume_floor"] >= PROGRAMME_FLOOR and r["volume_cv"] <= PROGRAMME_CV)
        out[e] = o
    ranks = sorted((o["volume_anomaly_rank"] for o in out.values() if o["volume_anomaly_rank"] is not None), reverse=True)
    n_flag = int(math.ceil(TOP_FRACTION * len(ranks))) if len(ranks) >= MIN_RANKED else 0
    cut = ranks[n_flag - 1] if n_flag else None
    for o in out.values():
        if o["volume_anomaly_rank"] is not None:
            o["wash_volume_suspect"] = bool(o["volume_anomaly_rank"] >= cut) if cut is not None else None
    return out


def same_asset_control(mexc_rows: Sequence[Dict[str, Any]], other_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Meme actif, memes heures, autre place : multiple de volume median et ecart de couplage volume/amplitude.
    La seule comparaison controlee par l'actif ; descriptive."""
    m = {r["open_time_ms"]: r for r in mexc_rows if r.get("quote_volume")}; o = {r["open_time_ms"]: r for r in other_rows if r.get("quote_volume")}
    common = sorted(set(m) & set(o))
    out: Dict[str, Any] = {"n_common_hours": len(common), "status": None, "log10_venue_volume_multiple": None, "coupling_gap": None, "coupling_mexc": None, "coupling_other": None}
    if len(common) < MIN_HOURS:
        out["status"] = "NOT_COMPUTABLE"; return out
    ratios = [m[t]["quote_volume"] / o[t]["quote_volume"] for t in common if o[t]["quote_volume"] > 0 and m[t]["quote_volume"] > 0]
    fm, fo = features([m[t] for t in common]), features([o[t] for t in common])
    out["log10_venue_volume_multiple"] = round(math.log10(statistics.median(ratios)), 4) if ratios else None
    out["coupling_mexc"], out["coupling_other"] = fm["volume_range_coupling"], fo["volume_range_coupling"]
    if fm["volume_range_coupling"] is not None and fo["volume_range_coupling"] is not None:
        out["coupling_gap"] = round(fm["volume_range_coupling"] - fo["volume_range_coupling"], 4)
    out["status"] = "MEASURED" if out["log10_venue_volume_multiple"] is not None else "NOT_COMPUTABLE"; return out


def venue_level_difference(mexc_values: Sequence[float], control_values: Sequence[float]) -> Dict[str, Any]:
    """Difference de moyenne (log10) MEXC - places de controle, SE, IC 95 %, et le ratio minimal detectable.
    Etiquette obligatoire : difference de place + marche + frais + epoque, jamais un controle par evenement."""
    a, b = list(mexc_values), list(control_values)
    out = {"n_mexc": len(a), "n_control": len(b), "mean_mexc": None, "mean_control": None, "diff_log10": None, "se": None, "ci95_log10": None, "ci95_half_width_ratio": None,
           "label": "venue+market+fee+era difference; not a per-event control; selection on Binance listing applies to both groups"}
    if len(a) < 2 or len(b) < 2:
        return out
    ma, mb = statistics.mean(a), statistics.mean(b); se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    out.update({"mean_mexc": round(ma, 4), "mean_control": round(mb, 4), "diff_log10": round(ma - mb, 4), "se": round(se, 4),
                "ci95_log10": [round(ma - mb - 1.96 * se, 4), round(ma - mb + 1.96 * se, 4)], "ci95_half_width_ratio": round(10 ** (1.96 * se), 2)})
    return out
