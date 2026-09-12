"""vision_manifest: timestamps only and always UTC, coverage with the index-or-premium core, versioned and
atomic writes that never silently overwrite, verification."""
import csv
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import vision_manifest as VM  # noqa: E402


def _zip(p: Path, name: str, rows):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(name, "\n".join(",".join(map(str, r)) for r in rows) + "\n")


def test_naive_iso_is_utc_not_local_time(monkeypatch):
    """Vision writes bookDepth / metrics timestamps as naive UTC. Reading them as local time shifted every
    manifest by the host offset (CEST = 2 h)."""
    for tz in ("Europe/Amsterdam", "America/New_York", "UTC"):
        monkeypatch.setenv("TZ", tz)
        try:
            import time as _t
            _t.tzset()
        except AttributeError:
            pass
        assert VM._ts_norm("2025-05-02 08:30:00") == "2025-05-02T08:30:00.000+00:00", tz
    assert VM._ts_norm("2025-05-02T08:30:00+02:00") == "2025-05-02T06:30:00.000+00:00"      # offset explicite : converti
    assert VM._ts_norm("1746174600000") == "2025-05-02T08:30:00.000+00:00"                   # ms
    assert VM._ts_norm("1746174600000000") == "2025-05-02T08:30:00.000+00:00"                # µs (piege Vision)
    assert VM._ts_norm("open_time") is None and VM._ts_norm("") is None


def test_timestamp_columns_match_the_vision_layouts(tmp_path):
    p = tmp_path / "X-aggTrades-2025-05-02.zip"
    _zip(p, "a.csv", [["agg_trade_id", "price", "quantity", "first", "last", "transact_time", "is_buyer_maker"],
                      [1, "0.5", "10", 1, 1, 1746174600000, "true"], [2, "0.6", "5", 2, 2, 1746174660000000, "false"]])
    info = VM.inspect_zip(p, "aggTrades")
    assert info["rows"] == 2 and info["first_ts"] == "2025-05-02T08:30:00.000+00:00" and info["last_ts"] == "2025-05-02T08:31:00.000+00:00"
    b = tmp_path / "X-bookTicker-2025-05-02.zip"          # col 5 = transaction_time, col 4 = best_ask_qty
    _zip(b, "b.csv", [["update_id", "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty", "transaction_time", "event_time"],
                      [1, "0.5", "100", "0.6", "200", 1746174600000, 1746174600001]])
    assert VM.inspect_zip(b, "bookTicker")["first_ts"] == "2025-05-02T08:30:00.000+00:00"
    d = tmp_path / "X-bookDepth-2025-05-02.zip"
    _zip(d, "d.csv", [["timestamp", "percentage", "depth", "notional"], ["2025-05-02 08:30:00", "1", "10", "1000"]])
    assert VM.inspect_zip(d, "bookDepth")["first_ts"] == "2025-05-02T08:30:00.000+00:00"


def test_unreadable_archive_reports_an_error(tmp_path):
    p = tmp_path / "broken.zip"; p.write_bytes(b"not a zip at all")
    assert "error" in VM.inspect_zip(p, "aggTrades")


def test_core_accepts_index_or_premium_and_counts_checksums():
    base = [{"dataset": "markPriceKlines", "status": "ok", "checksum_verified": True}, {"dataset": "aggTrades", "status": "ok", "checksum_verified": True}]
    with_index = VM.coverage(base + [{"dataset": "indexPriceKlines", "status": "ok", "checksum_verified": True}])
    with_prem = VM.coverage(base + [{"dataset": "premiumIndexKlines", "status": "ok", "checksum_verified": False}])
    neither = VM.coverage(base + [{"dataset": "indexPriceKlines", "status": "404"}])
    assert with_index["core_complete"] and with_prem["core_complete"] and not neither["core_complete"]
    assert with_prem["index_reference_complete"] and not neither["index_reference_complete"]
    assert with_prem["checksum_verified"] == 2 and with_prem["checksum_unverified"] == 1
    partial = VM.coverage([{"dataset": "aggTrades", "status": "ok"}, {"dataset": "aggTrades", "status": "404"}])
    assert partial["complete"]["aggTrades"] is False                      # un seul jour manquant suffit


def test_write_is_versioned_atomic_and_survives_a_corrupt_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(VM, "LOG", tmp_path / "log.jsonl")
    ev = {"event_id": "e1", "symbol": "XUSDT", "t0": "2025-05-02T08:30:00+00:00"}
    def man(run, b):
        return VM.build(ev, [{"dataset": "aggTrades", "period": "2025-05-02", "status": "ok", "bytes": b, "sha256": "a" * 64, "local": str(tmp_path / "f.zip"), "checksum_verified": True}], run)
    root = tmp_path / "man"
    p = VM.write(man("run1", 10), root); assert json.loads(p.read_text())["no_alpha_test"] is True
    VM.write(man("run2", 10), root)                                        # contenu identique : aucune archive
    assert not list(root.glob("e1.*.json"))
    VM.write(man("run3", 11), root)                                        # contenu different : archive
    assert (root / "e1.run1.json").exists() and json.loads(p.read_text())["run_id"] == "run3"   # run2 n'a jamais ete installe
    VM.write(man("run3", 12), root); VM.write(man("run3", 13), root)       # meme run_id reutilise : jamais d'ecrasement
    assert (root / "e1.run3.json").exists() and (root / "e1.run3.2.json").exists()
    (root / "e1.json").write_text("{ truncated")                           # manifeste illisible : archive tel quel, pas perdu
    VM.write(man("run4", 14), root)
    arch = [q for q in root.glob("e1.unreadable*.json")]; assert arch and arch[0].read_text() == "{ truncated"
    (root / "e1.json").write_text("null"); VM.write(man("run5", 15), root)  # JSON valide mais pas un dict : pas de crash
    assert set(VM.load_all(root)) == {"e1"}
    assert "manifest_rewritten" in (tmp_path / "log.jsonl").read_text()


def test_verify_detects_a_changed_file(tmp_path):
    f = tmp_path / "f.zip"; f.write_bytes(b"abc")
    m = VM.build({"event_id": "e", "symbol": "X", "t0": "2025-05-02T08:30:00+00:00"},
                 [{"dataset": "aggTrades", "period": "2025-05-02", "status": "ok", "bytes": 3, "sha256": VM.sha256_file(f), "local": str(f)}], "r")
    assert VM.verify(m)["ok"] is True
    f.write_bytes(b"xyz"); v = VM.verify(m); assert v["ok"] is False and v["sha_mismatch"] == [str(f)]
