import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from data_lake.indices import h2_population_labels as L


def test_lead_buckets():
    assert L.lead_bucket(None) == "unknown" and L.lead_bucket(0.01) == "< 1h" and L.lead_bucket(0.5) == "1h-24h"
    assert L.lead_bucket(3) == "1d-7d" and L.lead_bucket(20) == "7d-30d" and L.lead_bucket(100) == "30d-180d" and L.lead_bucket(500) == ">180d"


def test_populations_by_first_venue_and_precedence():
    assert L.population("OTHER_VENUE_FIRST", "mexc", None, False) == L.POP_MEXC
    assert L.population("OTHER_VENUE_FIRST", "okx", None, False) == L.POP_OKX
    assert L.population("OTHER_VENUE_FIRST", "bybit", None, False) == L.POP_BYBIT
    assert L.population("OTHER_VENUE_FIRST", "kucoin", None, False) == L.POP_KUCOIN
    assert L.population("OTHER_VENUE_FIRST", "somewhere", None, False) == L.POP_OTHER
    assert L.population("BINANCE_FIRST", None, None, False) == L.POP_TRUE_FIRST
    assert L.population("NO_OTHER_VENUE", None, None, False) == L.POP_TRUE_FIRST
    assert L.population("UNKNOWN_PRECEDENCE", None, ["gate"], False) == L.POP_GATE_UNDATED
    assert L.population("UNKNOWN_PRECEDENCE", None, ["gate", "bybit"], False) == L.POP_UNKNOWN
    assert L.population(None, None, None, False) == L.POP_UNKNOWN
    assert L.population("OTHER_VENUE_FIRST", "mexc", None, True) == L.POP_BINANCE_SPOT       # le spot Binance prime


def test_blockers_are_ordered_and_a_missing_field_blocks():
    ev = {"timestamp_bad": True, "provider_needed": True, "capacity_measured": False, "actual_fee_known": False}
    assert L.blockers(ev) == [L.BLK_TIMESTAMP, L.BLK_PROVIDER, L.BLK_CAPACITY, L.BLK_COST]
    assert L.blockers({}) == [L.BLK_CAPACITY, L.BLK_COST]                                # rien de fourni = rien de connu
    assert L.blockers({"capacity_measured": True, "actual_fee_known": False}, require_actual_fees=False) == []


def test_label_and_summary():
    ev = {"venue_precedence": "OTHER_VENUE_FIRST", "first_venue": "mexc", "lead_days": 11.0, "capacity_measured": True, "actual_fee_known": False}
    lab = L.label(ev)
    assert lab["population"] == L.POP_MEXC and lab["blockers"] == [L.BLK_COST] and lab["class"] == L.BLK_COST and lab["clean"] is False and lab["lead_time_bucket"] == "7d-30d"
    s = L.summarise([lab, L.label({"venue_precedence": "NO_OTHER_VENUE", "capacity_measured": True, "actual_fee_known": True})])
    assert s["answers"]["2_mexc_first"] == 1 and s["answers"]["1_true_first_listings"] == 1 and s["answers"]["5_blocked_only_by_cost"] == 1 and s["clean_now"] == 1
    assert s["answers"]["7_clean_per_population_if_cost_and_capacity_lifted"] == {L.POP_MEXC: 1, L.POP_TRUE_FIRST: 1}
