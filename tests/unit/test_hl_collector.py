"""
tests/test_hl_collector.py
─────────────────────────────────────────────────────────────────────────────
Parseurs, déduplication et reprise du collecteur Hyperliquid local
(scripts/hl_metaorders_collector.py). Aucun réseau.
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

_spec = importlib.util.spec_from_file_location(
    "hlc", Path(__file__).parents[2] / "scripts" / "hl_metaorders_collector.py")
hlc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hlc)

# échantillon conforme au probe réel du 2026-07-18 (recentTrades / WS trades)
TRADE = {"coin": "BTC", "side": "B", "px": "64007.0", "sz": "0.00359",
         "time": 1784375300770, "hash": "0x394e…", "tid": 2054703958989,
         "users": ["0x49bf112c5f81b70043700bc28f4fee36653cb13e",
                   "0xd90dffcf6a185794bdf81dca1c1c7067bc456789"]}
BOOK = {"coin": "BTC", "time": 1784375301724, "levels": [
    [{"px": "64006.0", "sz": "3.2", "n": 21}, {"px": "64005.0", "sz": "1.0", "n": 2}],
    [{"px": "64007.0", "sz": "2.0", "n": 5}, {"px": "64008.0", "sz": "4.0", "n": 3}]]}


def test_parse_trades_normalizes_and_skips_malformed():
    msg = {"channel": "trades", "data": [TRADE, {"coin": "BTC", "px": "x"}]}
    rows = hlc.parse_trades(msg)
    assert len(rows) == 1
    r = rows[0]
    assert r["px"] == 64007.0 and r["sz"] == 0.00359 and r["tid"] == 2054703958989
    assert r["buyer"].startswith("0x49bf") and r["seller"].startswith("0xd90d")
    assert r["schema_v"] == hlc.SCHEMA_V
    assert hlc.parse_trades({"channel": "autre", "data": [TRADE]}) == []


def test_dedup_new_drops_seen_tids():
    seen = set()
    rows = hlc.parse_trades({"channel": "trades", "data": [TRADE]})
    assert len(hlc.dedup_new(list(rows), seen)) == 1
    assert len(hlc.dedup_new(list(rows), seen)) == 0     # même tid → ignoré


def test_l2_features_spread_and_imbalance():
    f = hlc.l2_features(BOOK, ts_ms=1784375302000)
    assert f["best_bid"] == 64006.0 and f["best_ask"] == 64007.0
    assert 0 < f["spread_bps"] < 1
    assert abs(f["imbalance"] - (f["bid_depth_usd"] - f["ask_depth_usd"])
               / (f["bid_depth_usd"] + f["ask_depth_usd"])) < 1e-12
    assert hlc.l2_features({"coin": "BTC", "levels": [[]]}, 0) is None


def test_parse_ctxs_filters_watched_coins():
    payload = [{"universe": [{"name": "BTC"}, {"name": "ZZZ"}]},
               [{"funding": "0.0000125", "openInterest": "1000.5",
                 "premium": "0.0001", "oraclePx": "64000.0",
                 "markPx": "64010.0", "dayNtlVlm": "5e8"},
                {"funding": "0.01"}]]
    rows = hlc.parse_ctxs(payload, 123, ["BTC"])
    assert len(rows) == 1
    assert rows[0]["funding"] == 1.25e-5 and rows[0]["mark_px"] == 64010.0


def test_detect_twap_users_spacing_rule():
    now = 10_000_000
    def fills(user, spacing_ms, n=5):
        return [{"coin": "BTC", "side": "B", "time_ms": now - i * spacing_ms,
                 "buyer": user, "seller": "0xcp"} for i in range(n)]
    trades = fills("0xtwap", 30_000) + fills("0xburst", 500)
    found = hlc.detect_twap_users(trades, now)
    assert ("0xtwap", "BTC", "B") in found          # espacement ~30 s → TWAP
    assert all(u != "0xburst" for u, _, _ in found)  # rafale 0,5 s → non


def test_flush_and_read_table_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(hlc, "OUT", tmp_path)
    monkeypatch.setattr(hlc, "STATE", tmp_path / "state.json")
    rows = hlc.parse_trades({"channel": "trades", "data": [TRADE]})
    c1 = hlc.Collector(["BTC"])
    c1.buffers["trades"] += rows
    c1.flush()
    # redémarrage : le même trade revient du WS (seen_tids perdu) → nouveau part
    c2 = hlc.Collector(["BTC"])
    c2.buffers["trades"] += hlc.parse_trades({"channel": "trades", "data": [TRADE]})
    c2.flush()
    parts = list(tmp_path.glob("trades/date=*/part-*.parquet"))
    assert len(parts) == 2                            # append-only, jamais réécrit
    df = hlc.read_table("trades", root=tmp_path)
    assert len(df) == 1                               # dédup lecture sur tid
    assert (tmp_path / "state.json").exists()


def test_parse_twap_history_id_fields():
    # forme réelle observée : status = dict imbriqué {'status': 'finished'}
    payload = [{"time": 1784375000000, "status": {"status": "finished"},
                "state": {"coin": "BTC", "side": "B", "sz": "10",
                          "executedSz": "10", "executedNtl": "640000",
                          "minutes": 30}}]
    rows = hlc.parse_twap_history("0xu", payload, 1784375400000)
    assert len(rows) == 1
    r = rows[0]
    assert (r["user"], r["coin"], r["start_ms"]) == ("0xu", "BTC", 1784375000000)
    assert r["minutes"] == 30 and r["executed_ntl"] == 640000.0
    assert r["status"] == "finished"
