"""announcement_body_archive: scope from the tape, network failures are facts not crashes,
archives are never silently overwritten, and nothing about prices is ever computed."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import announcement_body_archive as A  # noqa: E402


def test_binance_article_code_is_parsed_from_the_url():
    assert A.binance_code("https://www.binance.com/en/support/announcement/ca3196e008da448da107d397bae0b4b0") == "ca3196e008da448da107d397bae0b4b0"
    assert A.binance_code("https://www.binance.com/en/support/announcement/detail/abcdef0123456789abcdef0123456789") == "abcdef0123456789abcdef0123456789"
    assert A.binance_code("https://www.okx.com/en-eu/help/okx-to-list-slx") is None
    assert A.url_key("https://a") != A.url_key("https://b") and len(A.url_key("https://a")) == 20


def test_scope_reads_the_real_tape_and_covers_h2_and_h3():
    h2 = A.scope_urls("h2"); h3 = A.scope_urls("h3")
    assert len(h2) == 174 and len(h3) == 56
    assert all(v["source"] == "binance" and v["event_ids"] for v in h2.values())
    assert all("h2" in v["scopes"] for v in h2.values())


def test_fetch_body_turns_any_failure_into_a_fact(monkeypatch):
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    import http.client
    monkeypatch.setattr(A, "_get", lambda url, timeout=30: (_ for _ in ()).throw(http.client.IncompleteRead(b"x")))
    r = A.fetch_body("https://www.binance.com/en/support/announcement/" + "a" * 32, "binance")
    assert r["payload"] is None and r["error"] and r["http_status"] is None      # jamais d'exception qui remonte
    from urllib.error import HTTPError
    monkeypatch.setattr(A, "_get", lambda url, timeout=30: (_ for _ in ()).throw(HTTPError(url, 404, "no", {}, None)))
    assert A.fetch_body("https://x/announcement/" + "a" * 32, "binance")["http_status"] == 404
    calls = []
    def rl(url, timeout=30):
        calls.append(url); raise HTTPError(url, 429, "rate", {}, None)
    monkeypatch.setattr(A, "_get", rl)
    r = A.fetch_body("https://x/announcement/" + "a" * 32, "binance")
    assert r["error"] == "HTTP 429" and len(calls) == A.RETRIES                  # recul, puis abandon propre


def test_store_record_is_versioned_and_extracts(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STORE", tmp_path / "store"); monkeypatch.setattr(A, "INDEX", tmp_path / "index.jsonl")
    meta = {"url": "https://www.binance.com/en/support/announcement/" + "a" * 32, "source": "binance", "event_ids": ["e1"], "titles": ["T"], "scopes": ["h2"]}
    body = {"node": "root", "child": [{"node": "element", "tag": "p", "child": [{"node": "text", "text": "2025-05-02 08:30 (UTC): ABCUSDT Perpetual Contract opens trading"}]}]}
    payload = {"code": "000000", "data": {"title": "Binance Futures Will Launch ABCUSDT", "body": json.dumps(body), "releaseDate": 1746170000000}}
    rec = A.store_record(meta, {"http_status": 200, "payload_kind": "cms_json", "payload": payload, "error": None})
    assert rec["body_chars"] > 0 and rec["extracted"]["trading_start_ts"] == "2025-05-02T08:30:00+00:00" and rec["no_alpha_test"] is True
    same = A.store_record(meta, {"http_status": 200, "payload_kind": "cms_json", "payload": payload, "error": None})
    assert same["fetched_at_local"] == rec["fetched_at_local"]                    # corps identique : pas de reecriture
    payload2 = json.loads(json.dumps(payload)); payload2["data"]["body"] = json.dumps({"node": "root", "child": [{"node": "text", "text": "different"}]})
    A.store_record(meta, {"http_status": 200, "payload_kind": "cms_json", "payload": payload2, "error": None})
    archived = list((tmp_path / "store" / "binance").glob("*.*.json"))
    assert archived and json.loads(archived[0].read_text())["extracted"]["trading_start_ts"] == "2025-05-02T08:30:00+00:00"
    assert "body_rewritten" in (tmp_path / "index.jsonl").read_text()


def test_empty_body_is_recorded_without_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STORE", tmp_path / "s"); monkeypatch.setattr(A, "INDEX", tmp_path / "i.jsonl")
    meta = {"url": "https://x/announcement/" + "b" * 32, "source": "binance", "event_ids": ["e"], "titles": ["t"], "scopes": ["h3"]}
    rec = A.store_record(meta, {"http_status": 429, "payload_kind": "cms_json", "payload": None, "error": "HTTP 429"})
    assert rec["body_chars"] == 0 and rec["extracted"] is None and rec["error"] == "HTTP 429"
