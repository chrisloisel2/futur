"""P8: the read-only client cannot place an order by construction, refuses a key that can trade,
and the whole phase runs and reports honestly with no credentials at all."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import account_execution_reality as A  # noqa: E402
from data_lake.collectors import binance_account_readonly as RO  # noqa: E402

SOURCE = (ROOT / "data_lake" / "collectors" / "binance_account_readonly.py").read_text()


def _code_without_docstrings(src: str) -> str:
    """Le code executable seul : les docstrings et commentaires parlent de ce qui n'existe pas."""
    import ast as _ast
    tree = _ast.parse(src)
    strings = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Expr) and isinstance(getattr(node, "value", None), _ast.Constant) and isinstance(node.value.value, str):
            strings.add(node.value.value)
    out = src
    for s in strings:
        out = out.replace(s, "")
    return "\n".join(l.split("#")[0] for l in out.splitlines())


def test_the_module_contains_no_order_path():
    """Structural, not declarative: there is nothing to disable because nothing exists."""
    body = _code_without_docstrings(SOURCE)
    for forbidden in ("/fapi/v1/order", "/api/v3/order", "newOrder", "cancelOrder", "POST", "DELETE", "PUT"):
        assert forbidden not in body, forbidden
    assert all(not p.endswith("/order") for p in RO.ALLOWED)
    assert len(RO.ALLOWED) == 9 and all(isinstance(v, tuple) and len(v) == 3 for v in RO.ALLOWED.values())


def test_any_endpoint_outside_the_whitelist_raises_before_a_request(monkeypatch):
    cl = RO.ReadOnlyClient()
    monkeypatch.setattr(RO, "urlopen", lambda *a, **k: pytest.fail("a request was built for a non-whitelisted endpoint"))
    with pytest.raises(RO.ReadOnlyViolation):
        cl.get("/fapi/v1/order")
    with pytest.raises(RO.ReadOnlyViolation):
        cl._request("/sapi/v1/capital/withdraw/apply")


def test_signed_endpoints_are_inert_without_credentials(monkeypatch):
    monkeypatch.delenv(RO.ENV_KEY, raising=False); monkeypatch.delenv(RO.ENV_SECRET, raising=False)
    monkeypatch.delenv(RO.FALLBACK_ENV[0], raising=False); monkeypatch.delenv(RO.FALLBACK_ENV[1], raising=False)
    monkeypatch.setattr(RO, "urlopen", lambda *a, **k: pytest.fail("a signed request was attempted without a key"))
    cl = RO.ReadOnlyClient()
    assert RO.has_credentials() is False
    assert cl.get("/fapi/v1/commissionRate", {"symbol": "BTCUSDT"})["status"] == "no_credentials"
    assert cl.check_permissions()["usable"] is False


def test_a_key_that_can_trade_is_refused(monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.setenv(RO.ENV_SECRET, "s" * 20)
    cl = RO.ReadOnlyClient()
    monkeypatch.setattr(cl, "_request", lambda path, params=None: {"enableReading": True, "enableFutures": True, "enableWithdrawals": False})
    perm = cl.check_permissions()
    assert perm["usable"] is False and "enableFutures" in perm["granted_permissions"] and cl.refused_reason
    assert cl.get("/fapi/v1/commissionRate", {"symbol": "X"})["status"] == "refused"      # le refus bloque les appels suivants
    ok = RO.ReadOnlyClient(allow_trading_key=True)
    monkeypatch.setattr(ok, "_request", lambda path, params=None: {"enableReading": True, "enableFutures": True})
    assert ok.check_permissions()["usable"] is True                                        # autorisation explicite, et dite


def test_credentials_are_never_exposed(monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "SECRETKEY1234567890"); monkeypatch.setenv(RO.ENV_SECRET, "SECRETSECRET123456")
    r = RO.redact("SECRETKEY1234567890")
    assert "SECRETKEY1234567890" not in r and r.startswith("SEC") and "19 chars" in r
    assert RO.redact(None) == "<absent>"
    monkeypatch.setattr(RO, "urlopen", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline")))      # jamais de reseau dans un test
    acct = A.account_snapshot()
    assert "SECRETKEY1234567890" not in json.dumps(acct, default=str) and "SECRETSECRET123456" not in json.dumps(acct, default=str)


def test_a_refusal_never_leaves_a_phantom_ok_on_later_endpoints(monkeypatch, tmp_path):
    """Sonde refusee -> chaque endpoint de compte porte 'refused' avec la raison, jamais 'ok' avec reason None."""
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.setenv(RO.ENV_SECRET, "s" * 20); monkeypatch.setattr(A, "STORE", tmp_path)
    monkeypatch.setattr(RO.ReadOnlyClient, "_request", lambda self, path, params=None: {"enableReading": True, "enableFutures": True})
    acct = A.account_snapshot()
    assert acct["usable"] is False and all(e["status"] == "refused" and "enableFutures" in (e.get("reason") or "") for e in acct["endpoints"].values())


def test_a_normal_account_with_a_readonly_key_is_collected_not_refused(monkeypatch, tmp_path):
    """canWithdraw est vrai sur un compte normal : la collecte doit aboutir, les frais doivent etre connus, les dumps 0600."""
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.setenv(RO.ENV_SECRET, "s" * 20); monkeypatch.setattr(A, "STORE", tmp_path / "acct")
    payloads = {"/sapi/v1/account/apiRestrictions": {"enableReading": True, "ipRestrict": True, "createTime": 1},
                "/fapi/v1/commissionRate": {"symbol": "BTCUSDT", "makerCommissionRate": "0.000200", "takerCommissionRate": "0.000500"},
                "/fapi/v1/leverageBracket": [{"symbol": "BTCUSDT", "brackets": []}], "/fapi/v2/account": {"feeTier": 0, "canTrade": True, "canWithdraw": True, "assets": []},
                "/api/v3/account": {"makerCommission": 10, "takerCommission": 10, "canWithdraw": True, "balances": []}, "/fapi/v1/income": [], "/fapi/v1/userTrades": [{"id": 1}],
                "/sapi/v1/margin/allPairs": []}
    monkeypatch.setattr(RO.ReadOnlyClient, "_request", lambda self, path, params=None: payloads[path])
    acct = A.account_snapshot()
    assert acct["usable"] is True and acct["actual_fees"] == {"symbol": "BTCUSDT", "maker_bps": 2.0, "taker_bps": 5.0} and acct["fee_tier"] == 0
    assert acct["account_flags"]["spot_account"]["canWithdraw"] is True and acct["endpoints"]["own_fills"]["has_rows"] is True and "n_rows" not in acct["endpoints"]["own_fills"]
    import stat
    assert stat.S_IMODE((tmp_path / "acct" / "spot_account.json").stat().st_mode) == 0o600 and stat.S_IMODE((tmp_path / "acct").stat().st_mode) == 0o700


def test_phase_runs_and_reports_without_credentials(tmp_path, monkeypatch):
    for v in (RO.ENV_KEY, RO.ENV_SECRET, RO.FALLBACK_ENV[0], RO.FALLBACK_ENV[1]):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(A, "OUT", tmp_path)
    monkeypatch.setattr(A, "public_constraints", lambda syms: {"status": "ok", "constraints": {}, "missing": list(syms), "universe_size": 0})
    doc = A.collect()
    a = doc["answers"]
    assert a["1_actual_futures_taker_fee_bps"] is None and a["3_funding_payment_availability"] == "no_credentials"
    assert a["5_h2_h3_cost_assumptions"] == {"H2": "unknown", "H3": "unknown"}             # declare => on ne conclut pas
    assert any("no read-only API key" in t for t in a["6_what_remains_theoretical"])
    assert doc["no_orders"] is True and doc["no_secrets_stored"] is True
    for f in ("ACCOUNT_EXECUTION_REALITY.md", "ACCOUNT_EXECUTION_REALITY.json", "ACCOUNT_FEE_TABLE.md", "ACCOUNT_SYMBOL_CONSTRAINTS.md"):
        assert (tmp_path / f).exists() and (tmp_path / f).read_text().strip()


def test_published_schedule_is_used_as_the_fee_link_when_no_key():
    pub = A.published_vip0()
    assert pub["taker_bps"] == 5.0 and pub["source_class"] == "official"
    costs = A.build_costs(pub, {"endpoints": {}})
    assert costs["fee_provenance"] == "official_published"
    assert all(v["status"] == "unknown" for v in costs["comparisons"].values())            # spread/slippage restent declares


def test_tier_is_inferred_from_spot_commission_and_futures_permission_model_is_diagnosed():
    assert A.infer_tier_from_spot({"maker_bps": 10.0, "taker_bps": 10.0})["tier"] == "VIP0"
    assert A.infer_tier_from_spot({"maker_bps": 9.0, "taker_bps": 10.0})["tier"] == "VIP1"
    assert A.infer_tier_from_spot({"maker_bps": 7.5, "taker_bps": 7.5})["tier"] is None and A.infer_tier_from_spot(None)["tier"] is None
    acct = {"permission_check": {"status": "ok", "restrictions": {"enableReading": True, "enableFutures": False}},
            "endpoints": {"commission_rate": {"endpoint": "GET /fapi/v1/commissionRate", "binance_code": -2015}, "spot_account": {"endpoint": "GET /api/v3/account", "binance_code": None},
                          "funding_income": {"endpoint": "GET /fapi/v1/income", "binance_code": -2015}}}
    assert A.futures_permission_diagnosis(acct) == "futures_read_requires_enable_futures"
    acct["permission_check"]["restrictions"]["enableFutures"] = True; assert A.futures_permission_diagnosis(acct) is None   # avec Enable Futures, -2015 serait autre chose (IP)


def test_capacity_links_build_a_measured_chain_only_when_a_window_is_named(tmp_path):
    cap = {"events": [{"event_id": "e%d" % i, "windows": [{"window_min": 5, "book_ok": True, "effective_spread_bps": 10.0 + i, "sell_slippage_500_usd_bps": 2.0, "buy_slippage_500_usd_bps": 2.0,
                                                             "sell_slippage_500_usd_ub_bps": 100.0, "buy_slippage_500_usd_ub_bps": 100.0}]} for i in range(5)]
                     + [{"event_id": "e9", "windows": [{"window_min": 5, "book_ok": True, "effective_spread_bps": 50.0, "sell_slippage_500_usd_bps": None, "buy_slippage_500_usd_bps": None}]}]}
    p = tmp_path / "cap.json"; p.write_text(json.dumps(cap))
    c = A.capacity_links(5, 500, path=p)
    assert c["n_events"] == 6 and c["n_slippage"] == 5 and c["n_order_exceeds_book"] == 1 and c["spread_bps_median"] == 12.5 and c["slippage_rt_bps_median"] == 4.0 and c["slippage_rt_upper_bound_bps_median"] == 200.0
    assert A.capacity_links(15, 500, path=p) is None
    pub = {"maker_bps": 2.0, "taker_bps": 5.0}; acct = {"spot_fees": {"maker_bps": 10.0, "taker_bps": 10.0}}
    declared = A.build_costs(pub, acct); measured = A.build_costs(pub, acct, c)
    assert declared["fee_provenance"] == "official_published" and "VIP0" in declared["fee_note"] and declared["fee_tier_inferred"]["tier"] == "VIP0"
    if "H2" in declared["comparisons"]:
        assert declared["comparisons"]["H2"]["status"] == "unknown" and declared["comparisons"]["H2"]["weakest_provenance"] == "declared"
        assert measured["comparisons"]["H2"]["status"] in ("confirmed", "contradicted") and measured["comparisons"]["H2"]["weakest_provenance"] == "official_published"
        assert measured["comparisons"]["H2"]["measured_round_trip_bps"] == 2 * 5.0 + 12.5 + 4.0


def test_rebuild_from_store_makes_no_network_call_and_reads_only_the_dumps(tmp_path, monkeypatch):
    monkeypatch.setattr(RO, "urlopen", lambda *a, **k: pytest.fail("network call during --from-store"))
    store = tmp_path / "store"; store.mkdir(); (store / "spot_account.json").write_text(json.dumps({"makerCommission": 10, "takerCommission": 10, "canWithdraw": True, "balances": [{"asset": "X", "free": "1"}]}))
    coll = tmp_path / "COLLECTED.json"; coll.write_text(json.dumps({"key_redacted": "abc…yz (64 chars)", "env_var_used": RO.ENV_KEY, "permission_check": {"status": "ok", "usable": True, "restrictions": {"enableReading": True, "enableFutures": False}, "ip_restricted": False},
                                                                     "endpoints": {"commission_rate": {"status": "error", "endpoint": "GET /fapi/v1/commissionRate", "binance_code": -2015}}}))
    acct = A.snapshot_from_store(store, coll)
    assert acct["rebuilt_from_store"] and acct["has_credentials"] and acct["usable"] and acct["spot_fees"]["taker_bps"] == 10.0 and acct["account_flags"]["spot_account"]["canWithdraw"] is True
    assert "balances" not in json.dumps(acct) and A.futures_permission_diagnosis(acct) == "futures_read_requires_enable_futures"


def test_fee_decision_names_the_window_and_the_h2_chain_leaves_unknown_only_for_it(tmp_path):
    d = A.fee_decision()
    assert d["decision"] == "USE_OFFICIAL_PUBLISHED_VIP0_FUTURES_FEES" and d["schema"] == {"account_actual": False, "official_published": True, "tier_confirmed": "VIP0",
                                                                                          "tier_evidence": d["schema"]["tier_evidence"], "bnb_discount_applied": False, "residual_uncertainty_bps": 0.5}
    assert d["key_policy"]["futures_trading_key"] == "never" and d["cost_chain_window"] == {**d["cost_chain_window"], "hypothesis": "H2", "window_min": 15, "notional_usd": 500}
    pub = {"maker_bps": 2.0, "taker_bps": 5.0}; acct = {"spot_fees": {"maker_bps": 10.0, "taker_bps": 10.0}}
    if A.CAPACITY_FEATURES.exists() and "H2" in A.build_costs(pub, acct, decision={})["comparisons"]:
        with_d = A.build_costs(pub, acct); without = A.build_costs(pub, acct, decision={})
        assert with_d["comparisons"]["H2"]["weakest_provenance"] == "official_published" and with_d["comparisons"]["H2"]["status"] in ("confirmed", "contradicted")
        assert without["comparisons"]["H2"]["status"] == "unknown" and with_d["comparisons"].get("H3", {}).get("status", "unknown") == "unknown"   # H3 : aucune fenetre nommee
        assert with_d["capacity_links_used"]["H2"]["window_min"] == 15 and "H3" not in with_d.get("capacity_links_used", {})
    assert A.fee_decision(tmp_path / "missing.json") == {}
