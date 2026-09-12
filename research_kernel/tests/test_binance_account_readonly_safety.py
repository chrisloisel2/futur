"""The read-only client's safety properties, each one verified on behaviour or on the source itself."""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import binance_account_readonly as RO  # noqa: E402
from data_lake.collectors import account_execution_reality as A  # noqa: E402

SRC = ROOT / "data_lake" / "collectors" / "binance_account_readonly.py"
PRESCRIBED = {"/fapi/v1/commissionRate", "/fapi/v1/leverageBracket", "/fapi/v2/account", "/api/v3/account", "/fapi/v1/income", "/fapi/v1/userTrades", "/sapi/v1/margin/allPairs"}


def _code_only() -> str:
    src = SRC.read_text(); tree = ast.parse(src); strings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) and isinstance(node.value.value, str):
            strings.add(node.value.value)
    out = src
    for s in strings:
        out = out.replace(s, "")
    return "\n".join(l.split("#")[0] for l in out.splitlines())


def test_whitelist_is_exactly_the_prescribed_set_plus_two_reads():
    assert set(RO.ACCOUNT_ENDPOINTS) == PRESCRIBED
    extras = set(RO.ALLOWED) - PRESCRIBED
    assert extras == {"/fapi/v1/exchangeInfo", "/sapi/v1/account/apiRestrictions"}       # public, et la sonde de securite
    assert RO.ALLOWED["/fapi/v1/exchangeInfo"][1] is False                                # non signe
    assert "/sapi/v1/margin/interestRateHistory" not in RO.ALLOWED


def test_only_get_exists_and_no_mutating_endpoint_is_named():
    code = _code_only()
    for banned in ("POST", "DELETE", "PUT", "PATCH", "/order", "cancel", "transfer", "withdraw/apply", "newOrder"):
        assert banned not in code, banned
    assert 'method="GET"' in SRC.read_text()


def test_a_request_is_never_built_for_a_non_whitelisted_path(monkeypatch):
    called = []
    monkeypatch.setattr(RO, "urlopen", lambda *a, **k: called.append(a) or pytest.fail("request built"))
    cl = RO.ReadOnlyClient()
    for bad in ("/fapi/v1/order", "/sapi/v1/capital/withdraw/apply", "/sapi/v1/asset/transfer", "/fapi/v1/allOpenOrders"):
        with pytest.raises(RO.ReadOnlyViolation):
            cl.get(bad)
    assert called == []


def test_withdrawal_and_transfer_keys_are_refused(monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.setenv(RO.ENV_SECRET, "s" * 20)
    for flag in ("enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer", "enableSpotAndMarginTrading", "enableFutures"):
        cl = RO.ReadOnlyClient()
        monkeypatch.setattr(cl, "_request", lambda path, params=None, _f=flag: {"enableReading": True, _f: True})
        perm = cl.check_permissions()
        assert perm["usable"] is False and flag in perm["granted_permissions"], flag
        assert cl.get("/fapi/v1/userTrades", {"symbol": "X"})["status"] == "refused"


def test_a_key_that_cannot_read_is_refused(monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.setenv(RO.ENV_SECRET, "s" * 20)
    cl = RO.ReadOnlyClient(); monkeypatch.setattr(cl, "_request", lambda path, params=None: {"enableReading": False})
    assert cl.check_permissions()["usable"] is False


def test_account_flags_are_cross_checked():
    assert RO.cross_check_account_flags({"canWithdraw": True, "canTrade": False})["refuse"] is True
    assert RO.cross_check_account_flags({"canWithdraw": False, "canTrade": True})["refuse"] is False


def test_no_credentials_mode_writes_all_three_p11_reports(tmp_path, monkeypatch):
    for v in (RO.ENV_KEY, RO.ENV_SECRET, RO.FALLBACK_ENV[0], RO.FALLBACK_ENV[1]):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(A, "public_constraints", lambda syms: {"status": "ok", "constraints": {}, "missing": list(syms), "universe_size": 0})
    doc = A.collect(out=tmp_path)
    for f in ("ACCOUNT_EXECUTION_REALITY_COLLECTED.md", "ACCOUNT_EXECUTION_REALITY_COLLECTED.json", "H2_H3_COST_CHAIN_STATUS.md", "READONLY_KEY_SAFETY_AUDIT.md"):
        assert (tmp_path / f).exists(), f
    c = json.loads((tmp_path / "ACCOUNT_EXECUTION_REALITY_COLLECTED.json").read_text())
    assert c["mode"] == "no_credentials" and c["cost_chain_status"] == {"H2": "unknown", "H3": "unknown"} and c["no_secrets_stored"] is True
    chain = (tmp_path / "H2_H3_COST_CHAIN_STATUS.md").read_text()
    assert "never serve to promote" in chain


def test_secrets_never_reach_a_report(tmp_path, monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "TOPSECRETKEY00000000"); monkeypatch.setenv(RO.ENV_SECRET, "TOPSECRETSEC00000000")
    monkeypatch.setattr(A, "public_constraints", lambda syms: {"status": "ok", "constraints": {}, "missing": list(syms), "universe_size": 0})
    monkeypatch.setattr(RO, "urlopen", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline")))
    A.collect(out=tmp_path)
    for f in tmp_path.glob("*"):
        assert "TOPSECRET" not in f.read_text()
