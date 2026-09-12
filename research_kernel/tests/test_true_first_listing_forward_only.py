import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from research_kernel.mechanisms import true_first_listing_forward_only as M
from research_kernel.mechanism_spec import MechanismSpec


def test_spec_validates_and_is_forward_only():
    s = MechanismSpec.from_dict(M.spec()); s.validate()
    assert M.STATUS == "FORWARD_ONLY" and s.validation_window["start"] >= "2026-09-12"


def test_historical_verdict_is_refused_even_with_events():
    with pytest.raises(RuntimeError, match="FORWARD_ONLY"):
        M.verdict([{"event_id": "x"}] * 6)
    with pytest.raises(RuntimeError):
        M.verdict()


def test_historical_events_are_described_never_sampled():
    r = M.select([{"event_id": "a", "population": "TRUE_BINANCE_PERP_FIRST"}, {"event_id": "b", "population": "MEXC_FIRST"}])
    assert r["n_historical"] == 1 and r["n_sample"] == 0 and r["sample"] == []


def test_required_events_is_honest_arithmetic():
    assert M.required_events(30.0, 1546.0, 2.33) > 10_000 and M.required_events(300.0, 1546.0, 2.33) < 200
    assert M.MIN_EVENTS_BEFORE_FIRST_LOOK == 80
