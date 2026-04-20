"""
ProxyPool — pool de proxies partagé, chargé depuis MongoDB.

Source : mongodb://admin:admin123@100.93.248.105/ > proxy_db > proxies

Utilisé par :
  - les middlewares Scrapy (marketintel/middlewares.py)
  - les API collectors via api_collectors/http.py

Thread-safe. Singleton par process.
"""
import logging
import random
import threading
import time
from typing import Dict, List, Optional

from pymongo import MongoClient

log = logging.getLogger(__name__)

# Champs potentiels où l'URL du proxy peut être stockée dans le document
_URL_FIELDS = ("url", "proxy", "address", "http", "ip_port", "proxy_url")


def _extract_url(doc: dict) -> Optional[str]:
    """Extrait l'URL de proxy depuis un document MongoDB (schéma flexible)."""
    for field in _URL_FIELDS:
        val = doc.get(field)
        if val and isinstance(val, str) and ":" in val:
            return val if val.startswith("http") else f"http://{val}"

    # Cas : doc avec ip + port séparés
    ip = doc.get("ip") or doc.get("host")
    port = doc.get("port")
    if ip and port:
        return f"http://{ip}:{port}"

    return None


class ProxyPool:
    """
    Pool de proxies avec cache local et rotation aléatoire.

    Chargement depuis MongoDB, rafraîchissement automatique toutes les
    `refresh_interval` secondes. Les proxies en échec sont mis en quarantaine
    jusqu'au prochain rafraîchissement.
    """

    _instance: Optional["ProxyPool"] = None
    _singleton_lock = threading.Lock()

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(
        self,
        mongo_uri: str,
        db: str,
        collection: str,
        refresh_interval: int = 300,
    ):
        self._mongo_uri = mongo_uri
        self._db_name = db
        self._col_name = collection
        self._refresh_interval = refresh_interval

        self._proxies: List[str] = []
        self._quarantine: set = set()
        self._last_refresh: float = 0.0
        self._rw_lock = threading.RLock()

        self._client: Optional[MongoClient] = None
        self._load()

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "ProxyPool":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    from api_collectors.config import (
                        PROXY_MONGO_URI,
                        PROXY_MONGO_DB,
                        PROXY_COLLECTION,
                        PROXY_REFRESH_INTERVAL,
                    )
                    cls._instance = cls(
                        mongo_uri=PROXY_MONGO_URI,
                        db=PROXY_MONGO_DB,
                        collection=PROXY_COLLECTION,
                        refresh_interval=PROXY_REFRESH_INTERVAL,
                    )
        return cls._instance

    # ── Chargement MongoDB ────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._client is None:
                self._client = MongoClient(self._mongo_uri, serverSelectionTimeoutMS=5000)

            col = self._client[self._db_name][self._col_name]

            raw: List[str] = []
            for doc in col.find({}, {"_id": 0}):
                url = _extract_url(doc)
                if url:
                    raw.append(url)

            with self._rw_lock:
                self._proxies = raw
                self._quarantine.clear()
                self._last_refresh = time.monotonic()

            log.info("ProxyPool : %d proxies chargés depuis MongoDB (%s/%s)",
                     len(raw), self._db_name, self._col_name)

        except Exception:
            log.exception("ProxyPool : erreur chargement MongoDB — pool vide")

    def _maybe_refresh(self) -> None:
        if time.monotonic() - self._last_refresh > self._refresh_interval:
            self._load()

    # ── API publique ──────────────────────────────────────────────────────────

    def get(self) -> Optional[str]:
        """Retourne un proxy aléatoire disponible (hors quarantaine)."""
        self._maybe_refresh()
        with self._rw_lock:
            available = [p for p in self._proxies if p not in self._quarantine]
            if not available:
                # Tous en quarantaine → reset + rechargement
                log.warning("ProxyPool : tous les proxies en quarantaine — rechargement")
                self._load()
                available = self._proxies

            return random.choice(available) if available else None

    def mark_failed(self, proxy_url: str) -> None:
        """Met un proxy en quarantaine jusqu'au prochain refresh."""
        with self._rw_lock:
            self._quarantine.add(proxy_url)
            ratio = len(self._quarantine) / max(len(self._proxies), 1)
            if ratio >= 0.8:
                log.warning("ProxyPool : %.0f%% en quarantaine — rechargement forcé", ratio * 100)
                self._load()

    def for_requests(self) -> Optional[Dict[str, str]]:
        """Retourne un dict proxies compatible avec requests.get(proxies=...)."""
        url = self.get()
        if url:
            return {"http": url, "https": url}
        return None

    @property
    def size(self) -> int:
        with self._rw_lock:
            return len(self._proxies)

    @property
    def available(self) -> int:
        with self._rw_lock:
            return len([p for p in self._proxies if p not in self._quarantine])

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
