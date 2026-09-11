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
    assert len(RO.ALLOWED) == 10 and all(isinstance(v, tuple) and len(v) == 3 for v in RO.ALLOWED.values())


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
    acct = A.account_snapshot()
    assert "SECRETKEY1234567890" not in json.dumps(acct, default=str)


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
