"""exchange_info_diff: pure normalisation and diff; the watcher archives a snapshot only when the view changes."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import exchange_info_diff as X  # noqa: E402


def um(symbols):
    return {"serverTime": 1789000000000, "symbols": [{"symbol": s, "status": st, "onboardDate": ob, "contractType": "PERPETUAL", "marginAsset": "USDT", "quoteAsset": "USDT", "baseAsset": s[:-4], "permissionSets": [["GRID"]],
                                                     "liquidationFee": "0.015", "maintMarginPercent": "2.5", "filters": [{"filterType": "PRICE_FILTER", "tickSize": tick}, {"filterType": "LOT_SIZE", "minQty": "1", "stepSize": "1"}]} for s, st, ob, tick in symbols]}


def test_normalize_and_diff_detect_every_watched_change():
    a = X.normalize(um([("AAAUSDT", "TRADING", 1700000000000, "0.001"), ("BBBUSDT", "PENDING_TRADING", 1789100000000, "0.0001")]), "binance_um")
    b = X.normalize(um([("AAAUSDT", "SETTLING", 1700000000000, "0.001"), ("BBBUSDT", "TRADING", 1789100000000, "0.00001"), ("CCCUSDT", "PENDING_TRADING", 1789200000000, "0.01")]), "binance_um")
    assert a["AAAUSDT"]["market_type"] == "perp" and a["AAAUSDT"]["filters"]["PRICE_FILTER.tickSize"] == "0.001"
    d = X.diff(a, b); types = {(c["type"], c["symbol"]) for c in d}
    assert {("status_changed", "AAAUSDT"), ("status_changed", "BBBUSDT"), ("filters_changed", "BBBUSDT"), ("symbol_added", "CCCUSDT")} <= types
    c = X.diff(b, a); assert ("symbol_removed", "CCCUSDT") in {(x["type"], x["symbol"]) for x in c}
    base = X.diff(None, a); assert all(x["type"] == "symbol_added" and x["baseline"] for x in base)


def test_spot_normalize_keeps_margin_flag():
    sp = {"serverTime": 1, "symbols": [{"symbol": "AAAUSDT", "status": "TRADING", "baseAsset": "AAA", "quoteAsset": "USDT", "isMarginTradingAllowed": True, "isSpotTradingAllowed": True, "permissions": ["SPOT", "MARGIN"], "filters": []}]}
    n = X.normalize(sp, "binance_spot"); assert n["AAAUSDT"]["margin_trading_allowed"] is True and n["AAAUSDT"]["market_type"] == "spot"


def test_watcher_archives_only_on_change(tmp_path):
    w = X.ExchangeInfoWatcher("binance_um", root=tmp_path)
    r1 = w.poll(um([("AAAUSDT", "TRADING", 1, "0.1")])); r2 = w.poll(um([("AAAUSDT", "TRADING", 1, "0.1")])); r3 = w.poll(um([("AAAUSDT", "BREAK", 1, "0.1")]))
    assert r1["changed"] and not r2["changed"] and r3["changed"]
    snaps = list((tmp_path / "venue=binance_um").glob("snapshot_*.json")); assert len(snaps) == 2
    changes = [json.loads(l) for l in (tmp_path / "venue=binance_um" / "changes.jsonl").read_text().splitlines()]
    assert changes[-1]["type"] == "status_changed" and changes[-1]["old"] == "TRADING" and changes[-1]["new"] == "BREAK"
    w2 = X.ExchangeInfoWatcher("binance_um", root=tmp_path); assert w2.prev["AAAUSDT"]["status"] == "BREAK"   # etat persistant
