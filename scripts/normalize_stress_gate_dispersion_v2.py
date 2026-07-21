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

    hashes = {
        "cadence_report_hash": sha256_of(cadence_reports),
        "coverage_report_hash": sha256_of(coverage_reports),
        "markprice_report_hash": sha256_of(markprice_reports),
    }
    (MANIFESTS / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True))
    print(json.dumps(hashes, indent=2))
    print("\nOK — voir", MANIFESTS)


if __name__ == "__main__":
    main()
