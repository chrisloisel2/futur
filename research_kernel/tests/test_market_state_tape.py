"""market_state_tape: trigger dispatch is deduplicated, cooled down, bounded, and spot symbols are never captured."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import market_state_tape as T  # noqa: E402


def test_dispatcher_rules(tmp_path):
    cfg = dict(T.DEFAULTS); cfg["max_concurrent_captures"] = 1
    d = T.TriggerDispatcher(cfg, {}, log_path=tmp_path / "triggers.jsonl", spawn=False)
    trig = {"trigger_type": "new_perp_listing", "venue": "binance", "symbol": "NEWUSDT", "t0": None, "reason": "t", "market_type": "perp"}
    assert d.should_fire(trig)[0] is True
    r = d.dispatch(trig); assert r["fired"] is True and r["pid"] is None
    assert d.should_fire(trig)[0] is False and "cooldown" in d.should_fire(trig)[1]                 # dedup / cooldown
    assert d.should_fire({**trig, "symbol": "SPOTUSDT", "market_type": "spot"})[0] is False           # spot : jamais de capture WS
    assert d.should_fire({**trig, "trigger_type": "nope"})[0] is False
    assert (tmp_path / "triggers.jsonl").read_text().count("\n") == 1                                  # journalise
