#!/usr/bin/env python3
"""
frontend_pipeline/auth.py
─────────────────────────────────────────────────────────────────────────────
Authentification du COMMAND CENTER (exposé publiquement via ngrok).

  - utilisateurs : configs/command_center_users.json (gitignoré), PBKDF2-HMAC-
    SHA256 ; surcharge par env COMMAND_CENTER_USERS_FILE
  - secret de session : state/command_center_secret (gitignoré, 64 hex) ;
    surcharge par env COMMAND_CENTER_SECRET (chemin de fichier)
  - jeton : base64url(json {u, r, exp}) + "." + base64url(HMAC-SHA256)
    cookie cc_session, HttpOnly, SameSite=Lax, Path=/, Secure si https
    (7 j admin, 1 j invité)
  - garde ASGI pur : tout est protégé SAUF PUBLIC_PATHS / PUBLIC_PREFIXES ;
    /api/* sans jeton → 401 JSON, ailleurs → 302 /login?next=…
  - rôle : POST/PUT/PATCH/DELETE sous /api réservés à admin (403 JSON)
  - FAIL CLOSED : fichier utilisateurs ou secret absent/vide → 503 partout
    (jamais ouvert)
  - limitation : 8 échecs / 5 min par IP (X-Forwarded-For 1er saut sinon
    client) → /login?error=rate (429 JSON si Accept demande du JSON)

Aucun mot de passe ni secret n'est jamais écrit dans un log ou un fichier
suivi. Compatibilité Python 3.8 (venv hôte) ET 3.11 (conteneur).
"""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"

# Constantes de chemin (module-level → monkeypatchables par les tests).
USERS_FILE = Path(os.environ.get("COMMAND_CENTER_USERS_FILE",
                                 str(ROOT / "configs" / "command_center_users.json")))
SECRET_FILE = Path(os.environ.get("COMMAND_CENTER_SECRET",
                                  str(ROOT / "state" / "command_center_secret")))

COOKIE_NAME = "cc_session"
MAX_AGE_ADMIN_S = 7 * 24 * 3600
MAX_AGE_GUEST_S = 24 * 3600
DEFAULT_ITERATIONS = 200_000
ROLES = ("admin", "guest")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

PUBLIC_PATHS = {"/login", "/logout", "/manifest.webmanifest", "/sw.js", "/health"}
PUBLIC_PREFIXES = ("/icons/", "/static/")

RATE_MAX_FAILURES = 8
RATE_WINDOW_S = 300.0

# Sel factice pour l'utilisateur inconnu : même coût PBKDF2 que pour un vrai
# utilisateur → pas de fuite « cet utilisateur existe » par le temps de réponse.
_DUMMY_SALT = b"\x00" * 16

_users_cache: Dict[str, Any] = {"mtime": None, "users": None}
_secret_cache: Dict[str, Any] = {"mtime": None, "secret": None}
_failures: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)
_lock = threading.Lock()


# ── chargement (fail closed) ─────────────────────────────────────────────────

def _b64d(s: str) -> bytes:
    s = str(s).strip()
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii")) if ("-" in s or "_" in s) \
        else base64.b64decode(s.encode("ascii"))


def load_users() -> Dict[str, dict]:
    """{name: record} ; {} si fichier absent/illisible/vide (→ FAIL CLOSED)."""
    p = Path(USERS_FILE)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _users_cache["mtime"] == mtime and _users_cache["users"] is not None:
            return dict(_users_cache["users"])
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        users_raw = d.get("users") if isinstance(d, dict) else None
    except Exception:
        return {}
    users: Dict[str, dict] = {}
    if isinstance(users_raw, dict):
        for name, rec in users_raw.items():
            if not isinstance(rec, dict):
                continue
            if rec.get("algo") != "pbkdf2_sha256" or not rec.get("salt") or not rec.get("hash"):
                continue
            role = rec.get("role")
            if role not in ROLES:
                continue
            users[str(name)] = rec
    with _lock:
        _users_cache["mtime"] = mtime
        _users_cache["users"] = dict(users)
    return users


def load_secret() -> Optional[bytes]:
    """Octets du secret (contenu du fichier, espaces retirés) ; None si absent/vide."""
    p = Path(SECRET_FILE)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    with _lock:
        if _secret_cache["mtime"] == mtime and _secret_cache["secret"]:
            return _secret_cache["secret"]
    try:
        raw = p.read_text(encoding="utf-8").strip().encode("utf-8")
    except Exception:
        return None
    if len(raw) < 32:
        return None
    with _lock:
        _secret_cache["mtime"] = mtime
        _secret_cache["secret"] = raw
    return raw


def is_configured() -> bool:
    return bool(load_users()) and load_secret() is not None


# ── mots de passe ────────────────────────────────────────────────────────────

def verify_password(username: str, password: str) -> Optional[str]:
    """Rôle si (username, password) valide, sinon None. Temps constant :
    PBKDF2 toujours calculé (sel factice si utilisateur inconnu) et
    hmac.compare_digest pour la comparaison."""
    users = load_users()
    rec = users.get(username)
    if rec is None:
        iterations = DEFAULT_ITERATIONS
        salt = _DUMMY_SALT
        expected = b"\x00" * 32
    else:
        try:
            iterations = int(rec.get("iterations") or DEFAULT_ITERATIONS)
            salt = _b64d(rec["salt"])
            expected = _b64d(rec["hash"])
        except Exception:
            iterations, salt, expected = DEFAULT_ITERATIONS, _DUMMY_SALT, b"\x00" * 32
            rec = None
    iterations = max(1, min(iterations, 5_000_000))
    computed = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, iterations,
                                   dklen=len(expected) or 32)
    ok = hmac.compare_digest(computed, expected)
    if rec is None or not ok:
        return None
    return str(rec.get("role"))


# ── jetons ───────────────────────────────────────────────────────────────────

def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64u_dec(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def make_token(user: str, role: str, secret: bytes, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    ttl = MAX_AGE_ADMIN_S if role == "admin" else MAX_AGE_GUEST_S
    payload = json.dumps({"u": user, "r": role, "exp": int(now + ttl)},
                         separators=(",", ":"), sort_keys=True).encode("utf-8")
    p = _b64u(payload)
    sig = hmac.new(secret, p.encode("ascii"), hashlib.sha256).digest()
    return p + "." + _b64u(sig)


def verify_token(token: Optional[str], secret: bytes, now: Optional[float] = None) -> Optional[dict]:
    """{u, r, exp} si signature valide et non expiré, sinon None."""
    if not token or "." not in token:
        return None
    p, s = token.split(".", 1)
    try:
        expected = hmac.new(secret, p.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64u_dec(s), expected):
            return None
        d = json.loads(_b64u_dec(p).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("r") not in ROLES or not d.get("u"):
        return None
    now = time.time() if now is None else now
    try:
        if float(d.get("exp", 0)) <= now:
            return None
    except (TypeError, ValueError):
        return None
    return d


def _parse_cookies(header: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in header.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def session_from_scope(scope: dict) -> Optional[dict]:
    secret = load_secret()
    if secret is None:
        return None
    cookie_hdr = ""
    for k, v in scope.get("headers") or []:
        if k == b"cookie":
            cookie_hdr = v.decode("latin-1")
            break
    tok = _parse_cookies(cookie_hdr).get(COOKIE_NAME)
    return verify_token(tok, secret)


# ── limitation de débit ──────────────────────────────────────────────────────

def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "?"


def _rate_limited(ip: str, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with _lock:
        q = _failures[ip]
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        return len(q) >= RATE_MAX_FAILURES


def _record_failure(ip: str, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with _lock:
        _failures[ip].append(now)


def reset_rate_limits() -> None:
    with _lock:
        _failures.clear()


# ── utilitaires HTTP ─────────────────────────────────────────────────────────

def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or \
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


def _wants_json(request: Request) -> bool:
    acc = request.headers.get("accept", "")
    return "application/json" in acc and "text/html" not in acc


def safe_next(nxt: Optional[str]) -> str:
    """Chemin relatif même origine uniquement (rejette URL absolues, //, \\)."""
    if not nxt or not isinstance(nxt, str):
        return "/"
    nxt = nxt.strip()
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or "://" in nxt:
        return "/"
    if nxt.startswith("/login") or nxt.startswith("/logout"):
        return "/"
    return nxt


def _login_url(path: str, query: str) -> str:
    nxt = path + ("?" + query if query else "")
    return "/login?next=" + urllib.parse.quote(nxt, safe="/")


def _set_session_cookie(resp: Response, token: str, role: str, https: bool) -> None:
    resp.set_cookie(COOKIE_NAME, token,
                    max_age=MAX_AGE_ADMIN_S if role == "admin" else MAX_AGE_GUEST_S,
                    path="/", httponly=True, samesite="lax", secure=https)


def _clear_session_cookie(resp: Response, https: bool) -> None:
    resp.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=https)


# ── garde ASGI ───────────────────────────────────────────────────────────────

def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


async def _send_json(scope, receive, send, status: int, body: dict) -> None:
    resp = JSONResponse(body, status_code=status)
    await resp(scope, receive, send)


class AuthGate:
    """Middleware ASGI pur (pas de BaseHTTPMiddleware : pas de tampon, pas de
    thread supplémentaire). Ne touche que scope["type"] == "http"."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        method = (scope.get("method") or "GET").upper()
        if is_public(path):
            await self.app(scope, receive, send)
            return
        if not is_configured():
            await _send_json(scope, receive, send, 503, {"detail": "auth non configurée"})
            return
        sess = session_from_scope(scope)
        if sess is None:
            if path.startswith("/api"):
                await _send_json(scope, receive, send, 401, {"detail": "authentification requise"})
                return
            qs = (scope.get("query_string") or b"").decode("latin-1")
            resp = RedirectResponse(_login_url(path, qs), status_code=302)
            await resp(scope, receive, send)
            return
        if path.startswith("/api") and method in MUTATING and sess.get("r") != "admin":
            await _send_json(scope, receive, send, 403, {"detail": "lecture seule (invité)"})
            return
        state = scope.setdefault("state", {})
        state["cc_user"] = {"user": sess["u"], "role": sess["r"], "exp": sess.get("exp")}
        await self.app(scope, receive, send)


# ── routes ───────────────────────────────────────────────────────────────────

router = APIRouter()

_LOGIN_FALLBACK = """<!doctype html><html lang="fr"><meta charset="utf-8">
<title>FUTUR // connexion</title><body style="background:#0b0e12;color:#d8dee9;font-family:monospace">
<form method="post" action="/login" style="max-width:320px;margin:15vh auto">
<h1 style="font-size:16px">FUTUR // COMMAND CENTER</h1>
<p>Accès restreint. Tout est paper/shadow : capital virtuel, aucun ordre réel.</p>
<input name="username" placeholder="utilisateur" autocomplete="username" required style="width:100%;margin:4px 0">
<input name="password" type="password" placeholder="mot de passe" autocomplete="current-password" required style="width:100%;margin:4px 0">
<input type="hidden" name="next" value="">
<button type="submit">connexion</button></form></body></html>"""


@router.get("/health", include_in_schema=False)
def health():
    return {"ok": True}


@router.get("/login", include_in_schema=False)
def login_page():
    p = STATIC / "login.html"
    try:
        html = p.read_text(encoding="utf-8")
    except OSError:
        html = _LOGIN_FALLBACK
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


async def _credentials(request: Request) -> Tuple[str, str, str]:
    ctype = request.headers.get("content-type", "")
    username = password = nxt = ""
    if "application/json" in ctype:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            username = str(body.get("username") or "")
            password = str(body.get("password") or "")
            nxt = str(body.get("next") or "")
    else:
        # application/x-www-form-urlencoded parsé à la main : python-multipart
        # n'est installé ni dans le venv hôte ni dans l'image (pas de dépendance
        # nouvelle) et request.form() l'exige même pour un simple formulaire.
        try:
            raw = (await request.body())[:65536].decode("utf-8", "replace")
            form = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        except Exception:
            form = {}
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        nxt = str(form.get("next") or "")
    return username, password, nxt


@router.post("/login", include_in_schema=False)
async def login_submit(request: Request):
    https = _is_https(request)
    wants_json = _wants_json(request)
    if not is_configured():
        return JSONResponse({"detail": "auth non configurée"}, status_code=503)
    username, password, nxt = await _credentials(request)
    if not nxt:
        nxt = request.query_params.get("next", "")
    ip = client_ip(request)
    if _rate_limited(ip):
        if wants_json:
            return JSONResponse({"detail": "trop de tentatives, réessayez plus tard"}, status_code=429)
        return RedirectResponse("/login?error=rate", status_code=303)
    role = verify_password(username[:128], password[:1024])
    if role is None:
        _record_failure(ip)
        if wants_json:
            return JSONResponse({"detail": "identifiants invalides"}, status_code=401)
        return RedirectResponse("/login?error=1", status_code=303)
    secret = load_secret()
    if secret is None:   # pragma: no cover — is_configured() l'a vérifié juste avant
        return JSONResponse({"detail": "auth non configurée"}, status_code=503)
    token = make_token(username, role, secret)
    if wants_json:
        resp = JSONResponse({"user": username, "role": role, "next": safe_next(nxt)})
    else:
        resp = RedirectResponse(safe_next(nxt), status_code=303)
    _set_session_cookie(resp, token, role, https)
    return resp


@router.get("/logout", include_in_schema=False)
def logout(request: Request):
    resp = RedirectResponse("/login", status_code=303)
    _clear_session_cookie(resp, _is_https(request))
    return resp


@router.get("/api/me")
def api_me(request: Request):
    u = getattr(request.state, "cc_user", None)
    if not isinstance(u, dict):   # pragma: no cover — la garde a déjà refusé
        return JSONResponse({"detail": "authentification requise"}, status_code=401)
    return {"user": u["user"], "role": u["role"]}
