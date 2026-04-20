"""
Wrapper HTTP pour les API collectors.

Injecte automatiquement :
  - un proxy depuis ProxyPool (MongoDB)
  - le User-Agent configuré
  - le timeout par défaut

Usage :
    from api_collectors import http
    resp = http.get(url, params={...})
    resp.raise_for_status()
"""
import logging
from typing import Any, Dict, Optional

import requests

from api_collectors.config import REQUEST_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)


def _get_pool():
    """Lazy import pour éviter les imports circulaires."""
    from api_collectors.proxy import ProxyPool
    try:
        return ProxyPool.get_instance()
    except Exception:
        log.debug("ProxyPool non disponible — requête sans proxy")
        return None


def get(url: str, **kwargs) -> requests.Response:
    """
    requests.get avec proxy automatique depuis MongoDB.

    Retry transparent avec un nouveau proxy si le premier échoue
    à cause d'une erreur réseau (ProxyError, ConnectionError).
    """
    pool = _get_pool()
    proxy_url: Optional[str] = None

    # Injecter le proxy seulement si pas déjà fourni
    if pool and "proxies" not in kwargs:
        proxy_url = pool.get()
        if proxy_url:
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

    # Injecter User-Agent si pas de headers custom
    headers: Dict[str, Any] = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)
    kwargs["headers"] = headers

    kwargs.setdefault("timeout", REQUEST_TIMEOUT)

    try:
        response = requests.get(url, **kwargs)
        return response

    except (requests.exceptions.ProxyError,
            requests.exceptions.ConnectionError) as exc:

        if proxy_url and pool:
            log.debug("Proxy %s échoué (%s) — retry sans proxy", proxy_url, type(exc).__name__)
            pool.mark_failed(proxy_url)
            kwargs.pop("proxies", None)
            return requests.get(url, **kwargs)   # un retry sans proxy
        raise
