"""Tests logique pure de l'archiveur positioning (aucun réseau)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.institutional.data.positioning_archiver import (
    ENDPOINTS, UNIVERSE_50, merge_archive, normalize)

PAYLOAD_RATIO = [
    {"symbol": "BTCUSDT", "longAccount": "0.6050", "longShortRatio": "1.5317",
     "shortAccount": "0.3950", "timestamp": 1784315400000},
    {"symbol": "BTCUSDT", "longAccount": "0.6043", "longShortRatio": "1.5275",
     "shortAccount": "0.3957", "timestamp": 1784315700000},
]

PAYLOAD_TAKER = [
    {"buySellRatio": "1.1965", "sellVol": "246.7600", "buyVol": "295.2370",
     "timestamp": 1784315400000},
]


def test_normalize_ratio_types_and_columns():
    df = normalize(PAYLOAD_RATIO, "top_position", "BTCUSDT", "5m")
    assert list(df.columns) == ["timestamp", "symbol", "period",
                                "longAccount", "shortAccount", "longShortRatio"]
    assert str(df["timestamp"].dtype).startswith("datetime64[ns, UTC")
    assert df["longAccount"].dtype == float
    assert df["timestamp"].is_monotonic_increasing
    assert (df["symbol"] == "BTCUSDT").all()


def test_normalize_taker_has_no_symbol_in_payload():
    # takerlongshortRatio ne renvoie pas de champ symbol → injecté par nous
    df = normalize(PAYLOAD_TAKER, "taker_vol", "ETHUSDT", "5m")
    assert (df["symbol"] == "ETHUSDT").all()
    assert df["buyVol"].iloc[0] == 295.237


def test_normalize_empty_payload():
    assert normalize([], "top_account", "BTCUSDT", "5m").empty


def test_merge_dedup_keeps_last():
    a = normalize(PAYLOAD_RATIO, "top_position", "BTCUSDT", "5m")
    # même timestamp, valeur révisée → la nouvelle doit gagner
    revised = [dict(PAYLOAD_RATIO[1], longAccount="0.9999")]
    b = normalize(revised, "top_position", "BTCUSDT", "5m")
    out = merge_archive(a, b)
    assert len(out) == 2
    assert out["longAccount"].iloc[-1] == 0.9999
    assert out["timestamp"].is_monotonic_increasing


def test_merge_from_empty_archive():
    b = normalize(PAYLOAD_RATIO, "top_position", "BTCUSDT", "5m")
    out = merge_archive(None, b)
    assert len(out) == 2


def test_coverage_margin_vs_cadence():
    # invariant opérationnel : un appel (5m × 500) doit couvrir largement
    # la cadence du timer (6 h) — sinon des trous apparaissent
    coverage_h = 5 * 500 / 60
    assert coverage_h > 6 * 4   # marge ≥ 4 cadences manquées


def test_universe_and_endpoints_shape():
    assert len(UNIVERSE_50) == 50
    assert len(set(UNIVERSE_50)) == 50
    assert set(ENDPOINTS) == {"top_position", "top_account",
                              "global_account", "taker_vol"}
