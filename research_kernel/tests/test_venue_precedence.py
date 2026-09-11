"""venue_precedence: an unknown stays an unknown, and a multiplier prefix is not an asset identity."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import venue_precedence as VP  # noqa: E402

LAUNCH = "2025-05-02T08:30:00+00:00"


def inst(venue, market, base, ts, symbol=None):
    return {"venue": venue, "market_type": market, "symbol": symbol or (base + "USDT"), "base": base, "quote": "USDT",
            "status": "trading", "first_listed_ts": ts, "first_listed_source": "listTime" if ts else None, "raw_hash": "h"}


def test_multiplier_prefixes_are_not_part_of_the_asset():
    assert VP.normalise_base("1000PEPE") == "PEPE" and VP.normalise_base("1000000BOB") == "BOB"
    assert VP.normalise_base("1MBABYDOGE") == "BABYDOGE" and VP.normalise_base("PEPE") == "PEPE"
    assert VP.normalise_base("1INCH") == "1INCH"                 # un chiffre qui fait partie du nom n'est pas un multiplicateur
    assert VP.normalise_base("") == ""


def test_a_dated_earlier_listing_settles_it():
    by = VP.index_by_base([inst("mexc", "spot", "PEPE", "2025-04-01T00:00:00+00:00")])
    d = VP.decide("1000PEPE", LAUNCH, by)
    assert d["classification"] == VP.OTHER_VENUE_FIRST and d["confidence"] == "high" and d["first_elsewhere_venue"] == "mexc"
    assert round(d["lead_days"]) == 31 and "mexc" in d["evidence"]


def test_all_dated_later_means_binance_first():
    by = VP.index_by_base([inst("okx", "spot", "ABC", "2025-06-01T00:00:00+00:00")])
    d = VP.decide("ABC", LAUNCH, by)
    assert d["classification"] == VP.BINANCE_FIRST and d["confidence"] == "high" and d["n_dated_after"] == 1


def test_an_undated_market_is_never_counted_as_binance_first():
    by = VP.index_by_base([inst("gate", "spot", "ABC", None)])
    d = VP.decide("ABC", LAUNCH, by)
    assert d["classification"] == VP.UNKNOWN_PRECEDENCE and d["undated_venues"] == ["gate"] and d["confidence"] == "low"
    d2 = VP.decide("ABC", LAUNCH, by, announcement_evidence="bybit listing announced 2025-04-01")
    assert d2["classification"] == VP.UNKNOWN_PRECEDENCE and d2["confidence"] == "medium"   # une annonce renforce sans trancher


def test_no_instrument_anywhere():
    d = VP.decide("NOPE", LAUNCH, {})
    assert d["classification"] == VP.NO_OTHER_VENUE and d["n_instruments"] == 0
    d2 = VP.decide("NOPE", LAUNCH, {}, announcement_evidence="okx listing announced 2025-01-01")
    assert d2["classification"] == VP.UNKNOWN_PRECEDENCE                                    # annonce sans instrument : incoherent, donc inconnu


def test_earliest_venue_wins_and_summary_counts():
    by = VP.index_by_base([inst("mexc", "spot", "ABC", "2025-04-20T00:00:00+00:00"), inst("okx", "spot", "ABC", "2025-03-01T00:00:00+00:00")])
    d = VP.decide("ABC", LAUNCH, by)
    assert d["first_elsewhere_venue"] == "okx" and d["n_dated_before"] == 2
    s = VP.summarise([d, VP.decide("NOPE", LAUNCH, {}), VP.decide("X", LAUNCH, VP.index_by_base([inst("gate", "spot", "X", None)]))])
    assert s["n"] == 3 and s["known"] == 2 and s["unknown"] == 1 and s["first_venue"] == {"okx": 1}
