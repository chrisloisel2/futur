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
    r = d.dispatch(trig); assert r["fired"] is True and r["pid"] is None and d.state["last_fired"] == {}   # dry run : pas de refroidissement
    class _P:
        pid = 424242
    import types
    real = T.subprocess.Popen; T.subprocess.Popen = lambda *a, **k: _P()
    try:
        d_live = T.TriggerDispatcher(cfg, {}, log_path=tmp_path / "live.jsonl", spawn=True)
        r2 = d_live.dispatch(trig); assert r2["fired"] is True and r2["pid"] == 424242
        ok, why = d_live.should_fire(trig); assert ok is False and ("cooldown" in why or "already active" in why)   # dedup / cooldown apres une vraie capture
    finally:
        T.subprocess.Popen = real
    assert d.should_fire({**trig, "symbol": "SPOTUSDT", "market_type": "spot"})[0] is False           # spot : jamais de capture WS
    assert d.should_fire({**trig, "trigger_type": "nope"})[0] is False
    assert (tmp_path / "triggers.jsonl").read_text().count("\n") == 1                                  # journalise


def test_no_capture_mode_sets_no_cooldown_and_daily_cap_applies(tmp_path):
    cfg = dict(T.DEFAULTS); cfg["daily_caps"] = dict(cfg["daily_caps"], liquidation_burst=1)
    d = T.TriggerDispatcher(cfg, {}, log_path=tmp_path / "t.jsonl", spawn=False)
    trig = {"trigger_type": "liquidation_burst", "venue": "binance", "symbol": "XYZUSDT", "t0": None, "reason": "t", "market_type": "perp"}
    r = d.dispatch(trig); assert r["fired"] is True and "no cooldown" in r["decision"] and d.state["last_fired"] == {}   # dry run : rien de pose
    assert d.should_fire({**trig, "symbol": "BTCUSDT"})[0] is False                                                        # deja couvert 24/7
    d2 = T.TriggerDispatcher(cfg, {"daily_counts": {__import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d"): {"liquidation_burst": 1}}}, log_path=tmp_path / "t2.jsonl", spawn=False)
    ok, why = d2.should_fire(trig); assert ok is False and "daily cap" in why
