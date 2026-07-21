"""
tests/test_normalize_stress_gate_dispersion_v2.py
─────────────────────────────────────────────────────────────────────────────
Opération A (normaliser/diagnostiquer) uniquement — synthétique, aucune
donnée réelle nécessaire. Pas de statistique économique testée ici (pas de
quantile, drawdown, NW-t, bootstrap — hors scope de ce module).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import scripts.normalize_stress_gate_dispersion_v2 as N


def test_dedupe_exact_vs_conflicting():
    rows = [
        {"fundingTime": 1000, "fundingRate": "0.0001"},
        {"fundingTime": 1000, "fundingRate": "0.0001"},   # doublon EXACT
        {"fundingTime": 2000, "fundingRate": "0.0002"},
        {"fundingTime": 2000, "fundingRate": "0.9999"},   # doublon CONFLICTUEL
    ]
    diag = N.dedupe_and_diagnose(rows, "fundingTime")
    assert diag["rows_raw"] == 4
    assert diag["rows_unique"] == 2
    assert diag["duplicate_exact"] == 1
    assert diag["duplicate_conflicting"] == [2000]


def test_interval_histogram_absorbs_ms_jitter_not_real_cadence():
    HOUR = 3_600_000
    ts = [0, 8 * HOUR + 20, 16 * HOUR - 15, 24 * HOUR]     # ~8h avec jitter ms
    hist = N.interval_histogram(ts)
    assert len(hist) == 1
    assert hist[0]["delta_hours"] == 8.0
    assert hist[0]["n_occurrences"] == 3


def test_interval_histogram_distinguishes_real_cadence_change():
    HOUR = 3_600_000
    ts = [0, 8 * HOUR, 10 * HOUR, 12 * HOUR, 20 * HOUR]    # 8h, 2h, 2h, 8h
    hist = {h["delta_hours"]: h["n_occurrences"] for h in N.interval_histogram(ts)}
    assert hist[8.0] == 2
    assert hist[2.0] == 2


def test_segment_cadence_regimes_detects_contiguous_regime_change():
    HOUR = 3_600_000
    deltas = [8, 8, 8] + [2, 2, 2] + [8, 8]   # cumulés, jamais de trou implicite
    ts = [0]
    for d in deltas:
        ts.append(ts[-1] + d * HOUR)
    segments = N.segment_cadence_regimes(ts)
    cadences = [s["cadence_hours"] for s in segments]
    assert cadences == [8, 2, 8]


def test_cross_venue_coverage_exact_join_no_asof():
    binance = {1000: {}, 2000: {}, 3000: {}}
    bybit = {2000: {}, 3000: {}, 4000: {}}
    cov = N.cross_venue_coverage(
        "TEST", {k: v for k, v in binance.items()}, {k: v for k, v in bybit.items()})
    assert cov["exact_timestamp_intersections"] == 0   # hors fenêtre signal (timestamps trop petits)


def test_cross_venue_coverage_within_signal_window():
    a = N.SIGNAL_START_MS + 1000
    b = N.SIGNAL_START_MS + 2000
    c = N.SIGNAL_START_MS + 3000
    binance = {a: {}, b: {}}
    bybit = {b: {}, c: {}}
    cov = N.cross_venue_coverage("TEST", binance, bybit)
    assert cov["exact_timestamp_intersections"] == 1
    assert cov["binance_only_events"] == 1
    assert cov["bybit_only_events"] == 1


def test_forward_window_completeness_rejects_incomplete_window_no_interpolation():
    STEP = N.BAR_STEP_MS
    sig_ts = N.SIGNAL_START_MS
    decision_ts = ((sig_ts // STEP) + 1) * STEP
    full_window = set(range(decision_ts, decision_ts + N.FORWARD_HORIZON_MS, STEP))
    # actif 1 : fenêtre complète
    result_complete = N.forward_window_completeness([sig_ts], full_window)
    assert result_complete["signals_complete_window"] == 1
    assert result_complete["signals_rejected_incomplete_window"] == 0
    # actif 2 : un trou au milieu -> rejeté, jamais interpolé
    holed = full_window - {sorted(full_window)[len(full_window) // 2]}
    result_holed = N.forward_window_completeness([sig_ts], holed)
    assert result_holed["signals_complete_window"] == 0
    assert result_holed["signals_rejected_incomplete_window"] == 1


def test_longest_gap_and_coverage_by_year():
    HOUR = 3_600_000
    ts = [N.SIGNAL_START_MS, N.SIGNAL_START_MS + HOUR, N.SIGNAL_START_MS + 100 * HOUR]
    gap = N.longest_gap(ts)
    assert gap["gap_hours"] == 99.0
    years = N.coverage_by_year(ts)
    assert sum(years.values()) == len(ts)
