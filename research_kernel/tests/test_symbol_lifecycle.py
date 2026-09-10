"""symbol_lifecycle: transitions -> mandatory lifecycle events and capture triggers; baseline never triggers."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import exchange_info_diff as X  # noqa: E402
from data_lake.collectors import symbol_lifecycle as L  # noqa: E402
from data_lake.collectors.market_state_schema import validate  # noqa: E402


def view(**kw):
    base = {"status": "TRADING", "onboard_date": 1789100000000, "contract_type": "PERPETUAL", "margin_asset": "USDT", "quote_asset": "USDT", "base_asset": "NEW", "market_type": "perp", "permissions": [], "filters": {}, "margin_trading_allowed": None}
    base.update(kw); return base


def test_birth_status_and_death_produce_events_and_triggers(tmp_path):
    life = L.SymbolLifecycle(tmp_path / "state.json")
    ev, tr = life.apply("binance_um", X.diff(None, {"OLDUSDT": view(base_asset="OLD")}), 1789000000000)
    assert ev == [] and tr == []                                                          # baseline : rien ne se declenche
    ev, tr = life.apply("binance_um", X.diff({"OLDUSDT": view(base_asset="OLD")}, {"OLDUSDT": view(base_asset="OLD"), "NEWUSDT": view(status="PENDING_TRADING")}), 1789000005000)
    assert ev[0]["event_kind"] == "born" and ev[0]["new_status"] == "PENDING_TRADING" and validate("symbol_lifecycle_event", ev[0])
    assert tr[0]["trigger_type"] == "new_perp_listing" and tr[0]["anticipated"] is True and tr[0]["t0"].startswith("2026-")
    ev, tr = life.apply("binance_um", [{"type": "status_changed", "symbol": "NEWUSDT", "old": "PENDING_TRADING", "new": "TRADING", "cur": view()}], 1789000010000)
    assert ev[0]["event_kind"] == "status_change" and tr[0]["trigger_type"] == "new_perp_listing" and tr[0]["anticipated"] is False
    ev, tr = life.apply("binance_um", [{"type": "status_changed", "symbol": "OLDUSDT", "old": "TRADING", "new": "SETTLING", "cur": view(status="SETTLING", base_asset="OLD")}], 1789000020000)
    assert tr[0]["trigger_type"] == "delisting_detected" and life.state["binance_um|OLDUSDT"]["died_at"]
    ev, tr = life.apply("binance_um", [{"type": "symbol_removed", "symbol": "OLDUSDT", "old": view(status="SETTLING", base_asset="OLD"), "new": None}], 1789000030000)
    assert ev[0]["event_kind"] == "died" and ev[0]["new_status"] == "REMOVED"
    life.save(); again = L.SymbolLifecycle(tmp_path / "state.json"); assert again.state["binance_um|NEWUSDT"]["status"] == "TRADING"


def test_spot_margin_toggle_is_a_shortability_event(tmp_path):
    life = L.SymbolLifecycle(tmp_path / "s.json")
    life.apply("binance_spot", X.diff(None, {"AAAUSDT": view(market_type="spot", contract_type="SPOT", margin_trading_allowed=False)}), 1)
    ev, tr = life.apply("binance_spot", [{"type": "margin_trading_changed", "symbol": "AAAUSDT", "old": False, "new": True, "cur": view(market_type="spot", margin_trading_allowed=True)}], 2)
    assert ev[0]["event_kind"] == "shortability_change" and ev[0]["new_status"] == "True" and tr == []
