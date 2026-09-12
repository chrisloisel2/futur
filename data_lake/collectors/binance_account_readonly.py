#!/usr/bin/env python3
"""
binance_account_readonly.py -- client Binance STRICTEMENT en lecture.

Trois garanties, structurelles et non declaratives :
  1. Une liste blanche d'endpoints. Tout appel hors liste leve. Aucun chemin d'ordre n'existe dans ce fichier.
  2. Seule la methode GET est implementee. Il n'y a pas de code POST / DELETE ici.
  3. Si la cle porte une permission de trading, le client REFUSE de s'en servir (sauf autorisation explicite
     de l'appelant, qui est alors journalisee) : une cle qui peut trader n'a rien a faire dans un depot de recherche.

Les identifiants viennent uniquement de l'environnement. Ils ne sont ni ecrits, ni journalises, ni retournes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FAPI = "https://fapi.binance.com"
API = "https://api.binance.com"

#: endpoint -> (base, signe ?, ce qu'il donne). RIEN d'autre n'est appelable.
ALLOWED: Dict[str, Tuple[str, bool, str]] = {
    "/fapi/v1/exchangeInfo": (FAPI, False, "symbol constraints (public)"),
    "/fapi/v1/commissionRate": (FAPI, True, "actual maker/taker fee for one futures symbol"),
    "/fapi/v1/leverageBracket": (FAPI, True, "leverage brackets and maintenance margin"),
    "/fapi/v1/userTrades": (FAPI, True, "own fills"),
    "/fapi/v1/income": (FAPI, True, "own income rows (funding fees, commissions)"),
    "/fapi/v2/account": (FAPI, True, "account metadata: fee tier and permissions"),
    "/api/v3/account": (API, True, "spot commission rates"),
    "/sapi/v1/account/apiRestrictions": (API, True, "what this key is allowed to do"),
    "/sapi/v1/margin/allPairs": (API, True, "margin pairs (is the asset borrowable?)"),
    "/sapi/v1/margin/interestRateHistory": (API, True, "borrow interest rate history"),
}
#: ce que le compte NE DOIT PAS pouvoir faire pour qu'on utilise la cle
FORBIDDEN_PERMISSIONS = ("enableSpotAndMarginTrading", "enableFutures", "enableMargin", "enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer")
ENV_KEY, ENV_SECRET = "BINANCE_READONLY_API_KEY", "BINANCE_READONLY_API_SECRET"
FALLBACK_ENV = ("BINANCE_API_KEY", "BINANCE_API_SECRET")


class ReadOnlyViolation(RuntimeError):
    pass


def credentials() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """-> (cle, secret, nom_de_la_variable). Jamais journalise, jamais retourne dans un rapport."""
    k, s = os.getenv(ENV_KEY), os.getenv(ENV_SECRET)
    if k and s:
        return k, s, ENV_KEY
    k, s = os.getenv(FALLBACK_ENV[0]), os.getenv(FALLBACK_ENV[1])
    if k and s:
        return k, s, FALLBACK_ENV[0]
    return None, None, None


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
            if not self._key:
                return {"status": "no_credentials", "path": path, "endpoint": "GET " + path}
            if self.refused_reason:
                return {"status": "refused", "path": path, "reason": self.refused_reason}
        try:
            return {"status": "ok", "path": path, "data": self._request(path, params)}
        except HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                detail = None
            self.calls.append({"path": path, "status": e.code})
            return {"status": "error", "path": path, "http_status": e.code, "error": (detail or {}).get("msg") or ("HTTP %s" % e.code)}
        except Exception as e:
            self.calls.append({"path": path, "status": None})
            return {"status": "error", "path": path, "http_status": None, "error": "%s: %s" % (type(e).__name__, str(e)[:80])}

    # ---- garde-fou de permissions
    def check_permissions(self) -> Dict[str, Any]:
        """Refuse la cle si elle peut trader. Une cle de recherche doit etre inerte sur le marche."""
        if not self._key:
            return {"status": "no_credentials", "usable": False, "reason": "no API key in the environment"}
        r = self.get("/sapi/v1/account/apiRestrictions")
        if r["status"] != "ok":
            self.refused_reason = "cannot read API restrictions: %s" % r.get("error")
            return {"status": r["status"], "usable": False, "reason": self.refused_reason, "detail": r.get("error")}
        self.restrictions = r["data"]
        granted = [p for p in FORBIDDEN_PERMISSIONS if r["data"].get(p)]
        if granted and not self.allow_trading_key:
            self.refused_reason = "the key grants %s; a research repository must use a key that cannot trade" % ", ".join(granted)
            return {"status": "refused", "usable": False, "reason": self.refused_reason, "granted_permissions": granted, "restrictions": r["data"]}
        return {"status": "ok", "usable": True, "granted_permissions": granted, "restrictions": r["data"],
                "note": "trading permissions present but explicitly allowed by the caller" if granted else "key is read-only"}
