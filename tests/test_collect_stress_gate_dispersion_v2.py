"""
tests/test_collect_stress_gate_dispersion_v2.py
─────────────────────────────────────────────────────────────────────────────
Collecteur stress_gate_dispersion_v2 : tests avec réponses HTTP MOCKÉES
uniquement — aucun appel réseau réel dans cette suite, aucune donnée
économique analysée. Prouve pagination, retry/backoff, rejet fail-closed,
et persistance idempotente des pages brutes.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import scripts.collect_stress_gate_dispersion_v2 as C


@pytest.fixture(autouse=True)
def _tmp_out(tmp_path, monkeypatch):
    """Redirige toute écriture (pages brutes + journal) vers un tmp_path —
    jamais data/research/ pendant les tests."""
    monkeypatch.setattr(C, "RAW", tmp_path / "raw")
    monkeypatch.setattr(C, "LOG", tmp_path / "logs" / "acquisition.jsonl")
    yield


def _mock_sequence(pages):
    """pages: liste de (status, body_bytes). Consommée dans l'ordre ; lève
    si appelée plus de fois que prévu (preuve qu'aucun appel superflu)."""
    it = iter(pages)

    def _fn(url):
        try:
            return next(it)
        except StopIteration:
            raise AssertionError(f"appel HTTP inattendu (plus de pages mockées) : {url}")
    return _fn


def _binance_funding_row(ts, rate="0.0001"):
    return {"symbol": "BTCUSDT", "fundingTime": ts, "fundingRate": rate}


def _bybit_funding_row(ts, rate="0.0001"):
    return {"symbol": "BTCUSDT", "fundingRateTimestamp": str(ts), "fundingRate": rate}


# ── pagination ───────────────────────────────────────────────────────────

def test_binance_funding_pagination_forward_sorted_deduped():
    """Une vraie réponse Binance bornée [start,end] ne peut renvoyer moins de
    `limit` lignes QUE s'il n'y a plus rien dans l'intervalle restant — donc
    une page pleine (1000) doit forcer une continuation, une page partielle
    doit arrêter la boucle. Page1 pleine (1000 lignes, avec un doublon
    volontaire de la dernière ligne en tête de page2) + page2 partielle."""
    STEP = 8 * 3600_000
    page1 = [_binance_funding_row(1000 + i * STEP) for i in range(1000)]
    last_ts = page1[-1]["fundingTime"]
    page2 = [_binance_funding_row(last_ts),                # doublon inter-pages (chevauchement startTime)
            _binance_funding_row(last_ts + STEP)]
    mock = _mock_sequence([
        (200, json.dumps(page1).encode()),
        (200, json.dumps(page2).encode()),
    ])
    rows = C.collect_binance_funding("BTCUSDT", 0, last_ts + 10 * STEP, http_get=mock)
    ts = [r["fundingTime"] for r in rows]
    assert ts == sorted(ts)
    assert len(ts) == len(set(ts))          # doublon inter-pages éliminé
    assert len(rows) == 1001                # 1000 + 1 nouvelle (le doublon ne compte qu'une fois)


def test_bybit_funding_pagination_backward_stops_at_start_boundary():
    """Même logique côté Bybit (pagination arrière) : une page pleine (200)
    force la continuation vers le passé, une page partielle est terminale."""
    newest = [_bybit_funding_row(3000 - i) for i in range(200)]   # 3000..2801, pleine
    oldest_ts = int(newest[-1]["fundingRateTimestamp"])           # 2801
    oldest = [_bybit_funding_row(oldest_ts), _bybit_funding_row(500)]  # 500 < start=1000
    mock = _mock_sequence([
        (200, json.dumps({"result": {"list": newest}}).encode()),
        (200, json.dumps({"result": {"list": oldest}}).encode()),
    ])
    rows = C.collect_bybit_funding("BTCUSDT", start_ms=1000, end_ms=3000, http_get=mock)
    ts = sorted(int(r["fundingRateTimestamp"]) for r in rows)
    assert ts[0] == oldest_ts                # 500 exclu : hors borne start
    assert ts[-1] == 3000
    assert 500 not in ts


# ── retry / rate limiting ──────────────────────────────────────────────

def test_retry_on_429_then_success(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)   # pas d'attente réelle en test
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] < 3:
            return 429, b"rate limited"
        return 200, json.dumps([_binance_funding_row(1000)]).encode()

    status, body, attempt = C.fetch_with_retry("http://x", http_get=flaky)
    assert status == 200
    assert attempt == 3
    assert calls["n"] == 3


def test_retry_exhausted_raises_not_infinite_loop(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def always_429(url):
        calls["n"] += 1
        return 429, b"rate limited"

    with pytest.raises(C.AcquisitionError):
        C.fetch_with_retry("http://x", http_get=always_429)
    assert calls["n"] == C.MAX_ATTEMPTS      # jamais plus que le budget fixé


def test_non_retryable_4xx_raises_immediately(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def bad_request(url):
        calls["n"] += 1
        return 400, b"bad params"

    with pytest.raises(C.AcquisitionError):
        C.fetch_with_retry("http://x", http_get=bad_request)
    assert calls["n"] == 1                   # pas de retry gaspillé sur une 400


# ── intégrité / persistance idempotente ─────────────────────────────────

def test_malformed_response_rejected_not_silently_empty():
    mock = _mock_sequence([(200, b'{"unexpected": "shape"}')])
    with pytest.raises(C.AcquisitionError):
        C.binance_funding_page("BTCUSDT", 0, 1000, http_get=mock)


def test_raw_page_never_overwritten_silently_on_conflict(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    body1 = json.dumps([_binance_funding_row(1000)]).encode()
    body2 = json.dumps([_binance_funding_row(1000, rate="0.9999")]).encode()  # contenu DIFFERENT, même page_key
    mock1 = _mock_sequence([(200, body1)])
    C.binance_funding_page("BTCUSDT", 0, 1000, http_get=mock1)

    mock2 = _mock_sequence([(200, body2)])
    with pytest.raises(C.AcquisitionError):
        C.binance_funding_page("BTCUSDT", 0, 1000, http_get=mock2)


def test_rerun_with_identical_page_is_a_noop_not_a_rewrite():
    body = json.dumps([_binance_funding_row(1000)]).encode()
    mock1 = _mock_sequence([(200, body)])
    rows1 = C.binance_funding_page("BTCUSDT", 0, 1000, http_get=mock1)
    path = C._raw_page_path("binance", "funding", "BTCUSDT", "0_1000")
    mtime1 = path.stat().st_mtime_ns

    mock2 = _mock_sequence([(200, body)])   # même contenu exact
    rows2 = C.binance_funding_page("BTCUSDT", 0, 1000, http_get=mock2)
    assert rows1 == rows2
    # contenu identique -> pas d'erreur, fichier resté cohérent
    assert hashlib_equal(path, body)


def hashlib_equal(path: Path, body: bytes) -> bool:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(body).hexdigest()


def test_acquisition_log_has_required_schema_fields():
    body = json.dumps([_binance_funding_row(1000)]).encode()
    mock = _mock_sequence([(200, body)])
    C.binance_funding_page("BTCUSDT", 0, 1000, http_get=mock)
    line = json.loads(C.LOG.read_text().strip().splitlines()[-1])
    required = {"request_id", "retrieved_at_utc", "venue", "endpoint", "symbol",
               "parameters", "http_status", "attempt_number",
               "response_byte_length", "response_sha256", "first_timestamp",
               "last_timestamp", "row_count"}
    assert required.issubset(line.keys())
    assert line["venue"] == "binance"
    assert line["row_count"] == 1
