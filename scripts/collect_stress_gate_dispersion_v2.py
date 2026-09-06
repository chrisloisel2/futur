#!/usr/bin/env python3
"""
scripts/collect_stress_gate_dispersion_v2.py
─────────────────────────────────────────────────────────────────────────────
Collecteur déterministe, en lecture seule, des données publiques Binance +
Bybit nécessaires à stress_gate_dispersion_v2_reproduction (voir
research/edge_factory/basis_dispersion/stress_gate_dispersion_v2/
PREREGISTRATION.md — fenêtre gelée experiment_start_utc/experiment_end_utc,
4 actifs, 2 venues, mark price Binance 5m comme série canonique).

Ne fait AUCUNE analyse économique. Écrit uniquement : pages brutes
horodatées + journal JSONL + hashes. La normalisation (panel.parquet) et le
test primaire sont des étapes séparées (commits 5 et 6).

Usage :
    python3 scripts/collect_stress_gate_dispersion_v2.py --symbol BTCUSDT --kind funding --venue binance
    python3 scripts/collect_stress_gate_dispersion_v2.py --all   # les 4 actifs x 2 venues x (funding+markprice)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research" / "stress_gate_dispersion_v2"
RAW = OUT / "raw"
LOG = OUT / "logs" / "acquisition.jsonl"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
# Bornes gelées, PREREGISTRATION.md amendement (bis) : signal != prix.
SIGNAL_START_MS = int(datetime(2022, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
SIGNAL_END_MS = int(datetime(2026, 7, 14, tzinfo=timezone.utc).timestamp() * 1000)
PRICE_START_MS = SIGNAL_START_MS
PRICE_END_MS = int(datetime(2026, 7, 15, 1, tzinfo=timezone.utc).timestamp() * 1000)

MAX_ATTEMPTS = 5
MAX_PAGES = 2000             # échec explicite avant boucle infinie (amendement quater)
RATE_LIMIT_SLEEP_S = 1.0     # ~1 req/s/collecteur, largement sous les limites documentées


class AcquisitionError(RuntimeError):
    """Levée après épuisement des retries — jamais de boucle infinie."""


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── HTTP : un seul point d'entrée, mockable dans les tests ─────────────────

def http_get_json(url: str) -> tuple[int, bytes]:
    """Retourne (http_status, raw_bytes). Seul point de contact réseau —
    monkeypatché dans les tests, jamais appelé en dur ailleurs."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def fetch_with_retry(url: str, *, http_get: Callable[[str], tuple[int, bytes]] = http_get_json
                     ) -> tuple[int, bytes, int]:
    """Retry exponentiel + jitter, respecte 429, s'arrête après MAX_ATTEMPTS
    (jamais infini). Retourne (status, body, attempt_number final)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, body = http_get(url)
        if status == 200:
            return status, body, attempt
        if status == 429 or status >= 500:
            if attempt == MAX_ATTEMPTS:
                raise AcquisitionError(
                    f"{url} : {MAX_ATTEMPTS} tentatives épuisées, dernier statut {status}")
            backoff = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(backoff)
            continue
        # 4xx autre que 429 : anomalie persistante, on n'insiste pas
        raise AcquisitionError(f"{url} : statut {status} non retryable — {body[:300]!r}")
    raise AcquisitionError(f"{url} : sortie de boucle inattendue")   # défensif, jamais atteint


# ── journal + persistance idempotente ───────────────────────────────────────

@dataclass
class PageRecord:
    request_id: str
    retrieved_at_utc: str
    venue: str
    endpoint: str
    symbol: str
    parameters: dict
    http_status: int
    attempt_number: int
    response_byte_length: int
    response_sha256: str
    first_timestamp: Optional[int]
    last_timestamp: Optional[int]
    row_count: int


def _log_page(rec: PageRecord) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(asdict(rec), sort_keys=True) + "\n")


def _raw_page_path(venue: str, kind: str, symbol: str, page_key: str) -> Path:
    d = RAW / venue / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{symbol}_{page_key}.json"


def _identity_key(body: bytes) -> bytes:
    return body


def _bybit_list_key(body: bytes) -> bytes:
    """Bug trouvé lors de run_20260721_full (bybit/funding/BTCUSDT FAILED) :
    l'enveloppe de réponse Bybit contient un champ `time` (horodatage SERVEUR
    de la réponse elle-même), différent à chaque appel même quand
    `result.list` (la donnée de funding réelle) est identique — confirmé par
    2 appels réels consécutifs au même endpoint (voir commit). Comparer les
    octets bruts complets produit donc un faux conflit à chaque re-fetch
    d'une page déjà persistée. Cette clé de comparaison isole le contenu
    économiquement significatif ; le fichier persisté reste néanmoins la
    réponse brute COMPLÈTE, inchangée, pour l'audit."""
    return json.dumps(json.loads(body).get("result", {}).get("list", []),
                      sort_keys=True).encode()


def _persist_page_idempotent(path: Path, body: bytes, *,
                             comparison_key_fn=_identity_key) -> str:
    """N'écrase jamais une page déjà validée. Si le fichier existe, compare
    `comparison_key_fn(contenu)` (pas les octets bruts par défaut inutiles à
    comparer pour des endpoints avec enveloppe volatile — voir
    _bybit_list_key) au lieu de réécrire. Retourne le sha256 du contenu
    (brut) effectivement sur disque."""
    if path.exists():
        existing_body = path.read_bytes()
        if comparison_key_fn(existing_body) != comparison_key_fn(body):
            raise AcquisitionError(
                f"{path} existe déjà avec un contenu SIGNIFICATIVEMENT "
                "différent — jamais écrasé silencieusement, nouveau "
                "acquisition_run_id requis pour reconcilier")
        return hashlib.sha256(existing_body).hexdigest()  # no-op : contenu significatif identique
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _request_id(venue: str, endpoint: str, symbol: str, params: dict) -> str:
    payload = json.dumps({"venue": venue, "endpoint": endpoint, "symbol": symbol,
                          "params": params}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── Binance ──────────────────────────────────────────────────────────────

def binance_funding_page(symbol: str, start_ms: int, end_ms: int, *,
                         http_get=http_get_json, persist: bool = True) -> list[dict]:
    params = {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000}
    url = ("https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&startTime=%d"
          "&endTime=%d&limit=1000" % (symbol, start_ms, end_ms))
    status, body, attempt = fetch_with_retry(url, http_get=http_get)
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise AcquisitionError(f"binance fundingRate: réponse inattendue {rows!r}")
    ts_field = "fundingTime"
    if persist:
        page_key = f"{start_ms}_{end_ms}"
        _persist_page_idempotent(
            _raw_page_path("binance", "funding", symbol, page_key), body)
        _log_page(PageRecord(
            request_id=_request_id("binance", "fundingRate", symbol, params),
            retrieved_at_utc=now_utc_iso(), venue="binance", endpoint="fundingRate",
            symbol=symbol, parameters=params, http_status=status, attempt_number=attempt,
            response_byte_length=len(body), response_sha256=hashlib.sha256(body).hexdigest(),
            first_timestamp=rows[0][ts_field] if rows else None,
            last_timestamp=rows[-1][ts_field] if rows else None, row_count=len(rows)))
    return rows


def collect_binance_funding(symbol: str, start_ms: int, end_ms: int, *,
                            http_get=http_get_json, sleep_s: float = RATE_LIMIT_SLEEP_S
                            ) -> list[dict]:
    all_rows: list[dict] = []
    cursor = start_ms
    prev_cursor: Optional[int] = None
    prev_page_hash: Optional[str] = None
    n_pages = 0
    while cursor < end_ms:
        n_pages += 1
        if n_pages > MAX_PAGES:
            raise AcquisitionError(
                f"binance funding {symbol}: MAX_PAGES={MAX_PAGES} dépassé "
                "avant convergence — arrêt explicite, jamais une boucle infinie")
        page = binance_funding_page(symbol, cursor, end_ms, http_get=http_get)
        if not page:
            break
        page_hash = hashlib.sha256(
            json.dumps(page, sort_keys=True).encode()).hexdigest()
        if page_hash == prev_page_hash:
            raise AcquisitionError(
                f"binance funding {symbol}: page identique à la précédente "
                f"(cursor={cursor}) — aucune progression, arrêt")
        all_rows.extend(page)
        cursor = max(r["fundingTime"] for r in page) + 1
        if prev_cursor is not None and cursor <= prev_cursor:
            raise AcquisitionError(
                f"binance funding {symbol}: cursor n'avance pas "
                f"({cursor} <= {prev_cursor}) — arrêt")
        prev_cursor, prev_page_hash = cursor, page_hash
        if len(page) < 1000:
            break
        time.sleep(sleep_s)
    # tri + dédup explicites, jamais supposés de la réponse serveur
    dedup = {r["fundingTime"]: r for r in all_rows}
    return [dedup[k] for k in sorted(dedup)]


def binance_markprice_page(symbol: str, start_ms: int, end_ms: int, *,
                           interval: str = "5m", http_get=http_get_json,
                           persist: bool = True) -> list[list]:
    params = {"symbol": symbol, "interval": interval, "startTime": start_ms,
              "endTime": end_ms, "limit": 1500}
    url = ("https://fapi.binance.com/fapi/v1/markPriceKlines?symbol=%s&interval=%s"
          "&startTime=%d&endTime=%d&limit=1500" % (symbol, interval, start_ms, end_ms))
    status, body, attempt = fetch_with_retry(url, http_get=http_get)
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise AcquisitionError(f"binance markPriceKlines: réponse inattendue {rows!r}")
    if persist:
        page_key = f"{start_ms}_{end_ms}_{interval}"
        _persist_page_idempotent(
            _raw_page_path("binance", "mark_price", symbol, page_key), body)
        _log_page(PageRecord(
            request_id=_request_id("binance", "markPriceKlines", symbol, params),
            retrieved_at_utc=now_utc_iso(), venue="binance", endpoint="markPriceKlines",
            symbol=symbol, parameters=params, http_status=status, attempt_number=attempt,
            response_byte_length=len(body), response_sha256=hashlib.sha256(body).hexdigest(),
            first_timestamp=rows[0][0] if rows else None,
            last_timestamp=rows[-1][0] if rows else None, row_count=len(rows)))
    return rows


def collect_binance_markprice(symbol: str, start_ms: int, end_ms: int, *,
                              interval: str = "5m", http_get=http_get_json,
                              sleep_s: float = RATE_LIMIT_SLEEP_S) -> list[list]:
    all_rows: list[list] = []
    cursor = start_ms
    prev_cursor: Optional[int] = None
    prev_page_hash: Optional[str] = None
    n_pages = 0
    while cursor < end_ms:
        n_pages += 1
        if n_pages > MAX_PAGES:
            raise AcquisitionError(
                f"binance mark_price {symbol}: MAX_PAGES={MAX_PAGES} dépassé "
                "avant convergence — arrêt explicite, jamais une boucle infinie")
        page = binance_markprice_page(symbol, cursor, end_ms, interval=interval, http_get=http_get)
        if not page:
            break
        page_hash = hashlib.sha256(
            json.dumps(page, sort_keys=True).encode()).hexdigest()
        if page_hash == prev_page_hash:
            raise AcquisitionError(
                f"binance mark_price {symbol}: page identique à la précédente "
                f"(cursor={cursor}) — aucune progression, arrêt")
        all_rows.extend(page)
        cursor = page[-1][0] + 1     # open_time de la dernière barre + 1ms
        if prev_cursor is not None and cursor <= prev_cursor:
            raise AcquisitionError(
                f"binance mark_price {symbol}: cursor n'avance pas "
                f"({cursor} <= {prev_cursor}) — arrêt")
        prev_cursor, prev_page_hash = cursor, page_hash
        if len(page) < 1500:
            break
        time.sleep(sleep_s)
    dedup = {r[0]: r for r in all_rows}
    return [dedup[k] for k in sorted(dedup)]


# ── Bybit ────────────────────────────────────────────────────────────────

def bybit_funding_page(symbol: str, end_ms: int, *, http_get=http_get_json,
                       persist: bool = True) -> list[dict]:
    params = {"category": "linear", "symbol": symbol, "endTime": end_ms, "limit": 200}
    url = ("https://api.bybit.com/v5/market/funding/history?category=linear"
          "&symbol=%s&endTime=%d&limit=200" % (symbol, end_ms))
    status, body, attempt = fetch_with_retry(url, http_get=http_get)
    parsed = json.loads(body)
    rows = parsed.get("result", {}).get("list", []) if isinstance(parsed, dict) else None
    if rows is None:
        raise AcquisitionError(f"bybit funding/history: réponse inattendue {parsed!r}")
    ts_field = "fundingRateTimestamp"
    if persist:
        page_key = f"end{end_ms}"
        _persist_page_idempotent(
            _raw_page_path("bybit", "funding", symbol, page_key), body,
            comparison_key_fn=_bybit_list_key)
        _log_page(PageRecord(
            request_id=_request_id("bybit", "funding/history", symbol, params),
            retrieved_at_utc=now_utc_iso(), venue="bybit", endpoint="funding/history",
            symbol=symbol, parameters=params, http_status=status, attempt_number=attempt,
            response_byte_length=len(body), response_sha256=hashlib.sha256(body).hexdigest(),
            first_timestamp=int(rows[-1][ts_field]) if rows else None,
            last_timestamp=int(rows[0][ts_field]) if rows else None, row_count=len(rows)))
    return rows


def collect_bybit_funding(symbol: str, start_ms: int, end_ms: int, *,
                          http_get=http_get_json, sleep_s: float = RATE_LIMIT_SLEEP_S
                          ) -> list[dict]:
    """Le raw page count (loggé par bybit_funding_page) peut dépasser
    largement la fenêtre demandée : `endTime` seul renvoie jusqu'à 200
    enregistrements PRÉCÉDANT cette borne, quelle que soit leur ancienneté
    (documenté). Le filtre `>= start_ms` ci-dessous est ce qui restreint le
    résultat à la fenêtre réellement demandée — ne pas confondre les deux
    compteurs (voir tests, invariant vérifié explicitement)."""
    all_rows: list[dict] = []
    cursor = end_ms
    prev_cursor: Optional[int] = None
    prev_page_hash: Optional[str] = None
    n_pages = 0
    while True:
        n_pages += 1
        if n_pages > MAX_PAGES:
            raise AcquisitionError(
                f"bybit funding {symbol}: MAX_PAGES={MAX_PAGES} dépassé "
                "avant convergence — arrêt explicite, jamais une boucle infinie")
        page = bybit_funding_page(symbol, cursor, http_get=http_get)
        if not page:
            break
        page_hash = hashlib.sha256(
            json.dumps(page, sort_keys=True).encode()).hexdigest()
        if page_hash == prev_page_hash:
            raise AcquisitionError(
                f"bybit funding {symbol}: page identique à la précédente "
                f"(cursor={cursor}) — aucune progression, arrêt")
        all_rows.extend(page)
        oldest = min(int(r["fundingRateTimestamp"]) for r in page)
        cursor_new = oldest - 1
        if prev_cursor is not None and cursor_new >= prev_cursor:
            raise AcquisitionError(
                f"bybit funding {symbol}: cursor ne recule pas "
                f"({cursor_new} >= {prev_cursor}) — arrêt")
        prev_cursor, prev_page_hash = cursor_new, page_hash
        if oldest <= start_ms or len(page) < 200:
            break
        cursor = cursor_new
        time.sleep(sleep_s)
    dedup = {int(r["fundingRateTimestamp"]): r for r in all_rows
            if int(r["fundingRateTimestamp"]) >= start_ms}
    return [dedup[k] for k in sorted(dedup)]


def bybit_markprice_page(symbol: str, end_ms: int, *, interval: str = "5",
                         http_get=http_get_json, persist: bool = True) -> list[list]:
    params = {"category": "linear", "symbol": symbol, "interval": interval,
              "end": end_ms, "limit": 1000}
    url = ("https://api.bybit.com/v5/market/mark-price-kline?category=linear"
          "&symbol=%s&interval=%s&end=%d&limit=1000" % (symbol, interval, end_ms))
    status, body, attempt = fetch_with_retry(url, http_get=http_get)
    parsed = json.loads(body)
    rows = parsed.get("result", {}).get("list", []) if isinstance(parsed, dict) else None
    if rows is None:
        raise AcquisitionError(f"bybit mark-price-kline: réponse inattendue {parsed!r}")
    if persist:
        page_key = f"end{end_ms}_{interval}"
        _persist_page_idempotent(
            _raw_page_path("bybit", "mark_price", symbol, page_key), body,
            comparison_key_fn=_bybit_list_key)
        _log_page(PageRecord(
            request_id=_request_id("bybit", "mark-price-kline", symbol, params),
            retrieved_at_utc=now_utc_iso(), venue="bybit", endpoint="mark-price-kline",
            symbol=symbol, parameters=params, http_status=status, attempt_number=attempt,
            response_byte_length=len(body), response_sha256=hashlib.sha256(body).hexdigest(),
            first_timestamp=int(rows[-1][0]) if rows else None,
            last_timestamp=int(rows[0][0]) if rows else None, row_count=len(rows)))
    return rows


# ── Orchestrateur — périmètre PRIMAIRE uniquement (amendement ter) ────────
# 4 actifs x {funding Binance, funding Bybit, mark price Binance 5m} = 12
# séries. PAS de mark price Bybit ici (auxiliaire QC seulement, hors scope
# de ce run) : voir PREREGISTRATION.md.

PRIMARY_SERIES = [(sym, venue, kind) for sym in SYMBOLS
                 for venue, kind in (("binance", "funding"), ("bybit", "funding"),
                                     ("binance", "mark_price"))]


def _acquisition_run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_full_acquisition(*, run_id: Optional[str] = None,
                         sleep_s: float = RATE_LIMIT_SLEEP_S) -> dict:
    """Collecte séquentielle des 12 séries primaires sur la fenêtre gelée.
    Reprenable : chaque page déjà persistée est un no-op idempotent (voir
    _persist_page_idempotent), donc relancer après interruption ne refait
    pas le travail déjà validé."""
    run_id = run_id or _acquisition_run_id()
    started = now_utc_iso()
    summary: dict = {"acquisition_run_id": run_id, "started_at_utc": started,
                     "collector_commit": "HEAD", "series": {}}
    for sym, venue, kind in PRIMARY_SERIES:
        key = f"{venue}/{kind}/{sym}"
        print(f"[{run_id}] {key} ...", flush=True)
        try:
            if venue == "binance" and kind == "funding":
                rows = collect_binance_funding(sym, SIGNAL_START_MS, SIGNAL_END_MS, sleep_s=sleep_s)
            elif venue == "bybit" and kind == "funding":
                rows = collect_bybit_funding(sym, SIGNAL_START_MS, SIGNAL_END_MS, sleep_s=sleep_s)
            elif venue == "binance" and kind == "mark_price":
                rows = collect_binance_markprice(sym, PRICE_START_MS, PRICE_END_MS, sleep_s=sleep_s)
            else:
                raise AcquisitionError(f"série hors périmètre primaire : {key}")
        except AcquisitionError as e:
            summary["series"][key] = {"status": "FAILED", "error": str(e)}
            print(f"[{run_id}] {key} FAILED: {e}", flush=True)
            continue
        summary["series"][key] = {"status": "OK", "n_rows": len(rows)}
        print(f"[{run_id}] {key} OK: {len(rows)} lignes", flush=True)
    summary["completed_at_utc"] = now_utc_iso()
    (OUT / "logs").mkdir(parents=True, exist_ok=True)
    (OUT / "logs" / f"{run_id}_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True))
    return summary


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", choices=SYMBOLS)
    ap.add_argument("--venue", choices=["binance", "bybit"])
    ap.add_argument("--kind", choices=["funding", "mark_price"])
    ap.add_argument("--start-ms", type=int)
    ap.add_argument("--end-ms", type=int)
    ap.add_argument("--smoke-test", action="store_true",
                    help="ne collecte qu'UNE fenêtre de 3 jours, pour vérifier la connectivité réelle sans lancer la collecte complète")
    ap.add_argument("--all", action="store_true",
                    help="collecte complète des 12 séries primaires sur la fenêtre gelée (signal/prix, cf. PREREGISTRATION.md)")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    if args.all:
        summary = run_full_acquisition(run_id=args.run_id)
        print(json.dumps(summary, indent=2, sort_keys=True))
        n_failed = sum(1 for s in summary["series"].values() if s["status"] == "FAILED")
        raise SystemExit(1 if n_failed else 0)

    if not (args.symbol and args.venue and args.kind):
        ap.error("--symbol --venue --kind requis (ou --smoke-test/--all)")

    is_price = args.kind == "mark_price"
    default_start = PRICE_START_MS if is_price else SIGNAL_START_MS
    default_end = PRICE_END_MS if is_price else SIGNAL_END_MS
    start_ms = args.start_ms if args.start_ms is not None else default_start
    end_ms = args.end_ms if args.end_ms is not None else default_end

    if args.smoke_test:
        end_ms = default_end
        start_ms = end_ms - 3 * 24 * 3600 * 1000     # 3 jours seulement

    if args.venue == "binance" and args.kind == "funding":
        rows = collect_binance_funding(args.symbol, start_ms, end_ms)
    elif args.venue == "binance" and args.kind == "mark_price":
        rows = collect_binance_markprice(args.symbol, start_ms, end_ms)
    elif args.venue == "bybit" and args.kind == "funding":
        rows = collect_bybit_funding(args.symbol, start_ms, end_ms)
    else:
        raise SystemExit("bybit mark_price : hors périmètre primaire (auxiliaire QC "
                         "uniquement, cf. PREREGISTRATION.md) — utiliser "
                         "bybit_markprice_page directement si vraiment nécessaire")

    print(f"{args.venue}/{args.kind}/{args.symbol}: {len(rows)} lignes "
         f"[{start_ms} .. {end_ms}]")


if __name__ == "__main__":
    main()
