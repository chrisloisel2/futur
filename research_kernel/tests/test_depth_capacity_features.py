"""depth_capacity_features: status rules, proxies, and the promise that no return is ever computed."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import depth_capacity_features as DC  # noqa: E402

SOURCE = (ROOT / "data_lake" / "indices" / "depth_capacity_features.py").read_text()


def _snap(res=20, bid20=5000.0, ask20=5000.0, bid100=20000.0, ask100=20000.0, ok=True):
    bid = {100: bid100, 200: bid100 * 1.5, 300: bid100 * 2, 400: bid100 * 2.5, 500: bid100 * 3}
    ask = {100: ask100, 200: ask100 * 1.5, 300: ask100 * 2, 400: ask100 * 2.5, 500: ask100 * 3}
    if res == 20:
        bid[20] = bid20; ask[20] = ask20
    return {"ts_ms": 0, "bid": bid, "ask": ask, "ok": ok, "error": None if ok else "bad", "resolution_bps": res}


def _feat(snap, spread):
    f = DC.snapshot_features(snap, 1.0); f["effective_spread_bps"] = spread; return f


def _code_only(src: str) -> str:
    import ast as _ast
    tree = _ast.parse(src); strings = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Expr) and isinstance(getattr(node, "value", None), _ast.Constant) and isinstance(node.value.value, str):
            strings.add(node.value.value)
    out = src
    for st in strings:
        out = out.replace(st, "")
    return "\n".join(l.split("#")[0] for l in out.splitlines())


def test_no_return_is_computed_anywhere():
    code = _code_only(SOURCE)
    for banned in ("return_bps", "math.log(p", "log(p", "pnl", "sharpe", "t_stat", "tradeable", "tradable"):
        assert banned not in code, banned


def test_status_rules_fine_resolution():
    assert DC.capacity_status(_feat(_snap(), 10.0))[0] == "CAPACITY_OK"
    assert DC.capacity_status(_feat(_snap(bid20=500.0), 10.0))[0] == "DEPTH_TOO_THIN"
    assert DC.capacity_status(_feat(_snap(), 80.0))[0] == "SPREAD_TOO_WIDE"
    assert DC.capacity_status(_feat(_snap(), None))[0] == "UNKNOWN"
    assert DC.capacity_status(_feat(_snap(ok=False), 10.0))[0] == "BAD_BOOK"
    assert DC.capacity_status(_feat(None, 10.0))[0] == "NO_DEPTH"


def test_coarse_resolution_can_only_reject_or_stay_unknown():
    st, score, why = DC.capacity_status(_feat(_snap(res=100), 10.0))
    assert st == "UNKNOWN" and "20 bps" in why[0]                                     # jamais CAPACITY_OK a 1 % seul
    assert DC.capacity_status(_feat(_snap(res=100, bid100=200.0), 10.0))[0] == "DEPTH_TOO_THIN"
    fine = DC.capacity_status(_feat(_snap(), 10.0))[1]; coarse = DC.capacity_status(_feat(_snap(res=100), 10.0))[1]
    assert coarse < fine                                                                # une mesure a 1 % vaut moins


def test_snapshot_features_respect_resolution_and_never_invent_best_quotes():
    f = DC.snapshot_features(_snap(), 1.0)
    assert f["bid_depth_10bps_usd"] is None and f["bid_depth_25bps_usd"] == 5000.0 and f["bid_depth_25bps_is_lower_bound"] is True
    assert f["best_bid"] is None and f["best_ask"] is None and f["spread_bps"] is None and f["mid_price_source"].endswith("(proxy)")
    assert f["sell_slippage_1000_usd_ub_bps"] == 20.0 and 0 < f["sell_slippage_1000_usd_bps"] <= 20.0
    c = DC.snapshot_features(_snap(res=100), 1.0)
    assert c["bid_depth_25bps_usd"] is None and c["bid_depth_20bps_native_usd"] is None and c["bid_depth_100bps_native_usd"] == 20000.0
    assert DC.snapshot_features(None, None)["bid_depth_50bps_usd"] is None


def test_spread_proxy_from_signed_trades():
    t = [(0, 100.0, 1.0, False), (100, 99.0, 1.0, True), (200, 100.0, 1.0, False), (300, 99.0, 1.0, True)]     # ask 100, bid 99
    r = DC.mid_and_spread_proxy(t, 0)
    assert r["n_trades"] == 4 and abs(r["effective_spread_bps"] - 1e4 / 99.5) < 0.01 and r["mid_price"] == 99.5
    assert DC.mid_and_spread_proxy(t, 60_000)["n_trades"] == 0 and DC.mid_and_spread_proxy([], 0)["effective_spread_bps"] is None
    one_sided = [(0, 100.0, 1.0, False)]
    assert DC.mid_and_spread_proxy(one_sided, 0)["effective_spread_bps"] is None                # un seul cote : pas de spread


def test_windows_and_statuses_are_the_declared_ones():
    assert DC.WINDOWS_MIN == (0, 1, 5, 15, 30, 60) and DC.NOTIONALS_USD == (100, 500, 1000)
    assert set(DC.STATUSES) == {"CAPACITY_OK", "SPREAD_TOO_WIDE", "DEPTH_TOO_THIN", "NO_DEPTH", "BAD_BOOK", "UNKNOWN"}
