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
    """canWithdraw / canTrade sont des capacites du COMPTE, vraies sur tout compte normal quelle que soit la cle :
    enregistrees, jamais un motif de refus (la sonde apiRestrictions, fail-closed, est la seule barriere)."""
    x = RO.cross_check_account_flags({"canWithdraw": True, "canTrade": True, "feeTier": 0})
    assert x["refuse"] is False and x["account_flags"] == {"canWithdraw": True, "canTrade": True} and "informational" in x["note"]


def _probe_ok(cl, monkeypatch, payload=None):
    monkeypatch.setattr(cl, "_request", lambda path, params=None: dict(payload or {"enableReading": True, "ipRestrict": True, "createTime": 1700000000000}))
    return cl.check_permissions()


def test_unknown_permission_flags_refuse_by_default(monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.setenv(RO.ENV_SECRET, "s" * 20)
    cl = RO.ReadOnlyClient(); r = _probe_ok(cl, monkeypatch, {"enableReading": True, "enableFixApiTrade": True})
    assert r["status"] == "refused" and r["granted_permissions"] == ["enableFixApiTrade"] and cl.permissions_ok is False
    cl2 = RO.ReadOnlyClient(); r2 = _probe_ok(cl2, monkeypatch, {"enableReading": True, "enableSomethingBinanceAddsNextYear": True})
    assert r2["status"] == "refused" and "enableSomethingBinanceAddsNextYear" in r2["reason"]
    cl3 = RO.ReadOnlyClient(); r3 = _probe_ok(cl3, monkeypatch, {"enableReading": True, "enableFixReadOnly": True, "ipRestrict": True, "createTime": 1, "tradingAuthorityExpirationTime": -1})
    assert r3["status"] == "ok" and cl3.permissions_ok and r3["restrictions"] == {"enableReading": True, "enableFixReadOnly": True, "ipRestrict": True}   # ni createTime ni expiration
    assert RO.granted_permissions({"enableReading": True, "enableFutures": False, "permitsUniversalTransfer": False}) == []


def test_account_endpoint_before_probe_is_refused(monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.setenv(RO.ENV_SECRET, "s" * 20)
    cl = RO.ReadOnlyClient(); sent = []
    monkeypatch.setattr(RO, "urlopen", lambda req, timeout=None: sent.append(req.full_url) or pytest.fail("a signed account request was sent before the probe"))
    r = cl.get("/fapi/v2/account"); assert r["status"] == "refused" and "before a successful apiRestrictions probe" in r["reason"] and sent == []
    monkeypatch.setattr(RO, "urlopen", lambda req, timeout=None: sent.append(req.full_url) or (_ for _ in ()).throw(ConnectionError("offline")))
    assert cl.get("/fapi/v1/exchangeInfo")["status"] == "error" and sent == ["https://fapi.binance.com/fapi/v1/exchangeInfo"]   # le public reste appelable, sans en-tete de cle
    _probe_ok(cl, monkeypatch); assert cl.permissions_ok is True


def test_redirects_are_not_followed(monkeypatch):
    import http.server, threading
    class H(http.server.BaseHTTPRequestHandler):
        hits = []
        def do_GET(self):
            H.hits.append(self.path)
            if self.path.startswith("/probe"):
                self.send_response(302); self.send_header("Location", "http://%s:%d/elsewhere" % self.server.server_address); self.end_headers()
            else:
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H); t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        from urllib.request import Request
        with pytest.raises(Exception) as ei:
            RO.urlopen(Request("http://127.0.0.1:%d/probe" % srv.server_address[1], headers={"X-MBX-APIKEY": "k"}), timeout=5)
        assert H.hits == ["/probe"] and getattr(ei.value, "code", None) == 302                                  # une seule requete, la redirection est une erreur
    finally:
        srv.shutdown()


def test_error_messages_lose_ip_addresses(monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.setenv(RO.ENV_SECRET, "s" * 20)
    import io
    from urllib.error import HTTPError
    body = json.dumps({"code": -2015, "msg": "Invalid API-key, IP, or permissions for action, request ip: 81.23.4.155"}).encode()
    cl = RO.ReadOnlyClient()
    monkeypatch.setattr(RO, "urlopen", lambda *a, **k: (_ for _ in ()).throw(HTTPError("u", 401, "x", {}, io.BytesIO(body))))
    r = cl.check_permissions()
    assert r["status"] == "error" and "81.23.4.155" not in json.dumps(r) and "<ip>" in r["reason"]
    assert RO.sanitise_error("host 2001:db8::1 refused") == "host <ip> refused" and RO.sanitise_error(None) is None and len(RO.sanitise_error("x" * 500)) == 120


def test_half_set_readonly_pair_never_falls_back(monkeypatch):
    monkeypatch.setenv(RO.ENV_KEY, "k" * 20); monkeypatch.delenv(RO.ENV_SECRET, raising=False)
    monkeypatch.setenv(RO.FALLBACK_ENV[0], "LEGACYTRADINGKEY0000"); monkeypatch.setenv(RO.FALLBACK_ENV[1], "LEGACYTRADINGSEC0000")
    k, s_, var = RO.credentials(); assert k is None and s_ is None and var == "half-set:" + RO.ENV_KEY
    cl = RO.ReadOnlyClient(); monkeypatch.setattr(RO, "urlopen", lambda *a, **k: pytest.fail("a request was sent with half-set credentials"))
    assert cl.check_permissions()["status"] == "refused" and "half-set" in cl.refused_reason and cl.get("/fapi/v2/account")["status"] == "refused"
    monkeypatch.delenv(RO.ENV_KEY); assert RO.credentials()[2] == RO.FALLBACK_ENV[0]                                # repli seulement quand AUCUNE variable read-only n'est posee


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
