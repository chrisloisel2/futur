#!/usr/bin/env python3
"""
scripts/normalize_stress_gate_dispersion_v2.py
─────────────────────────────────────────────────────────────────────────────
Opération A (normaliser + diagnostiquer), PAS l'opération B (décider quoi
entre dans le panel primaire — celle-ci reste strictement conforme au
préenregistrement, appliquée séparément dans build_primary_panel()).

Aucune statistique économique ici : pas de quantile expanding, pas de
drawdown, pas de Newey-West, pas de bootstrap. Uniquement : dédup fidèle,
histogramme d'intervalles, segmentation de régime de cadence, couverture
cross-venue, complétude des fenêtres forward (présence de barres, PAS leur
valeur de creux), hashes.

Lit les pages brutes déjà collectées (data/research/stress_gate_dispersion_v2/raw/)
sans jamais les modifier. Écrit dans normalized/ et manifests/.
"""
from __future__ import annotations

import glob
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "research" / "stress_gate_dispersion_v2"
RAW = BASE / "raw"
NORM = BASE / "normalized"
MANIFESTS = BASE / "manifests"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
SIGNAL_START_MS = int(datetime(2022, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
SIGNAL_END_MS = int(datetime(2026, 7, 14, tzinfo=timezone.utc).timestamp() * 1000)
PRICE_START_MS = SIGNAL_START_MS
PRICE_END_MS = int(datetime(2026, 7, 15, 1, tzinfo=timezone.utc).timestamp() * 1000)
BAR_STEP_MS = 5 * 60_000
FORWARD_HORIZON_MS = 24 * 3600_000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ── chargement des pages brutes (lecture seule, jamais modifiées) ──────────

def load_raw_binance_funding(symbol: str) -> List[dict]:
    rows: List[dict] = []
    for f in sorted(glob.glob(str(RAW / "binance" / "funding" / f"{symbol}_*.json"))):
        rows.extend(json.loads(Path(f).read_text()))
    return rows


def load_raw_bybit_funding(symbol: str) -> List[dict]:
    rows: List[dict] = []
    for f in sorted(glob.glob(str(RAW / "bybit" / "funding" / f"{symbol}_*.json"))):
        parsed = json.loads(Path(f).read_text())
        rows.extend(parsed.get("result", {}).get("list", []))
    return rows


def load_raw_binance_markprice(symbol: str) -> List[list]:
    rows: List[list] = []
    for f in sorted(glob.glob(str(RAW / "binance" / "mark_price" / f"{symbol}_*.json"))):
        rows.extend(json.loads(Path(f).read_text()))
    return rows


# ── dédup fidèle : distingue doublon EXACT (même contenu) de CONFLICTUEL
#    (même timestamp, contenu différent — jamais résolu silencieusement) ──

def dedupe_and_diagnose(rows: List[dict], ts_key: str) -> dict:
    unique: Dict[int, dict] = {}
    duplicate_exact = 0
    duplicate_conflicting: List[int] = []
    for r in rows:
        ts = int(r[ts_key])
        if ts in unique:
            if unique[ts] == r:
                duplicate_exact += 1
            else:
                duplicate_conflicting.append(ts)
                # jamais résolu silencieusement : on garde la première vue,
                # mais le conflit est rapporté explicitement (opération A =
                # diagnostiquer, pas décider) ; le conflit doit bloquer
                # DATASET_READY_FOR_REVIEW tant qu'il n'est pas expliqué.
        else:
            unique[ts] = r
    return {"unique": unique, "rows_raw": len(rows), "rows_unique": len(unique),
           "duplicate_exact": duplicate_exact,
           "duplicate_conflicting": sorted(set(duplicate_conflicting))}


# ── histogramme d'intervalles + segmentation en régimes contigus ─────────

def interval_histogram(ts_sorted: List[int]) -> List[dict]:
    if len(ts_sorted) < 2:
        return []
    deltas = [ts_sorted[i + 1] - ts_sorted[i] for i in range(len(ts_sorted) - 1)]
    # bucketé à la minute la plus proche pour absorber le jitter serveur
    # (quelques dizaines de ms observées sur Binance) sans fusionner des
    # cadences réellement différentes (2h/4h/8h restent distinctes).
    buckets = Counter(round(d / 60_000) for d in deltas)
    total = len(deltas)
    out = []
    for minutes, n in sorted(buckets.items(), key=lambda x: -x[1]):
        out.append({"delta_minutes": minutes, "delta_hours": round(minutes / 60, 3),
                   "n_occurrences": n, "share": round(n / total, 4)})
    return out


def segment_cadence_regimes(ts_sorted: List[int]) -> List[dict]:
    """Segments contigus par cadence observée (bucket à l'heure la plus
    proche). Ne suppose JAMAIS 8h a priori — dérivé uniquement des
    timestamps réels."""
    if len(ts_sorted) < 2:
        return []
    def bucket_hours(d_ms: int) -> int:
        return round(d_ms / 3_600_000)
    segments = []
    seg_start = ts_sorted[0]
    cur = bucket_hours(ts_sorted[1] - ts_sorted[0])
    n_in_seg = 1
    for i in range(1, len(ts_sorted) - 1):
        d = ts_sorted[i + 1] - ts_sorted[i]
        b = bucket_hours(d)
        if b != cur:
            segments.append({"start_ts": seg_start, "start_iso": iso(seg_start),
                            "end_ts": ts_sorted[i], "end_iso": iso(ts_sorted[i]),
                            "cadence_hours": cur, "n_settlements": n_in_seg + 1})
            seg_start = ts_sorted[i]
            cur = b
            n_in_seg = 0
        n_in_seg += 1
    segments.append({"start_ts": seg_start, "start_iso": iso(seg_start),
                    "end_ts": ts_sorted[-1], "end_iso": iso(ts_sorted[-1]),
                    "cadence_hours": cur, "n_settlements": n_in_seg + 1})
    return segments


def longest_gap(ts_sorted: List[int]) -> Optional[dict]:
    if len(ts_sorted) < 2:
        return None
    deltas = [(ts_sorted[i + 1] - ts_sorted[i], ts_sorted[i], ts_sorted[i + 1])
             for i in range(len(ts_sorted) - 1)]
    d, a, b = max(deltas)
    return {"gap_hours": round(d / 3_600_000, 2), "from_iso": iso(a), "to_iso": iso(b)}


def coverage_by_year(ts_sorted: List[int]) -> Dict[int, int]:
    c = Counter(datetime.fromtimestamp(t / 1000, tz=timezone.utc).year for t in ts_sorted)
    return dict(sorted(c.items()))


# ── rapport de cadence par série (venue x symbol) ───────────────────────

def cadence_report_funding(venue: str, symbol: str) -> dict:
    if venue == "binance":
        raw = load_raw_binance_funding(symbol)
        diag = dedupe_and_diagnose(raw, "fundingTime")
    else:
        raw = load_raw_bybit_funding(symbol)
        diag = dedupe_and_diagnose(raw, "fundingRateTimestamp")
    ts_in_window = sorted(t for t in diag["unique"] if SIGNAL_START_MS <= t < SIGNAL_END_MS)
    return {
        "venue": venue, "symbol": symbol,
        "rows_raw": diag["rows_raw"], "rows_unique": diag["rows_unique"],
        "rows_in_signal_window": len(ts_in_window),
        "duplicate_exact": diag["duplicate_exact"],
        "duplicate_conflicting": diag["duplicate_conflicting"],
        "interval_histogram": interval_histogram(ts_in_window),
        "cadence_regimes": segment_cadence_regimes(ts_in_window),
        "unexpected_intervals": [h for h in interval_histogram(ts_in_window)
                                 if abs(h["delta_hours"] - 8.0) > 0.05],
        "first_timestamp": ts_in_window[0] if ts_in_window else None,
        "last_timestamp": ts_in_window[-1] if ts_in_window else None,
        "first_iso": iso(ts_in_window[0]) if ts_in_window else None,
        "last_iso": iso(ts_in_window[-1]) if ts_in_window else None,
    }, diag["unique"]


# ── couverture cross-venue (jointure EXACTE, fail-closed, jamais d'asof) ──

def cross_venue_coverage(symbol: str, binance_unique: Dict[int, dict],
                         bybit_unique: Dict[int, dict]) -> dict:
    b_ts = {t for t in binance_unique if SIGNAL_START_MS <= t < SIGNAL_END_MS}
    y_ts = {t for t in bybit_unique if SIGNAL_START_MS <= t < SIGNAL_END_MS}
    common = sorted(b_ts & y_ts)
    binance_only = sorted(b_ts - y_ts)
    bybit_only = sorted(y_ts - b_ts)
    return {
        "symbol": symbol,
        "binance_events": len(b_ts), "bybit_events": len(y_ts),
        "exact_timestamp_intersections": len(common),
        "binance_only_events": len(binance_only), "bybit_only_events": len(bybit_only),
        "intersection_rate_vs_binance": round(len(common) / len(b_ts), 4) if b_ts else None,
        "intersection_rate_vs_bybit": round(len(common) / len(y_ts), 4) if y_ts else None,
        "longest_gap_in_common_events": longest_gap(common),
        "coverage_by_year_common": coverage_by_year(common),
        "coverage_by_year_binance_only": coverage_by_year(binance_only),
        "coverage_by_year_bybit_only": coverage_by_year(bybit_only),
    }


def sol_extra_events_diagnosis(binance_regimes: List[dict], bybit_regimes: List[dict]
                               ) -> dict:
    b_non8h = [r for r in binance_regimes if r["cadence_hours"] != 8]
    y_non8h = [r for r in bybit_regimes if r["cadence_hours"] != 8]
    explained = bool(b_non8h or y_non8h)
    return {
        "extra_events_explained": explained,
        "cause": ("changement de cadence réel décidé par la venue (segments "
                 "contigus non-8h ci-dessous), pas un doublon ni un artefact "
                 "de collecte") if explained else "non expliqué",
        "binance_non_8h_segments": b_non8h,
        "bybit_non_8h_segments": y_non8h,
        "note": ("Fenêtre coïncide avec la période FTX (nov. 2022) — à "
                "confirmer indépendamment avant d'invoquer cette cause dans "
                "un rapport final, non affirmé ici comme preuve, seulement "
                "comme contexte.") if explained else "",
    }


# ── appariement mutuel un-à-un (amendement settlement_timestamp_alignment_v1)
#    ── PAS un test économique : sert uniquement à reconnaître qu'il s'agit
#    du même règlement, jamais à choisir une tolérance a posteriori (celle-ci
#    est fixée à 1000ms AVANT ce diagnostic, cf. décision utilisateur). ────

import bisect

TOLERANCE_MS_DEFAULT = 1000


def mutual_one_to_one_match(binance_ts: List[int], bybit_ts: List[int], *,
                            tolerance_ms: int = TOLERANCE_MS_DEFAULT) -> dict:
    """Règle symétrique : (1) même actif [en amont, par appel] ; (2) |Δ|<=
    tolerance_ms ; (3) chaque événement n'a qu'UN SEUL candidat admissible
    de l'autre côté ; (4) implique mutuellement-plus-proche puisqu'un seul
    candidat existe ; (5) aucun événement réutilisé (candidats = ensembles
    disjoints par construction) ; (6) toute ambiguïté (>1 candidat) est
    rejetée, jamais résolue en choisissant le plus proche. Déterministe,
    invariant à l'ordre d'entrée (dépend seulement des ensembles triés)."""
    b_sorted = sorted(binance_ts)
    y_sorted = sorted(bybit_ts)

    def candidates(ts: int, other_sorted: List[int]) -> List[int]:
        lo = bisect.bisect_left(other_sorted, ts - tolerance_ms)
        hi = bisect.bisect_right(other_sorted, ts + tolerance_ms)
        return other_sorted[lo:hi]

    b_candidates = {b: candidates(b, y_sorted) for b in b_sorted}
    y_candidates = {y: candidates(y, b_sorted) for y in y_sorted}

    matches: List[Tuple[int, int]] = []
    ambiguous_binance, ambiguous_bybit = [], []
    unmatched_binance, unmatched_bybit = [], []

    for b, cands in b_candidates.items():
        if len(cands) == 0:
            unmatched_binance.append(b)
        elif len(cands) > 1:
            ambiguous_binance.append(b)
        else:
            y = cands[0]
            if len(y_candidates[y]) == 1 and y_candidates[y][0] == b:
                matches.append((b, y))
            else:
                ambiguous_binance.append(b)   # b a 1 candidat mais y n'a pas b comme seul candidat
    matched_y_set = {y for _, y in matches}
    for y, cands in y_candidates.items():
        if y in matched_y_set:
            continue
        if len(cands) == 0:
            unmatched_bybit.append(y)
        else:
            ambiguous_bybit.append(y)   # >1 candidat, OU 1 candidat mais pas mutuel

    matched_b, matched_y = {m[0] for m in matches}, {m[1] for m in matches}
    return {"matches": matches,
           "ambiguous_binance": sorted(set(ambiguous_binance) - matched_b),
           "ambiguous_bybit": sorted(set(ambiguous_bybit) - matched_y),
           "unmatched_binance": sorted(unmatched_binance),
           "unmatched_bybit": sorted(unmatched_bybit),
           "b_multi_candidate_count": sum(1 for c in b_candidates.values() if len(c) > 1),
           "y_multi_candidate_count": sum(1 for c in y_candidates.values() if len(c) > 1)}


def offset_stats(matches: List[Tuple[int, int]]) -> dict:
    if not matches:
        return {"n": 0}
    signed = [y - b for b, y in matches]
    abs_sorted = sorted(abs(o) for o in signed)
    def pctl(p):
        idx = min(len(abs_sorted) - 1, int(round(p * (len(abs_sorted) - 1))))
        return abs_sorted[idx]
    return {"n": len(signed), "p50_abs_ms": pctl(0.50), "p95_abs_ms": pctl(0.95),
           "p99_abs_ms": pctl(0.99), "max_abs_ms": abs_sorted[-1],
           "signed_sample": signed[:10]}


def run_mini_audit(symbol: str, binance_ts_window: List[int], bybit_ts_window: List[int],
                   *, tolerance_ms: int = TOLERANCE_MS_DEFAULT) -> dict:
    """Diagnostic pur : vérifie que la règle ≤1s correspond à la structure
    constatée. Ne sélectionne PAS la tolérance (déjà fixée) et ne construit
    aucune cible économique."""
    exact = set(binance_ts_window) & set(bybit_ts_window)
    m = mutual_one_to_one_match(binance_ts_window, bybit_ts_window, tolerance_ms=tolerance_ms)
    return {
        "symbol": symbol, "tolerance_ms": tolerance_ms,
        "n_binance_events": len(binance_ts_window), "n_bybit_events": len(bybit_ts_window),
        "n_exact_matches": len(exact),
        "n_mutual_one_to_one_matches": len(m["matches"]),
        "offset_distribution": offset_stats(m["matches"]),
        "n_ambiguous": len(m["ambiguous_binance"]) + len(m["ambiguous_bybit"]),
        "n_events_multiple_candidates": m["b_multi_candidate_count"] + m["y_multi_candidate_count"],
        "n_unmatched_binance_after_tolerance": len(m["unmatched_binance"]),
        "n_unmatched_bybit_after_tolerance": len(m["unmatched_bybit"]),
        "coverage_by_year_matched": coverage_by_year(sorted(b for b, y in m["matches"])),
    }


# ── mark price : barres attendues vs observées, complétude des fenêtres ──

def markprice_coverage(symbol: str) -> Tuple[dict, Dict[int, list]]:
    rows = load_raw_binance_markprice(symbol)
    diag = dedupe_and_diagnose([{"open_time": r[0], "row": r} for r in rows], "open_time")
    unique = {t: v["row"] for t, v in diag["unique"].items()}
    ts_in_window = sorted(t for t in unique if PRICE_START_MS <= t < PRICE_END_MS)
    expected_n = (PRICE_END_MS - PRICE_START_MS) // BAR_STEP_MS
    expected_set = set(range(PRICE_START_MS, PRICE_END_MS, BAR_STEP_MS))
    observed_set = set(ts_in_window)
    missing = sorted(expected_set - observed_set)
    return {
        "symbol": symbol,
        "rows_raw": diag["rows_raw"], "rows_unique_in_window": len(ts_in_window),
        "duplicate_exact": diag["duplicate_exact"],
        "duplicate_conflicting": diag["duplicate_conflicting"],
        "expected_bars": expected_n, "observed_bars": len(ts_in_window),
        "missing_bars": len(missing),
        "missing_sample": [iso(t) for t in missing[:10]],
        "longest_gap": longest_gap(ts_in_window),
        "first_iso": iso(ts_in_window[0]) if ts_in_window else None,
        "last_iso": iso(ts_in_window[-1]) if ts_in_window else None,
    }, unique


def forward_window_completeness(signal_ts_sorted: List[int],
                                markprice_ts_set: set) -> dict:
    """Complétude de présence des barres — PAS le calcul du creux lui-même
    (ça reste Phase 2). decision_timestamp = première barre 5m dont l'open
    est strictement postérieur à signal_timestamp."""
    complete, incomplete = 0, 0
    incomplete_examples = []
    for sts in signal_ts_sorted:
        decision_ts = ((sts // BAR_STEP_MS) + 1) * BAR_STEP_MS
        if decision_ts <= sts:
            decision_ts += BAR_STEP_MS
        expected_bars = set(range(decision_ts, decision_ts + FORWARD_HORIZON_MS, BAR_STEP_MS))
        if expected_bars.issubset(markprice_ts_set):
            complete += 1
        else:
            incomplete += 1
            if len(incomplete_examples) < 5:
                incomplete_examples.append(iso(sts))
    return {"signals_total": len(signal_ts_sorted), "signals_complete_window": complete,
           "signals_rejected_incomplete_window": incomplete,
           "incomplete_examples": incomplete_examples}


def decision_timestamp_for(pair_available_at: int) -> int:
    """Première barre mark price 5m dont l'open est STRICTEMENT postérieur
    à pair_available_at (amendement settlement_timestamp_alignment_v1) —
    jamais arrondi vers le passé, jamais la barre courante."""
    dt = ((pair_available_at // BAR_STEP_MS) + 1) * BAR_STEP_MS
    if dt <= pair_available_at:
        dt += BAR_STEP_MS
    return dt


def window_bars_present(decision_ts: int, markprice_ts_set: set) -> bool:
    expected = set(range(decision_ts, decision_ts + FORWARD_HORIZON_MS, BAR_STEP_MS))
    return expected.issubset(markprice_ts_set)


# ── comparabilité des intervalles (amendement sexies) — variable primaire
#    = taux réglé brut, JAMAIS normalisé silencieusement ; seule l'ADMISSION
#    au panel primaire dépend de l'égalité des intervalles observés. ──────

ALLOWED_INTERVAL_HOURS = (2, 4, 8)
INTERVAL_TOLERANCE_MS = 60_000   # 1 min, >>30ms de jitter max observé, <<2h (plus petit régime)


def previous_in_sorted(ts_sorted: List[int], ts: int) -> Optional[int]:
    idx = bisect.bisect_left(ts_sorted, ts)
    if idx == 0:
        return None
    return ts_sorted[idx - 1]


def interval_regime_hours(delta_ms: Optional[int]) -> Optional[int]:
    if delta_ms is None:
        return None
    for h in ALLOWED_INTERVAL_HOURS:
        if abs(delta_ms - h * 3_600_000) <= INTERVAL_TOLERANCE_MS:
            return h
    return None   # irrégulier / inconnu — jamais forcé dans un régime le plus proche


def build_primary_panel(symbol: str, binance_funding: Dict[int, dict],
                        bybit_funding: Dict[int, dict], markprice_ts_set: set,
                        *, tolerance_ms: int = TOLERANCE_MS_DEFAULT) -> List[dict]:
    """Opération B (admission au panel primaire) — strictement bornée au
    préenregistrement, PAS de statistique économique calculée ici (pas de
    quantile, pas de drawdown, pas de NW-t, pas de bootstrap)."""
    b_ts_sorted = sorted(t for t in binance_funding if SIGNAL_START_MS <= t < SIGNAL_END_MS)
    y_ts_sorted = sorted(t for t in bybit_funding if SIGNAL_START_MS <= t < SIGNAL_END_MS)
    m = mutual_one_to_one_match(b_ts_sorted, y_ts_sorted, tolerance_ms=tolerance_ms)
    matched = set(m["matches"])
    unmatched_or_ambiguous = ({(b, None) for b in m["unmatched_binance"] + m["ambiguous_binance"]}
                              | {(None, y) for y in m["unmatched_bybit"] + m["ambiguous_bybit"]})

    rows: List[dict] = []

    def _rate(d: dict, key: str) -> Optional[float]:
        try:
            return float(d[key])
        except (KeyError, TypeError, ValueError):
            return None

    for b_ts, y_ts in sorted(matched):
        row = {"symbol": symbol, "binance_raw_timestamp": b_ts, "bybit_raw_timestamp": y_ts,
              "timestamp_offset_ms": y_ts - b_ts}
        pair_available_at = max(b_ts, y_ts)
        row["pair_available_at"] = pair_available_at
        row["canonical_settlement_timestamp"] = pair_available_at

        b_prev = previous_in_sorted(b_ts_sorted, b_ts)
        y_prev = previous_in_sorted(y_ts_sorted, y_ts)
        row["binance_previous_settlement"] = b_prev
        row["bybit_previous_settlement"] = y_prev
        b_interval = interval_regime_hours(b_ts - b_prev if b_prev is not None else None)
        y_interval = interval_regime_hours(y_ts - y_prev if y_prev is not None else None)
        row["binance_interval_hours"] = b_interval
        row["bybit_interval_hours"] = y_interval

        rate_b = _rate(binance_funding[b_ts], "fundingRate")
        rate_y = _rate(bybit_funding[y_ts], "fundingRate")
        row["funding_rate_binance_raw"] = rate_b
        row["funding_rate_bybit_raw"] = rate_y
        row["raw_dispersion"] = (abs(rate_b - rate_y) if rate_b is not None
                                 and rate_y is not None else None)

        decision_ts = decision_timestamp_for(pair_available_at)
        row["decision_timestamp"] = decision_ts

        reason = None
        if rate_b is None or rate_y is None:
            reason = "missing_funding_leg"
        elif b_prev is None or y_prev is None:
            reason = "missing_previous_settlement"
        elif b_interval is None or y_interval is None:
            reason = "irregular_interval"
        elif b_interval != y_interval:
            reason = "interval_mismatch"
        elif not window_bars_present(decision_ts, markprice_ts_set):
            reason = "incomplete_forward_window"
        row["eligible_primary"] = reason is None
        row["primary_rejection_reason"] = reason
        rows.append(row)

    for b_ts, y_ts in sorted(unmatched_or_ambiguous, key=lambda p: p[0] or p[1]):
        reason = "ambiguous_match" if (b_ts, y_ts) in {(b, None) for b in m["ambiguous_binance"]} \
                or (b_ts, y_ts) in {(None, y) for y in m["ambiguous_bybit"]} else "unmatched_timestamp"
        rows.append({"symbol": symbol, "binance_raw_timestamp": b_ts, "bybit_raw_timestamp": y_ts,
                    "eligible_primary": False, "primary_rejection_reason": reason})
    return rows


def sha256_of(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def main() -> None:
    NORM.mkdir(parents=True, exist_ok=True)
    MANIFESTS.mkdir(parents=True, exist_ok=True)

    cadence_reports = {}
    unique_by_venue_symbol: Dict[Tuple[str, str], Dict[int, dict]] = {}
    for symbol in SYMBOLS:
        for venue in ("binance", "bybit"):
            rep, unique = cadence_report_funding(venue, symbol)
            cadence_reports[f"{venue}/{symbol}"] = rep
            unique_by_venue_symbol[(venue, symbol)] = unique

    coverage_reports = {}
    for symbol in SYMBOLS:
        cov = cross_venue_coverage(symbol, unique_by_venue_symbol[("binance", symbol)],
                                   unique_by_venue_symbol[("bybit", symbol)])
        if symbol == "SOLUSDT":
            cov["extra_events_diagnosis"] = sol_extra_events_diagnosis(
                cadence_reports["binance/SOLUSDT"]["cadence_regimes"],
                cadence_reports["bybit/SOLUSDT"]["cadence_regimes"])
        coverage_reports[symbol] = cov

    markprice_reports = {}
    markprice_unique = {}
    for symbol in SYMBOLS:
        rep, unique = markprice_coverage(symbol)
        binance_ts_in_window = sorted(
            t for t in unique_by_venue_symbol[("binance", symbol)]
            if SIGNAL_START_MS <= t < SIGNAL_END_MS)
        rep["forward_window_completeness"] = forward_window_completeness(
            binance_ts_in_window, set(unique.keys()))
        markprice_reports[symbol] = rep
        markprice_unique[symbol] = unique

    out = {
        "cadence_reports": cadence_reports,
        "coverage_reports": coverage_reports,
        "markprice_reports": markprice_reports,
    }
    (MANIFESTS / "cadence_report.json").write_text(
        json.dumps(cadence_reports, indent=2, sort_keys=True))
    (MANIFESTS / "coverage_report.json").write_text(
        json.dumps(coverage_reports, indent=2, sort_keys=True))
    (MANIFESTS / "markprice_report.json").write_text(
        json.dumps(markprice_reports, indent=2, sort_keys=True))

    # ── mini-audit (settlement_timestamp_alignment_v1) — diagnostic seul ──
    mini_audits = {}
    for symbol in SYMBOLS:
        b_ts = sorted(t for t in unique_by_venue_symbol[("binance", symbol)]
                      if SIGNAL_START_MS <= t < SIGNAL_END_MS)
        y_ts = sorted(t for t in unique_by_venue_symbol[("bybit", symbol)]
                      if SIGNAL_START_MS <= t < SIGNAL_END_MS)
        mini_audits[symbol] = run_mini_audit(symbol, b_ts, y_ts)
    (MANIFESTS / "mini_audit_report.json").write_text(
        json.dumps(mini_audits, indent=2, sort_keys=True, default=str))

    # ── panel primaire (opération B, strictement bornée au préenregistrement) ──
    panel_reports = {}
    all_panel_rows = []
    for symbol in SYMBOLS:
        rows = build_primary_panel(symbol, unique_by_venue_symbol[("binance", symbol)],
                                   unique_by_venue_symbol[("bybit", symbol)],
                                   set(markprice_unique[symbol].keys()))
        all_panel_rows.extend(rows)
        by_reason = Counter(r.get("primary_rejection_reason") for r in rows if not r["eligible_primary"])
        panel_reports[symbol] = {
            "n_pairs_considered": len(rows),
            "n_eligible_primary": sum(1 for r in rows if r["eligible_primary"]),
            "rejections_by_reason": dict(by_reason),
        }
    (MANIFESTS / "primary_panel_report.json").write_text(
        json.dumps(panel_reports, indent=2, sort_keys=True, default=str))

    import pandas as pd
    NORM.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_panel_rows).to_parquet(NORM / "panel.parquet", index=False)

    funding_rows = []
    for symbol in SYMBOLS:
        for venue in ("binance", "bybit"):
            for ts, r in sorted(unique_by_venue_symbol[(venue, symbol)].items()):
                if not (SIGNAL_START_MS <= ts < SIGNAL_END_MS):
                    continue
                funding_rows.append({"venue": venue, "symbol": symbol, "settlement_ts": ts,
                                     "funding_rate_raw": r.get("fundingRate")})
    pd.DataFrame(funding_rows).to_parquet(NORM / "funding.parquet", index=False)

    price_rows = []
    for symbol in SYMBOLS:
        for ts in sorted(markprice_unique[symbol]):
            if PRICE_START_MS <= ts < PRICE_END_MS:
                price_rows.append({"symbol": symbol, "open_time": ts,
                                   "close": markprice_unique[symbol][ts][4]})
    pd.DataFrame(price_rows).to_parquet(NORM / "prices.parquet", index=False)

    raw_file_hashes = []
    for f in sorted(RAW.rglob("*.json")):
        raw_file_hashes.append((str(f.relative_to(RAW)), hashlib.sha256(f.read_bytes()).hexdigest()))

    eligible_rows = [
        {k: v for k, v in r.items()
         if k not in ("eligible_primary", "primary_rejection_reason")}
        for r in all_panel_rows if r["eligible_primary"]]
    eligible_rows_sorted = sorted(eligible_rows, key=str)

    hashes = {
        "raw_envelope_manifest_hash": sha256_of(raw_file_hashes),
        "semantic_raw_content_hash": sha256_of(
            {f"{v}/{s}": sorted(unique_by_venue_symbol[(v, s)].keys())
             for (v, s) in unique_by_venue_symbol}),
        "normalized_funding_hash": sha256_of(funding_rows),
        "normalized_mark_price_hash": sha256_of(price_rows),
        "cross_venue_intersection_hash": sha256_of(coverage_reports),
        "coverage_report_hash": sha256_of(coverage_reports),
        "cadence_report_hash": sha256_of(cadence_reports),
        "markprice_report_hash": sha256_of(markprice_reports),
        "mini_audit_hash": sha256_of(mini_audits),
        "primary_panel_report_hash": sha256_of(panel_reports),
        "analysis_input_hash": sha256_of(eligible_rows_sorted),
    }
    (MANIFESTS / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True))
    print(json.dumps(hashes, indent=2))
    print("\nOK — voir", MANIFESTS)


if __name__ == "__main__":
    main()
