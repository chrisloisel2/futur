"""
tests/test_normalize_stress_gate_dispersion_v2.py
─────────────────────────────────────────────────────────────────────────────
Opération A (normaliser/diagnostiquer) uniquement — synthétique, aucune
donnée réelle nécessaire. Pas de statistique économique testée ici (pas de
quantile, drawdown, NW-t, bootstrap — hors scope de ce module).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

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


# ── appariement mutuel un-à-un (amendement settlement_timestamp_alignment_v1) ──

def test_subsecond_jitter_pairs_same_settlement():
    binance = [1000 + 11, 1000 + 8 * 3_600_000 + 16]   # jitter +11ms, +16ms
    bybit = [1000, 1000 + 8 * 3_600_000]               # pile sur la grille
    m = N.mutual_one_to_one_match(binance, bybit)
    assert len(m["matches"]) == 2
    assert set(m["matches"]) == {(binance[0], bybit[0]), (binance[1], bybit[1])}


def test_match_over_one_second_rejected():
    binance = [1000 + 1001]     # 1001ms > tolerance 1000ms
    bybit = [1000]
    m = N.mutual_one_to_one_match(binance, bybit, tolerance_ms=1000)
    assert m["matches"] == []
    assert binance[0] in m["unmatched_binance"]
    assert bybit[0] in m["unmatched_bybit"]


def test_exact_timestamp_still_matches():
    m = N.mutual_one_to_one_match([5000], [5000])
    assert m["matches"] == [(5000, 5000)]


def test_ambiguous_candidate_rejected():
    # un binance à équidistance de deux bybit, tous deux dans la tolérance
    binance = [1_000_000]
    bybit = [1_000_000 - 500, 1_000_000 + 500]
    m = N.mutual_one_to_one_match(binance, bybit, tolerance_ms=1000)
    assert m["matches"] == []
    assert binance[0] in m["ambiguous_binance"]
    assert set(m["ambiguous_bybit"]) == set(bybit)


def test_event_cannot_be_reused():
    # deux binance proches d'un seul bybit -> aucun appariement, jamais le plus proche choisi arbitrairement
    binance = [1_000_000 - 100, 1_000_000 + 100]
    bybit = [1_000_000]
    m = N.mutual_one_to_one_match(binance, bybit, tolerance_ms=1000)
    assert m["matches"] == []
    used = [y for _, y in m["matches"]]
    assert len(used) == len(set(used))   # jamais réutilisé (vide ici, mais invariant vérifié)


def test_matching_is_input_order_invariant():
    binance = [3000, 1000, 2000]
    bybit = [2000 + 10, 1000 - 10, 3000 + 5]
    m1 = N.mutual_one_to_one_match(binance, bybit)
    m2 = N.mutual_one_to_one_match(list(reversed(binance)), list(reversed(bybit)))
    assert sorted(m1["matches"]) == sorted(m2["matches"])


def test_no_asof_or_forward_fill_in_matching():
    """Un événement bybit isolé loin de tout binance ne doit jamais être
    apparié par proximité au-delà de la tolérance, quelle que soit sa
    position dans la série."""
    binance = [0, 10_000_000]
    bybit = [5_000_000]   # équidistant, hors tolérance des deux côtés
    m = N.mutual_one_to_one_match(binance, bybit, tolerance_ms=1000)
    assert m["matches"] == []
    assert bybit[0] in m["unmatched_bybit"]


# ── comparabilité des intervalles + construction du panel primaire ─────────

HOUR = 3_600_000


def _full_markprice_window(decision_ts: int) -> set:
    return set(range(decision_ts, decision_ts + N.FORWARD_HORIZON_MS, N.BAR_STEP_MS))


def _funding_row(rate="0.0001"):
    return {"fundingRate": rate}


def test_equal_8h_intervals_are_primary_eligible():
    t = N.SIGNAL_START_MS + 100 * HOUR
    binance = {t - 8 * HOUR: _funding_row(), t: _funding_row()}
    bybit = {t - 8 * HOUR + 5: _funding_row(), t + 5: _funding_row()}
    decision_ts = N.decision_timestamp_for(max(t, t + 5))
    markprice = _full_markprice_window(decision_ts)
    rows = N.build_primary_panel("TEST", binance, bybit, markprice)
    eligible = [r for r in rows if r["binance_raw_timestamp"] == t]
    assert len(eligible) == 1 and eligible[0]["eligible_primary"] is True
    assert eligible[0]["binance_interval_hours"] == 8
    assert eligible[0]["bybit_interval_hours"] == 8


def test_equal_2h_intervals_are_primary_eligible():
    t = N.SIGNAL_START_MS + 100 * HOUR
    binance = {t - 2 * HOUR: _funding_row(), t: _funding_row()}
    bybit = {t - 2 * HOUR + 5: _funding_row(), t + 5: _funding_row()}
    decision_ts = N.decision_timestamp_for(max(t, t + 5))
    markprice = _full_markprice_window(decision_ts)
    rows = N.build_primary_panel("TEST", binance, bybit, markprice)
    row = [r for r in rows if r["binance_raw_timestamp"] == t][0]
    assert row["eligible_primary"] is True
    assert row["binance_interval_hours"] == row["bybit_interval_hours"] == 2


def test_2h_vs_8h_interval_rejected():
    t = N.SIGNAL_START_MS + 100 * HOUR
    binance = {t - 8 * HOUR: _funding_row(), t: _funding_row()}     # binance: 8h
    bybit = {t - 2 * HOUR + 5: _funding_row(), t + 5: _funding_row()}  # bybit: 2h
    decision_ts = N.decision_timestamp_for(max(t, t + 5))
    markprice = _full_markprice_window(decision_ts)
    rows = N.build_primary_panel("TEST", binance, bybit, markprice)
    row = [r for r in rows if r["binance_raw_timestamp"] == t][0]
    assert row["eligible_primary"] is False
    assert row["primary_rejection_reason"] == "interval_mismatch"


def test_unknown_interval_rejected():
    t = N.SIGNAL_START_MS + 100 * HOUR
    binance = {t - 5 * HOUR: _funding_row(), t: _funding_row()}     # 5h : hors {2,4,8}
    bybit = {t - 5 * HOUR + 5: _funding_row(), t + 5: _funding_row()}
    decision_ts = N.decision_timestamp_for(max(t, t + 5))
    markprice = _full_markprice_window(decision_ts)
    rows = N.build_primary_panel("TEST", binance, bybit, markprice)
    row = [r for r in rows if r["binance_raw_timestamp"] == t][0]
    assert row["eligible_primary"] is False
    assert row["primary_rejection_reason"] == "irregular_interval"


def test_pair_available_at_uses_later_raw_timestamp():
    base = N.SIGNAL_START_MS + 100 * HOUR
    b_ts, y_ts = base, base + 300      # bybit 300ms après binance
    binance = {b_ts - 8 * HOUR: _funding_row(), b_ts: _funding_row()}
    bybit = {y_ts - 8 * HOUR: _funding_row(), y_ts: _funding_row()}
    decision_ts = N.decision_timestamp_for(max(b_ts, y_ts))
    markprice = _full_markprice_window(decision_ts)
    rows = N.build_primary_panel("TEST", binance, bybit, markprice)
    row = [r for r in rows if r["binance_raw_timestamp"] == b_ts][0]
    assert row["pair_available_at"] == y_ts   # le PLUS TARDIF, jamais le plus tôt


def test_canonicalization_does_not_advance_decision_time():
    """decision_timestamp doit toujours être STRICTEMENT postérieur à
    pair_available_at, jamais égal ni antérieur — même si b_ts et y_ts sont
    à quelques centaines de ms d'écart."""
    base = N.SIGNAL_START_MS + 100 * HOUR
    b_ts, y_ts = base, base + 300
    binance = {b_ts - 8 * HOUR: _funding_row(), b_ts: _funding_row()}
    bybit = {y_ts - 8 * HOUR: _funding_row(), y_ts: _funding_row()}
    decision_ts = N.decision_timestamp_for(max(b_ts, y_ts))
    markprice = _full_markprice_window(decision_ts)
    rows = N.build_primary_panel("TEST", binance, bybit, markprice)
    row = [r for r in rows if r["binance_raw_timestamp"] == b_ts][0]
    assert row["decision_timestamp"] > row["pair_available_at"]


def test_mark_price_gap_rejects_incomplete_forward_window():
    t = N.SIGNAL_START_MS + 100 * HOUR
    binance = {t - 8 * HOUR: _funding_row(), t: _funding_row()}
    bybit = {t - 8 * HOUR + 5: _funding_row(), t + 5: _funding_row()}
    decision_ts = N.decision_timestamp_for(max(t, t + 5))
    markprice = _full_markprice_window(decision_ts)
    one_bar = sorted(markprice)[len(markprice) // 2]
    markprice_with_gap = markprice - {one_bar}    # un trou au milieu, jamais interpolé
    rows = N.build_primary_panel("TEST", binance, bybit, markprice_with_gap)
    row = [r for r in rows if r["binance_raw_timestamp"] == t][0]
    assert row["eligible_primary"] is False
    assert row["primary_rejection_reason"] == "incomplete_forward_window"


def test_interval_uses_full_venue_series_not_matched_subset_only():
    """Contrôle explicite demandé en relecture : l'intervalle observé doit
    venir de la série COMPLETE propre à chaque venue (avant appariement),
    jamais de la seule intersection des événements communs — sinon les
    événements Bybit intermédiaires (2h) sans contrepartie Binance (8h)
    disparaîtraient et les deux séries sembleraient artificiellement
    espacées de 8h."""
    base = N.SIGNAL_START_MS + 200 * HOUR
    # Binance : 8h (base, base+8h). Bybit : 2h (base, +2h, +4h, +6h, +8h) —
    # seuls base et base+8h sont appariés à Binance ; +2h/+4h/+6h n'ont pas
    # de contrepartie Binance et doivent rester dans la série bybit propre.
    binance = {base: _funding_row(), base + 8 * HOUR: _funding_row()}
    bybit = {base: _funding_row(), base + 2 * HOUR: _funding_row(),
            base + 4 * HOUR: _funding_row(), base + 6 * HOUR: _funding_row(),
            base + 8 * HOUR: _funding_row()}
    decision_ts = N.decision_timestamp_for(base + 8 * HOUR)
    markprice = _full_markprice_window(decision_ts)
    rows = N.build_primary_panel("TEST", binance, bybit, markprice)
    row = [r for r in rows if r["binance_raw_timestamp"] == base + 8 * HOUR][0]
    # bybit_interval_hours doit être 2h (base+6h -> base+8h), PAS 8h
    # (ce qui arriverait si on ne regardait que les événements communs base/base+8h)
    assert row["bybit_interval_hours"] == 2
    assert row["binance_interval_hours"] == 8
    assert row["eligible_primary"] is False
    assert row["primary_rejection_reason"] == "interval_mismatch"


def test_panel_hash_deterministic():
    t = N.SIGNAL_START_MS + 100 * HOUR
    binance = {t - 8 * HOUR: _funding_row(), t: _funding_row()}
    bybit = {t - 8 * HOUR + 5: _funding_row(), t + 5: _funding_row()}
    decision_ts = N.decision_timestamp_for(max(t, t + 5))
    markprice = _full_markprice_window(decision_ts)
    rows1 = N.build_primary_panel("TEST", binance, bybit, markprice)
    rows2 = N.build_primary_panel("TEST", binance, bybit, markprice)
    assert N.sha256_of(rows1) == N.sha256_of(rows2)


def test_longest_gap_and_coverage_by_year():
    HOUR = 3_600_000
    ts = [N.SIGNAL_START_MS, N.SIGNAL_START_MS + HOUR, N.SIGNAL_START_MS + 100 * HOUR]
    gap = N.longest_gap(ts)
    assert gap["gap_hours"] == 99.0
    years = N.coverage_by_year(ts)
    assert sum(years.values()) == len(ts)
