from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

import requests


class PublicHTTPClient:
    """
    HTTP client for public data sources.

    It intentionally contains no proxy rotation and backs off when sources return
    `429`/`418`, including `Retry-After` when present.
    """

    def __init__(
        self,
        *,
        rate_limit_per_minute: Optional[int] = None,
        retries: int = 5,
        timeout: int = 30,
        session: Optional[requests.Session] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.retries = retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "futur-public-data/1.0")
        self._sleeper = sleeper
        self._last_request_at = 0.0
        self._min_interval = (
            60.0 / float(rate_limit_per_minute)
            if rate_limit_per_minute and rate_limit_per_minute > 0
            else 0.0
        )

    def _rate_limit(self) -> None:
        if self._min_interval <= 0:
            return
        now = time.monotonic()
        wait = self._min_interval - (now - self._last_request_at)
        if wait > 0:
            self._sleeper(wait)
        self._last_request_at = time.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_error: Optional[BaseException] = None
        for attempt in range(self.retries):
            self._rate_limit()
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if response.status_code in (418, 429):
                    retry_after = _parse_retry_after(response.headers)
                    self._sleeper(retry_after if retry_after is not None else min(60.0, 2.0 ** attempt))
                    continue
                response.raise_for_status()
                return response
            except Exception as exc:  # requests and fake test sessions share no common exception type.
                last_error = exc
                if attempt == self.retries - 1:
                    break
                self._sleeper(min(60.0, 2.0 ** attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("HTTP request failed without response: %s %s" % (method, url))

    def get_json(self, url: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.request("GET", url, params=params).json()

    def get_bytes(self, url: str, params: Optional[Mapping[str, Any]] = None) -> bytes:
        return self.request("GET", url, params=params).content

    def download(self, url: str, path: Path, checksum_url: Optional[str] = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.get_bytes(url)
        path.write_bytes(payload)
        if checksum_url:
            checksum_text = self.get_bytes(checksum_url).decode("utf-8", errors="replace")
            verify_sha256(path, checksum_text)
        return path


def _parse_retry_after(headers: Mapping[str, str]) -> Optional[float]:
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path, checksum_text: str) -> None:
    expected = checksum_text.strip().split()[0]
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise ValueError("Checksum mismatch for %s: expected %s got %s" % (path, expected, actual))


class CheckpointStore:
    """Small JSON checkpoint store for resumable public backfills."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                self._state: Dict[str, Any] = json.load(fh)
        else:
            self._state = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._state[key] = value
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._state, fh, indent=2, sort_keys=True, default=str)
        tmp.replace(self.path)
