#!/usr/bin/env python3
"""
binance_account_readonly.py -- client Binance STRICTEMENT en lecture.

Trois garanties, structurelles et non declaratives :
  1. Une liste blanche d'endpoints. Tout appel hors liste leve. Aucun chemin d'ordre n'existe dans ce fichier.
  2. Seule la methode GET est implementee. Il n'y a pas de code POST / DELETE ici.
  3. Si la cle porte une permission de trading, le client REFUSE de s'en servir (sauf autorisation explicite
     de l'appelant, qui est alors journalisee) : une cle qui peut trader n'a rien a faire dans un depot de recherche.

Les identifiants viennent uniquement de l'environnement. Ils ne sont ni ecrits, ni journalises, ni retournes.

Durcissement P13 (relecture adverse avant la premiere vraie cle) :
  - refus PAR DEFAUT : toute permission enable*/permits* vraie autre que la lecture refuse la cle (enableFixApiTrade
    et tout drapeau futur compris), pas seulement les huit nommees ;
  - porte structurelle : aucun endpoint de compte n'est appelable avant que la sonde apiRestrictions ait dit oui ;
  - aucune redirection HTTP suivie (urllib re-emettrait l'en-tete X-MBX-APIKEY vers un autre hote) ;
  - messages d'erreur Binance aseptises (les adresses IP que Binance renvoie dans -2015 n'atteignent aucun rapport) ;
  - variables read-only a moitie posees = erreur, jamais de repli silencieux sur une cle legacy ;
  - canWithdraw / canTrade sont des capacites de COMPTE, pas de cle : informatifs, jamais un motif de refus.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    """Une redirection n'est jamais suivie : urllib re-emettrait l'en-tete X-MBX-APIKEY vers l'hote cible."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirect())


def urlopen(req: Request, timeout: Optional[int] = None):
    """Le seul point de sortie reseau du module : GET, sans redirection."""
    return _OPENER.open(req, timeout=timeout)


_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{1,4}\b")


def sanitise_error(msg: Optional[str], limit: int = 120) -> Optional[str]:
    """Un message d'erreur Binance peut contenir l'adresse IP de la machine (-2015 'request ip: ...') : elle est
    retiree avant que le message n'atteigne un rapport ou un journal. Tronque a `limit`."""
    if msg is None:
        return None
    return _IP_RE.sub("<ip>", str(msg))[:limit]

FAPI = "https://fapi.binance.com"
API = "https://api.binance.com"

#: endpoint -> (base, signe ?, ce qu'il donne). RIEN d'autre n'est appelable.
ALLOWED: Dict[str, Tuple[str, bool, str]] = {
    # les sept endpoints de compte prescrits par la charte P11
    "/fapi/v1/commissionRate": (FAPI, True, "actual maker/taker fee for one futures symbol"),
    "/fapi/v1/leverageBracket": (FAPI, True, "leverage brackets and maintenance margin"),
    "/fapi/v2/account": (FAPI, True, "futures account metadata: fee tier, canTrade / canWithdraw flags"),
    "/api/v3/account": (API, True, "spot commission rates, canTrade / canWithdraw flags"),
    "/fapi/v1/income": (FAPI, True, "own income rows (funding fees, commissions)"),
    "/fapi/v1/userTrades": (FAPI, True, "own fills"),
    "/sapi/v1/margin/allPairs": (API, True, "margin pairs (is the asset borrowable?)"),
    # deux lectures hors compte, justifiees dans READONLY_KEY_SAFETY_AUDIT.md
    "/fapi/v1/exchangeInfo": (FAPI, False, "symbol constraints (public, unsigned)"),
    "/sapi/v1/account/apiRestrictions": (API, True, "the safety probe: what this key is allowed to do; read BEFORE any account call"),
}
ACCOUNT_ENDPOINTS = ("/fapi/v1/commissionRate", "/fapi/v1/leverageBracket", "/fapi/v2/account", "/api/v3/account", "/fapi/v1/income", "/fapi/v1/userTrades", "/sapi/v1/margin/allPairs")
#: ce que la cle NE DOIT PAS pouvoir faire (les huit de la charte P11 ; la regle reelle est plus large, voir granted_permissions)
FORBIDDEN_PERMISSIONS = ("enableSpotAndMarginTrading", "enableFutures", "enableMargin", "enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer", "enableVanillaOptions", "enablePortfolioMarginTrading")
#: les SEULS drapeaux enable*/permits* qui peuvent etre vrais : tout autre drapeau vrai refuse la cle (deny-by-default)
PERMISSIONS_ALLOWED_TRUE = ("enableReading", "enableFixReadOnly")
#: drapeaux de COMPTE (pas de cle) lus a titre informatif : canWithdraw est vrai sur tout compte normal quelle que soit la cle
ACCOUNT_FLAGS_INFORMATIONAL = ("canWithdraw", "canTrade", "canDeposit")
ENV_KEY, ENV_SECRET = "BINANCE_READONLY_API_KEY", "BINANCE_READONLY_API_SECRET"
FALLBACK_ENV = ("BINANCE_API_KEY", "BINANCE_API_SECRET")


class ReadOnlyViolation(RuntimeError):
    pass


def credentials() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """-> (cle, secret, nom_de_la_variable). Jamais journalise, jamais retourne dans un rapport.
    Une paire read-only a moitie posee est une ERREUR (variable 'half-set:...'), jamais un repli sur la paire legacy."""
    k, s = os.getenv(ENV_KEY), os.getenv(ENV_SECRET)
    if k and s:
        return k, s, ENV_KEY
    if k or s:
        return None, None, "half-set:%s" % (ENV_KEY if k else ENV_SECRET)
    k, s = os.getenv(FALLBACK_ENV[0]), os.getenv(FALLBACK_ENV[1])
    if k and s:
        return k, s, FALLBACK_ENV[0]
    return None, None, None


def granted_permissions(restrictions: Dict[str, Any]) -> List[str]:
    """Tout drapeau enable*/permits* vrai qui n'est pas une permission de lecture. Deny-by-default : un drapeau que
    Binance ajouterait demain refuse la cle tant qu'il n'est pas explicitement dans PERMISSIONS_ALLOWED_TRUE."""
    return sorted(k for k, v in (restrictions or {}).items() if isinstance(k, str) and (k.startswith("enable") or k.startswith("permits")) and v and k not in PERMISSIONS_ALLOWED_TRUE)


def restrictions_summary(restrictions: Dict[str, Any]) -> Dict[str, bool]:
    """Seulement les booleens de permission : ni createTime, ni date d'expiration, ni quoi que ce soit d'autre."""
    return {k: bool(v) for k, v in (restrictions or {}).items() if isinstance(k, str) and isinstance(v, bool) and (k.startswith("enable") or k.startswith("permits") or k == "ipRestrict")}


def has_credentials() -> bool:
    return credentials()[0] is not None


def redact(s: Optional[str]) -> str:
    if not s:
        return "<absent>"
    return "%s…%s (%d chars)" % (s[:3], s[-2:], len(s))


class ReadOnlyClient:
    def __init__(self, allow_trading_key: bool = False, timeout: int = 20):
        self._key, self._secret, self.env_var = credentials()
        self.allow_trading_key = allow_trading_key
        self.timeout = timeout
        self.calls: List[Dict[str, Any]] = []
        self.restrictions: Optional[Dict[str, Any]] = None
        self.refused_reason: Optional[str] = None
        self.permissions_ok = False                      # porte structurelle : vrai seulement apres une sonde reussie
        if self.env_var and self.env_var.startswith("half-set:"):
            self.refused_reason = "read-only credentials are half-set (%s without its pair); no fallback" % self.env_var.split(":", 1)[1]

    # ---- transport
    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if path not in ALLOWED:
            raise ReadOnlyViolation("endpoint %r is not in the read-only whitelist" % path)
        base, signed, _ = ALLOWED[path]
        params = dict(params or {})
        headers = {"User-Agent": "futur-account-readonly/1.0"}
        if signed:
            if not self._key:
                raise ReadOnlyViolation("signed endpoint %s requires credentials" % path)
            params.setdefault("recvWindow", 5000); params["timestamp"] = int(time.time() * 1000)
            q = urlencode(params, doseq=True)
            sig = hmac.new(self._secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            url = "%s%s?%s&signature=%s" % (base, path, q, sig)
            headers["X-MBX-APIKEY"] = self._key
        else:
            url = "%s%s%s" % (base, path, ("?" + urlencode(params, doseq=True)) if params else "")
        # GET seulement : aucune autre methode n'existe dans ce fichier
        with urlopen(Request(url, headers=headers, method="GET"), timeout=self.timeout) as r:
            body = r.read()
        self.calls.append({"path": path, "params": {k: v for k, v in params.items() if k not in ("timestamp", "signature")}, "status": 200})
        return json.loads(body.decode("utf-8", "replace"))

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """-> {"status": ok|error|no_credentials|refused, "data"|"error"}. Ne leve jamais pour une panne reseau."""
        base, signed, _ = ALLOWED.get(path, (None, None, None))
        if base is None:
            raise ReadOnlyViolation("endpoint %r is not in the read-only whitelist" % path)
        if signed:
            if self.refused_reason:
                return {"status": "refused", "path": path, "reason": self.refused_reason}
            if not self._key:
                return {"status": "no_credentials", "path": path, "endpoint": "GET " + path}
            if path in ACCOUNT_ENDPOINTS and not self.permissions_ok:          # porte structurelle, pas procedurale
                return {"status": "refused", "path": path, "reason": "account endpoint requested before a successful apiRestrictions probe"}
        try:
            return {"status": "ok", "path": path, "data": self._request(path, params)}
        except HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                detail = None
            self.calls.append({"path": path, "status": e.code})
            code = (detail or {}).get("code") if isinstance(detail, dict) else None
            return {"status": "error", "path": path, "http_status": e.code, "binance_code": code,
                    "error": sanitise_error(((detail or {}).get("msg") if isinstance(detail, dict) else None) or ("HTTP %s" % e.code))}
        except Exception as e:
            self.calls.append({"path": path, "status": None})
            return {"status": "error", "path": path, "http_status": None, "error": sanitise_error("%s: %s" % (type(e).__name__, str(e)[:80]))}

    # ---- garde-fou de permissions
    def check_permissions(self) -> Dict[str, Any]:
        """Refuse la cle si elle peut trader. Une cle de recherche doit etre inerte sur le marche."""
        if self.refused_reason:
            return {"status": "refused", "usable": False, "reason": self.refused_reason}
        if not self._key:
            return {"status": "no_credentials", "usable": False, "reason": "no API key in the environment"}
        r = self.get("/sapi/v1/account/apiRestrictions")
        if r["status"] != "ok" or not isinstance(r.get("data"), dict):
            self.refused_reason = "cannot read API restrictions: %s" % sanitise_error(r.get("error") or "unexpected payload")
            return {"status": r["status"] if r["status"] != "ok" else "error", "usable": False, "reason": self.refused_reason, "detail": sanitise_error(r.get("error"))}
        self.restrictions = r["data"]; summary = restrictions_summary(r["data"]); granted = granted_permissions(r["data"])
        if not r["data"].get("enableReading", False):
            self.refused_reason = "the key cannot even read"; return {"status": "refused", "usable": False, "reason": self.refused_reason, "restrictions": summary}
        if granted and not self.allow_trading_key:
            self.refused_reason = "the key grants %s; a research repository must use a key that cannot trade" % ", ".join(granted)
            return {"status": "refused", "usable": False, "reason": self.refused_reason, "granted_permissions": granted, "restrictions": summary}
        self.permissions_ok = True
        return {"status": "ok", "usable": True, "granted_permissions": granted, "restrictions": summary, "ip_restricted": bool(r["data"].get("ipRestrict")),
                "note": "trading permissions present but explicitly allowed by the caller" if granted else "key is read-only"}


def cross_check_account_flags(account_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Depuis /fapi/v2/account ou /api/v3/account : capacites du COMPTE, pas de la cle. canWithdraw est vrai sur tout
    compte normal meme avec une cle qui ne peut rien faire : informatif, jamais un motif de refus (la sonde
    apiRestrictions est la seule barriere, et elle est fail-closed)."""
    flags = {f: bool(account_payload.get(f)) for f in ACCOUNT_FLAGS_INFORMATIONAL if f in (account_payload or {})}
    return {"account_flags": flags, "refuse": False, "note": "account-level capabilities, not key permissions; informational"}
