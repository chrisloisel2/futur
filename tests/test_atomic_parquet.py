"""
tests/test_atomic_parquet.py
─────────────────────────────────────────────────────────────────────────────
Tests de corruption / concurrence pour l'écriture atomique (Phase 31).

Gate (doit passer avant tout hedge) :
    python3 -m pytest tests/test_atomic_parquet.py -q
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.data import atomic_parquet as ap


def make_rows(offset: int = 0, n: int = 10) -> pd.DataFrame:
    ts = pd.date_range("2026-01-01", periods=n, freq="1H", tz="UTC") + pd.Timedelta(hours=offset)
    return pd.DataFrame({"datetime": ts, "close": range(offset, offset + n)})


# 1. write normal → lisible
def test_atomic_write_readable(tmp_path):
    t = tmp_path / "BTCUSDT_1h_enriched.parquet"
    ap.atomic_write_parquet(make_rows(0, 10), t)
    ap.validate_parquet_readable(t)
    assert len(pd.read_parquet(t)) == 10


# 2. append normal → rows augmentent, monotones
def test_append_grows_monotonic(tmp_path):
    t = tmp_path / "BTCUSDT_1h_enriched.parquet"
    assert ap.append_enriched_atomic(t, make_rows(0, 10)) == 10
    assert ap.append_enriched_atomic(t, make_rows(10, 10)) == 20
    out = pd.read_parquet(t)
    assert out["datetime"].is_monotonic_increasing
    assert not out["datetime"].duplicated().any()


# 3. crash avant os.replace → ancien fichier reste valide
def test_crash_before_replace_keeps_old(tmp_path, monkeypatch):
    t = tmp_path / "BTCUSDT_1h_enriched.parquet"
    ap.atomic_write_parquet(make_rows(0, 5), t)
    old_sha = t.read_bytes()
    monkeypatch.setattr(ap.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        ap.atomic_write_parquet(make_rows(99, 5), t)
    assert t.read_bytes() == old_sha          # inchangé
    assert not list(tmp_path.glob("*.tmp"))   # temp nettoyé


# 4. temp invalide → ancien fichier reste valide
def test_invalid_temp_keeps_old(tmp_path, monkeypatch):
    t = tmp_path / "BTCUSDT_1h_enriched.parquet"
    ap.atomic_write_parquet(make_rows(0, 5), t)
    old = t.read_bytes()
    # forcer un temp corrompu : to_parquet écrit des octets invalides
    monkeypatch.setattr(pd.DataFrame, "to_parquet",
                        lambda self, p, **k: Path(p).write_bytes(b"NOTPARQUET"))
    with pytest.raises(ValueError):
        ap.atomic_write_parquet(make_rows(99, 5), t)
    assert t.read_bytes() == old


# 5. fichier existant corrompu → quarantine + exception, jamais écrasé
def test_corrupt_existing_is_never_overwritten(tmp_path):
    target = tmp_path / "LINKUSDT_1h_enriched.parquet"
    target.write_bytes(b"")  # corrompu
    with pytest.raises(RuntimeError):
        ap.append_enriched_atomic(target, make_rows(0, 10))
    assert not target.exists()                      # déplacé en quarantaine
    assert list(tmp_path.glob("*.corrupt.*"))       # quarantaine présente


# 6. concurrence 4 writers → parquet final valide
def test_concurrent_appends_preserve_valid_parquet(tmp_path):
    target = tmp_path / "BNBUSDT_1h_enriched.parquet"
    batches = [make_rows(offset=i * 10, n=10) for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(lambda df: ap.append_enriched_atomic(target, df), batches))
    out = pd.read_parquet(target)
    assert len(out) == 40
    assert out["datetime"].is_monotonic_increasing
    assert not out["datetime"].duplicated().any()
    ap.validate_parquet_readable(target)


# 7. zéro ligne → refus
def test_empty_refused(tmp_path):
    t = tmp_path / "X_1h_enriched.parquet"
    empty = pd.DataFrame({"datetime": pd.to_datetime([], utc=True), "close": []})
    with pytest.raises(ValueError):
        ap.atomic_write_parquet(empty, t)
    assert not t.exists()


# 8. magic bytes invalides → refus à la validation
def test_invalid_magic_refused(tmp_path):
    t = tmp_path / "bad.parquet"
    t.write_bytes(b"GARBAGE_DATA_NOT_PARQUET")
    with pytest.raises(ValueError):
        ap.validate_parquet_readable(t)
