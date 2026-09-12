"""vision_free_backfill: pure planning, resilient fetching (no exception escapes), checksum quarantine,
not-yet-published periods re-probed, disk cap honoured under threads, dry run touches nothing."""
import http.client
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import vision_free_backfill as B  # noqa: E402
from data_lake.collectors import vision_manifest as VM  # noqa: E402
from data_lake.collectors import vision_paths as P  # noqa: E402

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def _zipbytes(name="x.csv", rows=(("open_time", "o"), (1746174600000, "1"))):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, "\n".join(",".join(map(str, r)) for r in rows) + "\n")
    return buf.getvalue()


# ------------------------------------------------------------------ planification pure
def test_window_days_and_urls_are_utc():
    assert P.days_for_window("2025-05-02T08:30:00+00:00") == ["2025-05-02"]
    assert P.days_for_window("2025-05-02T00:10:00+00:00") == ["2025-05-01", "2025-05-02"]
    assert P.days_for_window("2025-05-02T19:00:00+00:00") == ["2025-05-02", "2025-05-03"]
    assert P.days_for_window("2025-05-02T02:00:00+08:00") == ["2025-05-01", "2025-05-02"]      # t0 = 18:00 UTC la veille
    assert P.months_for_window("2025-05-31T20:00:00+00:00") == ["2025-05", "2025-06"]
    assert P.url_for("aggTrades", "SXTUSDT", "2025-05-02").endswith("/daily/aggTrades/SXTUSDT/SXTUSDT-aggTrades-2025-05-02.zip")
    assert P.url_for("markPriceKlines", "SXTUSDT", "2025-05-02").endswith("/markPriceKlines/SXTUSDT/1m/SXTUSDT-1m-2025-05-02.zip")
    assert P.url_for("fundingRate", "SXTUSDT", "2025-05").endswith("/monthly/fundingRate/SXTUSDT/SXTUSDT-fundingRate-2025-05.zip")


def test_plan_counts_without_network():
    ev = [{"event_id": "a", "symbol": "AUSDT", "t0": "2025-05-02T08:30:00+00:00"}, {"event_id": "b", "symbol": "BUSDT", "t0": "2025-05-02T19:00:00+00:00"}]
    p = B.plan(ev, ["aggTrades", "fundingRate"]); assert p["files"] == 1 + 1 + 2 + 1 and p["days_per_event"] == {1: 1, 2: 1}


def test_period_end_and_not_yet_published():
    assert B.period_end_utc("2026-09") == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert B.period_end_utc("2026-12") == datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert B.is_not_yet_published("2026-09", NOW) is True           # mois en cours : un 404 n'est pas un trou
    assert B.is_not_yet_published("2026-08", NOW) is False
    assert B.is_not_yet_published("2026-09-10", NOW) is True        # hier : delai de publication
    assert B.is_not_yet_published("2026-08-01", NOW) is False


# ------------------------------------------------------------------ robustesse du telechargement
def test_incomplete_read_does_not_escape_fetch_one(monkeypatch, tmp_path):
    """http.client.IncompleteRead n'est pas un OSError : non rattrape, il faisait tomber toute la passe."""
    assert not issubclass(http.client.IncompleteRead, (OSError,))
    monkeypatch.setattr(B, "_get", lambda url, timeout=120: (_ for _ in ()).throw(http.client.IncompleteRead(b"partial")))
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    f = {"dataset": "aggTrades", "period": "2025-05-02", "url": "https://x/a.zip", "checksum_url": "https://x/a.zip.CHECKSUM", "local": str(tmp_path / "a.zip"), "priority": "P0"}
    assert B.fetch_one(f, {"status": "ok", "bytes": 10})["status"] == "error"


def test_fetch_one_success_path(monkeypatch, tmp_path):
    data = _zipbytes()
    monkeypatch.setattr(B, "_get", lambda url, timeout=120: (b"%s  a.zip" % VM.hashlib.sha256(data).hexdigest().encode()) if url.endswith(".CHECKSUM") else data)
    f = {"dataset": "markPriceKlines", "period": "2025-05-02", "url": "https://x/a.zip", "checksum_url": "https://x/a.zip.CHECKSUM", "local": str(tmp_path / "a.zip"), "priority": "P0"}
    r = B.fetch_one(f, {"status": "ok", "bytes": len(data)})
    assert r["status"] == "ok" and r["checksum_verified"] is True and r["rows"] == 1 and r["first_ts"] == "2025-05-02T08:30:00.000+00:00"
    assert Path(f["local"]).exists() and not list(tmp_path.glob("*.part"))


def test_checksum_mismatch_quarantines_so_a_rerun_redownloads(monkeypatch, tmp_path):
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(B, "_get", lambda url, timeout=120: b"deadbeef  a.zip" if url.endswith(".CHECKSUM") else _zipbytes())
    f = {"dataset": "aggTrades", "period": "2025-05-02", "url": "https://x/a.zip", "checksum_url": "https://x/a.zip.CHECKSUM", "local": str(tmp_path / "a.zip"), "priority": "P0"}
    r = B.fetch_one(f, {"status": "ok", "bytes": 10})
    assert r["status"] == "error" and "mismatch" in r["error"] and not Path(f["local"]).exists() and (tmp_path / "a.zip.bad").exists()


def test_unverifiable_checksum_stays_ok_but_is_counted(monkeypatch, tmp_path):
    data = _zipbytes()
    def get(url, timeout=120):
        if url.endswith(".CHECKSUM"):
            raise ConnectionResetError("reset")
        return data
    monkeypatch.setattr(B, "_get", get); monkeypatch.setattr(B.time, "sleep", lambda s: None)
    f = {"dataset": "aggTrades", "period": "2025-05-02", "url": "https://x/a.zip", "checksum_url": "https://x/a.zip.CHECKSUM", "local": str(tmp_path / "a.zip"), "priority": "P0"}
    r = B.fetch_one(f, {"status": "ok", "bytes": len(data)})
    assert r["status"] == "ok" and r["checksum_verified"] is None
    assert VM.coverage([r])["checksum_unverified"] == 1                      # compte, donc rapportable honnetement


def test_corrupt_archive_is_not_counted_as_covered(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "_get", lambda url, timeout=120: b"garbage" if not url.endswith(".CHECKSUM") else b"%s  a.zip" % VM.hashlib.sha256(b"garbage").hexdigest().encode())
    f = {"dataset": "aggTrades", "period": "2025-05-02", "url": "https://x/a.zip", "checksum_url": "https://x/a.zip.CHECKSUM", "local": str(tmp_path / "a.zip"), "priority": "P0"}
    r = B.fetch_one(f, {"status": "ok", "bytes": 7})
    assert r["status"] == "error" and VM.coverage([r])["complete"]["aggTrades"] is False


def test_404_on_an_unfinished_period_is_pending_not_a_hole(tmp_path):
    f = {"dataset": "fundingRate", "period": "2026-09", "url": "https://x/f.zip", "checksum_url": "https://x/f.zip.CHECKSUM", "local": str(tmp_path / "f.zip"), "priority": "P1"}
    assert B.fetch_one(f, {"status": "404"}, now=NOW)["status"] == "not_yet_published"
    old = {**f, "period": "2025-01"}
    assert B.fetch_one(old, {"status": "404"}, now=NOW)["status"] == "404"


def test_probe_reprobes_pending_404_but_not_settled_ones(monkeypatch, tmp_path):
    cache = tmp_path / "c.json"
    ev = [{"event_id": "a", "symbol": "AUSDT", "t0": "2026-09-05T08:30:00+00:00"}]
    urls = {f["url"]: f for f in P.files_for_event("AUSDT", ev[0]["t0"], ["fundingRate", "aggTrades"])}
    cache.write_text(json.dumps({u: {"status": "404", "bytes": 0} for u in urls}))
    asked = []
    monkeypatch.setattr(B, "_head", lambda url: asked.append(url) or {"status": "404", "bytes": 0})
    B.probe(ev, ["fundingRate", "aggTrades"], cache_path=cache, now=NOW)
    assert any("fundingRate" in u for u in asked)                            # mois en cours : re-sonde
    assert not any("aggTrades" in u for u in asked)                          # jour clos depuis 6 j : 404 definitif
    asked.clear(); B.probe(ev, ["fundingRate"], cache_path=cache, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert asked == []                                                        # periode close : 404 definitif, plus de HEAD


# ------------------------------------------------------------------ passe complete
def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(VM, "MANIFEST_ROOT", tmp_path / "man"); monkeypatch.setattr(VM, "LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(B, "probe", lambda *a, **k: {})
    ev = [{"event_id": "a", "symbol": "AUSDT", "t0": "2025-05-02T08:30:00+00:00"}]
    s = B.run(ev, ["aggTrades", "markPriceKlines"], dry=True, run_id="t")
    assert s["events"] == 1 and s["files_ok"] == 0 and not (tmp_path / "man").exists()      # un dry run n'ecrase aucun manifeste reel


def test_disk_cap_is_honoured_under_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(VM, "MANIFEST_ROOT", tmp_path / "man"); monkeypatch.setattr(VM, "LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(P, "LOCAL_ROOT", tmp_path / "store")                 # jamais d'ecriture dans le vrai entrepot
    files = P.files_for_event("AUSDT", "2025-05-02T08:30:00+00:00", ["aggTrades", "markPriceKlines", "metrics", "bookDepth", "klines"])
    monkeypatch.setattr(B, "probe", lambda *a, **k: {f["url"]: {"status": "ok", "bytes": 10**9} for f in files})
    def fake(f, info, verify_checksum=True, now=None):
        Path(f["local"]).parent.mkdir(parents=True, exist_ok=True); Path(f["local"]).write_bytes(b"x")
        return {**f, "status": "ok", "bytes": 10**9, "sha256": "a" * 64, "checksum_verified": True, "fetched_at_local": "now"}
    monkeypatch.setattr(B, "fetch_one", fake)
    s = B.run([{"event_id": "a", "symbol": "AUSDT", "t0": "2025-05-02T08:30:00+00:00"}], ["aggTrades", "markPriceKlines", "metrics", "bookDepth", "klines"], workers=8, max_gb=2.0, run_id="t")
    assert s["files_ok"] == 2 and s["files_skipped_cap"] == 3                 # le plafond tient malgre 8 fils
    m = json.loads((tmp_path / "man" / "a.json").read_text())
    assert m["bytes_on_disk"] == 2 * 10**9 and all(f.get("bytes") is None for f in m["files"] if f["status"] == "skipped")


def test_coverage_reports_use_only_the_given_events(tmp_path, monkeypatch):
    monkeypatch.setattr(VM, "MANIFEST_ROOT", tmp_path / "man")
    ev = B.load_events()[:2]
    for e in ev:
        VM.write(VM.build(e, [{"dataset": d, "period": e["t0"][:10], "status": "ok", "bytes": 1, "sha256": "a" * 64, "local": "x", "checksum_verified": True}
                              for d in ("markPriceKlines", "aggTrades", "indexPriceKlines")], "t"), tmp_path / "man")
    cov = B.coverage_reports(ev, out=tmp_path)
    assert cov["events"] == 2 and cov["manifests"] == 2 and cov["events_core_covered_free"] == 2
    rows = json.loads((tmp_path / "H2_AFTER_VISION_COVERAGE_MATRIX.json").read_text())["rows"]
    assert len(rows) == 2 and cov["no_return_computed"] is True
    assert all(not r["capacity_present"] for r in rows)                       # champ derive : jamais credite sans calcul
