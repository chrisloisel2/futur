"""h2_event_classifier: impediments are tested before structure, and a missing field is never a favourable default."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import h2_event_classifier as K  # noqa: E402


def ev(**kw):
    base = {"event_id": "e", "symbol": "ABCUSDT", "asset": "ABC", "first_bar_ts": "2025-05-02T08:30:00+00:00",
            "announced_start_ts": "2025-05-02T08:30:00+00:00", "market_state_core_complete": True, "missing_requires_provider": False,
            "actual_fee_known": True, "capacity_known": True, "venue_precedence": "TRUE_BINANCE_PERP_FIRST", "binance_spot_existed_before": False}
    base.update(kw); return base


def test_the_eight_classes_split_into_impediments_and_structure():
    assert set(K.ALL_CLASSES) == set(K.BLOCKING) | set(K.STRUCTURAL) and len(K.ALL_CLASSES) == 8
    assert K.UNKNOWN_PRECEDENCE in K.BLOCKING and K.TRUE_BINANCE_PERP_FIRST in K.STRUCTURAL


def test_a_clean_event_reaches_a_structural_class():
    c = K.classify(ev())
    assert c["classification"] == K.TRUE_BINANCE_PERP_FIRST and c["blocking"] is False
    assert K.classify(ev(venue_precedence="OTHER_VENUE_FIRST"))["classification"] == K.OTHER_VENUE_FIRST
    assert K.classify(ev(binance_spot_existed_before=True, venue_precedence="NO_OTHER_VENUE"))["classification"] == K.BINANCE_SPOT_FIRST


def test_impediments_are_tested_before_structure_and_in_order():
    assert K.classify(ev(first_bar_ts=None))["classification"] == K.BAD_TIMESTAMP
    assert K.classify(ev(announced_start_ts="2025-05-02T06:00:00+00:00"))["classification"] == K.BAD_TIMESTAMP     # 150 min d'ecart
    assert K.classify(ev(announced_start_ts=None))["classification"] == K.BAD_TIMESTAMP                            # rien pour corroborer
    assert K.classify(ev(market_state_core_complete=False))["classification"] == K.INSUFFICIENT_MARKET_STATE
    assert K.classify(ev(market_state_core_complete=False, missing_requires_provider=True))["classification"] == K.PROVIDER_NEEDED
    assert K.classify(ev(missing_requires_provider=True))["classification"] == K.PROVIDER_NEEDED
    assert K.classify(ev(actual_fee_known=False))["classification"] == K.INSUFFICIENT_EXECUTION_DATA
    assert K.classify(ev(capacity_known=False))["classification"] == K.INSUFFICIENT_EXECUTION_DATA
    assert K.classify(ev(venue_precedence=None))["classification"] == K.UNKNOWN_PRECEDENCE
    # un empechement anterieur l'emporte : pas de mauvais horodatage masque par un manque de cout
    assert K.classify(ev(first_bar_ts=None, actual_fee_known=False))["classification"] == K.BAD_TIMESTAMP


def test_a_small_timestamp_gap_is_tolerated_and_reported():
    c = K.classify(ev(announced_start_ts="2025-05-02T08:25:00+00:00"))
    assert c["classification"] == K.TRUE_BINANCE_PERP_FIRST and c["timestamp_gap_min"] == 5.0


def test_published_fees_can_be_accepted_explicitly_but_capacity_still_blocks():
    assert K.classify(ev(actual_fee_known=False), require_actual_fees=False)["classification"] == K.TRUE_BINANCE_PERP_FIRST
    assert K.classify(ev(actual_fee_known=False, capacity_known=False), require_actual_fees=False)["classification"] == K.INSUFFICIENT_EXECUTION_DATA


def test_summary_counts_and_eligibility():
    cl = [K.classify(ev()), K.classify(ev(actual_fee_known=False)), K.classify(ev(venue_precedence=None))]
    s = K.summarise(cl)
    assert s["n"] == 3 and s["eligible"] == 1 and s["blocked"] == 2 and len(K.eligible(cl)) == 1
    assert s["dominant_blocker"] in (K.INSUFFICIENT_EXECUTION_DATA, K.UNKNOWN_PRECEDENCE)
