"""
tests/test_command_center_auth.py — authentification du command center
(frontend_pipeline/auth.py) : login/logout, cookie, garde ASGI (401/302/403),
limitation de débit, fail closed (503) et chemins publics.

Tout est isolé : fichier utilisateurs + secret dans tmp_path (hash PBKDF2
généré ici même), constantes de auth monkeypatchées. Aucun secret réel lu.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend_pipeline import auth  # noqa: E402
from frontend_pipeline.command_center import app  # noqa: E402

ITER = 1000          # rapide en test ; l'algo est le même qu'en prod (200 000)
ADMIN_PW = "adm-pw-test"
GUEST_PW = "gst-pw-test"


def _record(pw: str, role: str) -> dict:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
    return {"algo": "pbkdf2_sha256", "iterations": ITER,
            "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(h).decode(),
            "role": role}


@pytest.fixture
def authcfg(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"schema_version": 1, "users": {
        "adm": _record(ADMIN_PW, "admin"), "gst": _record(GUEST_PW, "guest"),
    }}), encoding="utf-8")
    secret = tmp_path / "secret"
    secret.write_text("ab" * 32, encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users)
    monkeypatch.setattr(auth, "SECRET_FILE", secret)
    monkeypatch.setattr(auth, "_users_cache", {"mtime": None, "users": None})
    monkeypatch.setattr(auth, "_secret_cache", {"mtime": None, "secret": None})
    auth.reset_rate_limits()
    return {"users": users, "secret": secret}


@pytest.fixture
def client(authcfg):
    return TestClient(app)


def _login(client, user, pw, **extra):
    data = {"username": user, "password": pw}
    data.update(extra)
    return client.post("/login", data=data, follow_redirects=False)


# ── login ────────────────────────────────────────────────────────────────────

def test_login_ok_sets_cookie_and_redirects(client):
    r = _login(client, "adm", ADMIN_PW)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    sc = r.headers["set-cookie"]
    assert sc.startswith("cc_session=")
    assert "HttpOnly" in sc and "SameSite=lax" in sc.replace("SameSite=Lax", "SameSite=lax")
    assert "Path=/" in sc
    assert "Secure" not in sc                       # requête http en test
    assert "Max-Age=%d" % auth.MAX_AGE_ADMIN_S in sc


def test_login_guest_shorter_cookie_and_next(client):
    r = _login(client, "gst", GUEST_PW, next="/#LAB")
    assert r.status_code == 303 and r.headers["location"] == "/#LAB"
    assert "Max-Age=%d" % auth.MAX_AGE_GUEST_S in r.headers["set-cookie"]


def test_login_rejects_absolute_next(client):
    for nxt in ("https://evil.example/x", "//evil.example", "/\\evil", "/login?x=1"):
        r = _login(client, "adm", ADMIN_PW, next=nxt)
        assert r.status_code == 303 and r.headers["location"] == "/", nxt


def test_login_secure_when_forwarded_https(client):
    r = client.post("/login", data={"username": "adm", "password": ADMIN_PW},
                    headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert "Secure" in r.headers["set-cookie"]


def test_login_wrong_password(client):
    r = _login(client, "adm", "nope")
    assert r.status_code == 303 and r.headers["location"] == "/login?error=1"
    assert "set-cookie" not in r.headers


def test_login_unknown_user_same_status(client):
    r = _login(client, "ghost", "nope")
    assert r.status_code == 303 and r.headers["location"] == "/login?error=1"


def test_login_json_body_and_accept(client):
    r = client.post("/login", json={"username": "gst", "password": GUEST_PW},
                    headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["role"] == "guest"
    assert "cc_session=" in r.headers["set-cookie"]
    r = client.post("/login", json={"username": "gst", "password": "x"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 401


def test_rate_limit_after_8_failures(client):
    for _ in range(8):
        r = _login(client, "adm", "bad")
        assert r.headers["location"] == "/login?error=1"
    r = _login(client, "adm", "bad")
    assert r.status_code == 303 and r.headers["location"] == "/login?error=rate"
    # même le bon mot de passe est refusé pendant la fenêtre
    r = _login(client, "adm", ADMIN_PW)
    assert r.headers["location"] == "/login?error=rate"
    r = client.post("/login", json={"username": "adm", "password": ADMIN_PW},
                    headers={"Accept": "application/json"})
    assert r.status_code == 429


def test_rate_limit_is_per_ip_first_hop_xff(client):
    for _ in range(8):
        _login(client, "adm", "bad")
    r = client.post("/login", data={"username": "adm", "password": ADMIN_PW},
                    headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}, follow_redirects=False)
    assert r.headers["location"] == "/"       # autre IP → pas limitée


def test_rate_limit_window_expires(client, monkeypatch):
    for _ in range(8):
        _login(client, "adm", "bad")
    assert auth._rate_limited("testclient")
    assert not auth._rate_limited("testclient", now=auth.time.time() + auth.RATE_WINDOW_S + 1)


# ── garde ────────────────────────────────────────────────────────────────────

def test_unauth_api_401_json(client):
    r = client.get("/api/lab/portfolios")
    assert r.status_code == 401 and r.json() == {"detail": "authentification requise"}


def test_unauth_root_redirects_to_login_with_next(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/"
    r = client.get("/foo/bar?x=1", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/foo/bar%3Fx%3D1"


def test_guest_me_and_forbidden_post(client):
    _login(client, "gst", GUEST_PW)
    r = client.get("/api/me")
    assert r.status_code == 200 and r.json() == {"user": "gst", "role": "guest"}
    r = client.post("/api/forecast", json={"symbol": "BTCUSDT", "target": 1.0})
    assert r.status_code == 403 and r.json() == {"detail": "lecture seule (invité)"}
    r = client.delete("/api/forecast/BTCUSDT")
    assert r.status_code == 403
    assert client.get("/api/lab/portfolios").status_code == 200


def test_admin_post_passes_gate(client, monkeypatch):
    _login(client, "adm", ADMIN_PW)
    assert client.get("/api/me").json() == {"user": "adm", "role": "admin"}
    # /api/portfolio/init est une sonde admin-only inoffensive : elle répond
    # toujours 410 (legacy gelé) — donc la garde a laissé passer (pas 401/403).
    r = client.post("/api/portfolio/init")
    assert r.status_code == 410


def test_tampered_or_expired_token_rejected(client):
    secret = auth.load_secret()
    good = auth.make_token("adm", "admin", secret)
    p, s = good.split(".")
    client.cookies.set("cc_session", p + "." + ("A" if s[0] != "A" else "B") + s[1:])
    assert client.get("/api/me").status_code == 401
    client.cookies.set("cc_session", auth.make_token("adm", "admin", secret, now=0.0))
    assert client.get("/api/me").status_code == 401
    client.cookies.set("cc_session", auth.make_token("adm", "admin", b"other-secret-other-secret-00000000"))
    assert client.get("/api/me").status_code == 401
    client.cookies.set("cc_session", good)
    assert client.get("/api/me").status_code == 200


def test_logout_clears_cookie(client):
    _login(client, "adm", ADMIN_PW)
    assert client.get("/api/me").status_code == 200
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    sc = r.headers["set-cookie"]
    assert sc.startswith('cc_session=""') or sc.startswith("cc_session=;")
    assert "Max-Age=0" in sc or "expires=" in sc.lower()
    assert client.get("/api/me").status_code == 401


# ── fail closed / public ─────────────────────────────────────────────────────

def test_missing_users_file_503_everywhere_protected(client, authcfg):
    authcfg["users"].unlink()
    assert client.get("/api/lab/portfolios").status_code == 503
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 503 and r.json() == {"detail": "auth non configurée"}
    r = _login(client, "adm", ADMIN_PW)
    assert r.status_code == 503
    assert client.get("/health").status_code == 200      # public reste public


def test_empty_users_or_missing_secret_503(client, authcfg):
    authcfg["users"].write_text(json.dumps({"schema_version": 1, "users": {}}), encoding="utf-8")
    assert client.get("/api/me").status_code == 503
    authcfg["users"].write_text(json.dumps({"schema_version": 1, "users": {
        "adm": _record(ADMIN_PW, "admin")}}), encoding="utf-8")
    authcfg["secret"].unlink()
    assert client.get("/api/me").status_code == 503


def test_public_paths(client):
    assert client.get("/health").json() == {"ok": True}
    r = client.get("/login")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert client.get("/manifest.webmanifest").status_code == 200
    r = client.get("/sw.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert client.get("/icons/icon-192.png").status_code == 200
    r = client.get("/static/sw.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert client.get("/static/does-not-exist.js").status_code == 404
    r = client.get("/static/..%2Fcommand_center.py", follow_redirects=False)
    assert r.status_code == 404                      # traversée refusée par StaticFiles


def test_public_path_list_is_exactly_the_contract():
    assert auth.PUBLIC_PATHS == {"/login", "/logout", "/manifest.webmanifest", "/sw.js", "/health"}
    assert auth.PUBLIC_PREFIXES == ("/icons/", "/static/")
    assert auth.is_public("/static/js/core.js") and auth.is_public("/icons/x.png")
    assert not auth.is_public("/api/me") and not auth.is_public("/") and not auth.is_public("/staticx")


def test_root_serves_index_with_fallback(client, monkeypatch, tmp_path):
    _login(client, "adm", ADMIN_PW)
    from frontend_pipeline import command_center as cc
    st = tmp_path / "static"
    st.mkdir()
    (st / "command_center.html").write_text("<h1>OLD</h1>", encoding="utf-8")
    monkeypatch.setattr(cc, "STATIC", st)
    r = client.get("/")
    assert r.status_code == 200 and "OLD" in r.text and r.headers["cache-control"] == "no-cache"
    (st / "index.html").write_text("<h1>NEW</h1>", encoding="utf-8")
    assert "NEW" in client.get("/").text


def test_verify_password_constant_shape():
    # utilisateur inconnu → None sans exception ; mauvais enregistrement → None
    assert auth.verify_password("nobody", "x") is None


def test_no_secret_in_tracked_files():
    """Aucun mot de passe/secret de test ou réel ne doit apparaître dans le code."""
    for rel in ("frontend_pipeline/auth.py", "frontend_pipeline/command_center.py",
                "frontend_pipeline/status_api.py"):
        txt = (ROOT / rel).read_text(encoding="utf-8")
        assert "guest123" not in txt and "pbkdf2_sha256\"," not in txt
