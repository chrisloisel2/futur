"""pre_binance_features: strictly pre-t0 by construction; a post-t0 candle is refused, never filtered silently."""
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from data_lake.indices import pre_binance_features as F

D = 86_400_000; H = 3_600_000; T0 = 1_700_000_000_000


def _daily(n, start_close=1.0, step=0.1, qv=1000.0):
    out = []
    for i in range(n):
        c = start_close * (1 + step) ** i
        out.append({"open_time_ms": T0 - (n - i) * D, "open": c / (1 + step), "high": c * 1.05, "low": c * 0.95, "close": c, "volume": 1.0, "quote_volume": qv})
    return out


def test_a_candle_at_or_after_t0_is_refused():
    with pytest.raises(F.PostT0Leak):
        F.strictly_before([{"open_time_ms": T0, "close": 1.0}], T0)
    with pytest.raises(F.PostT0Leak):
        F.compute(_daily(3) + [{"open_time_ms": T0 + D, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "quote_volume": 1}], [], T0, "mexc", 1.0, "x")


def test_returns_are_pre_t0_and_windows_respected():
    f = F.compute(_daily(31, step=0.0), [], T0, "mexc", 30.0, "mexc spot")
    assert f["pre_binance_return_30d"] == 0.0 and f["pre_binance_return_7d"] == 0.0 and f["coverage_status"] == "daily_only"
    f2 = F.compute(_daily(8, step=0.1), [], T0, "mexc", 8.0, "mexc spot")
    assert abs(f2["pre_binance_return_7d"] - (1.1 ** 6 - 1)) < 1e-6                 # 7 j = 6 pas journaliers depuis la 1re bougie de la fenetre
    assert abs(f2["pre_binance_return_30d"] - (1.1 ** 7 - 1)) < 1e-6                # 30 j tronque a l'historique disponible (8 bougies)
    assert f2["pre_binance_volatility_7d"] == 0.0 and f2["pre_binance_pump_score"] is None       # pas constants : volatilite nulle, score indefini


def test_no_history_is_not_collected_and_scores_are_bounded():
    f = F.compute([], [], T0, "mexc", None, "x")
    assert f["coverage_status"] == "not_collected" and f["pre_binance_return_7d"] is None
    flat = F.compute(_daily(8, step=1.0), [], T0, "mexc", 8.0, "x")
    assert flat["pre_binance_pump_score"] is None                                        # rendements constants : volatilite nulle, score indefini
    cands = _daily(8, step=0.1); cands[3]["close"] *= 3.0                                # un pic : volatilite non nulle
    big = F.compute(cands, [], T0, "mexc", 8.0, "x")
    assert -10 <= big["pre_binance_pump_score"] <= 10 and 0 <= big["pre_binance_exhaustion_score"] <= 1


def test_hourly_gives_24h_and_full_coverage():
    hourly = [{"open_time_ms": T0 - (25 - i) * H, "open": 1, "high": 1.1, "low": 0.9, "close": 1.0 + i * 0.01, "volume": 1, "quote_volume": 10.0} for i in range(25)]
    f = F.compute(_daily(8), hourly, T0, "mexc", 8.0, "x")
    assert f["coverage_status"] == "full" and f["pre_binance_return_24h"] is not None and f["pre_binance_volume_24h"] == 240.0


def test_feature_list_is_the_declared_one():
    for k in ("pre_binance_return_30d", "pre_binance_return_24h", "pre_binance_pump_score", "pre_binance_exhaustion_score", "pre_binance_liquidity_proxy", "data_source", "coverage_status"):
        assert k in F.FEATURES
