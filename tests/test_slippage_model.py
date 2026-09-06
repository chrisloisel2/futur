"""
tests/test_slippage_model.py — modèle de coût d'exécution
(src/institutional/live_alpha_lab/slippage.py) et sonde de spread
(scripts/probe_spread_cross_section.py).

Ce que ces tests protègent : qu'un coût INCONNU se lise comme inconnu. Le
mode d'échec qu'on veut rendre impossible n'est pas « le chiffre est faux »,
c'est « le chiffre manquant a été remplacé en silence par la constante », ce
qui redonne exactement l'illusion de mesure que ce module existe pour lever.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.institutional.live_alpha_lab import slippage as S


def _probes(symbol="ARUSDT", spreads=(6.6,), n_repeat=1):
    rows = []
    t0 = pd.Timestamp("2026-09-06T09:00:00Z")
    for i in range(n_repeat):
        for j, sp in enumerate(spreads):
            rows.append({
                "probe_at": t0 + pd.Timedelta(minutes=15 * (i * len(spreads) + j)),
                "symbol": symbol, "spread_bps": sp,
                "top_bid_notional_usd": 50.0, "top_ask_notional_usd": 56.0,
            })
    return pd.DataFrame(rows)


def test_half_spread_is_half_the_quoted_spread():
    st = S.spread_stats(_probes(spreads=(6.6,)))
    assert st["ARUSDT"].half_spread_median_bps == pytest.approx(3.3)


def test_percentile_is_refused_while_the_sample_is_thin():
    """Un p90 sur trois sondes est un maximum déguisé."""
    st = S.spread_stats(_probes(spreads=(1.0, 2.0, 3.0)))
    assert st["ARUSDT"].n_probes == 3
    assert st["ARUSDT"].thin is True
    assert st["ARUSDT"].half_spread_p90_bps is None
    assert S.slippage_bps("ARUSDT", "MEASURED_P90", st) is None


def test_percentile_appears_once_the_sample_is_large_enough():
    st = S.spread_stats(_probes(spreads=(1.0, 2.0), n_repeat=15))   # 30 sondes
    assert st["ARUSDT"].thin is False
    assert st["ARUSDT"].half_spread_p90_bps is not None


def test_unknown_symbol_returns_none_never_the_constant():
    """Le mode d'échec à interdire : retomber en silence sur 2,0 bps."""
    st = S.spread_stats(_probes())
    assert S.slippage_bps("INCONNUUSDT", "MEASURED_MEDIAN", st) is None
    assert S.roundtrip_cost_bps("INCONNUUSDT", "MEASURED_MEDIAN", st) is None


def test_simulator_and_stress_scenarios_are_symbol_independent():
    assert S.slippage_bps("N_IMPORTE_QUOI", "SIMULATOR") == S.SIMULATOR_SLIPPAGE_BPS
    assert S.slippage_bps("N_IMPORTE_QUOI", "STRESS_BOUND") == S.STRESS_SLIPPAGE_BPS


def test_roundtrip_is_two_legs_of_fee_plus_slippage():
    assert S.roundtrip_cost_bps("X", "SIMULATOR") == pytest.approx(
        2 * (S.TAKER_FEE_BPS + S.SIMULATOR_SLIPPAGE_BPS))
    assert S.roundtrip_cost_bps("X", "SIMULATOR") == pytest.approx(14.0)
    assert S.roundtrip_cost_bps("X", "STRESS_BOUND") == pytest.approx(30.0)


def test_unknown_scenario_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        S.slippage_bps("BTCUSDT", "PAS_UN_SCENARIO")


def test_reprice_subtracts_the_symbol_specific_cost():
    st = S.spread_stats(_probes(symbol="ARUSDT", spreads=(6.6,)))
    outcomes = pd.DataFrame([{"symbol": "ARUSDT", "dec_excess_bps": 100.0}])
    net = S.reprice(outcomes, "MEASURED_MEDIAN", stats=st)
    assert net.iloc[0] == pytest.approx(100.0 - 2 * (5.0 + 3.3))


def test_reprice_leaves_nan_for_a_symbol_it_cannot_price():
    st = S.spread_stats(_probes(symbol="ARUSDT"))
    outcomes = pd.DataFrame([{"symbol": "AUTREUSDT", "dec_excess_bps": 100.0}])
    assert pd.isna(S.reprice(outcomes, "MEASURED_MEDIAN", stats=st).iloc[0])


def test_constants_match_the_simulator_they_claim_to_mirror():
    """Si portfolio.py change ses coûts, ce module doit suivre — sinon il
    re-tarifierait contre une base qui n'est plus celle du PnL live."""
    from src.institutional.live_alpha_lab import portfolio as P
    assert S.TAKER_FEE_BPS == P.TAKER_FEE_BPS
    assert S.SIMULATOR_SLIPPAGE_BPS == P.FIXED_SLIPPAGE_BPS


def test_crossed_or_empty_book_is_dropped_not_recorded_as_zero_spread():
    """Un carnet croisé écrit comme spread nul serait le point le plus
    optimiste de toute la distribution."""
    import scripts.probe_spread_cross_section as probe
    payload = [
        {"symbol": "AUSDT", "bidPrice": "100", "askPrice": "101", "bidQty": "1", "askQty": "1"},
        {"symbol": "BUSDT", "bidPrice": "0", "askPrice": "101", "bidQty": "1", "askQty": "1"},
        {"symbol": "CUSDT", "bidPrice": "102", "askPrice": "101", "bidQty": "1", "askQty": "1"},
    ]

    class _Resp:
        def read(self): return __import__("json").dumps(payload).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _Resp()
    try:
        df = probe.probe({"AUSDT", "BUSDT", "CUSDT"})
    finally:
        urllib.request.urlopen = orig
    assert set(df["symbol"]) == {"AUSDT"}
