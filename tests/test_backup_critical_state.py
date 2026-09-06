"""
tests/test_backup_critical_state.py — sauvegarde du périmètre critique
(scripts/backup_critical_state.py).

Ce que ces tests protègent :
  - la partition du JOUR n'est jamais archivée (le collecteur y écrit ; une
    archive tronquée comptée comme sauvegardée est pire qu'une archive
    absente) ;
  - `verify` distingue un fichier ABSENT d'un fichier ALTÉRÉ — ce ne sont pas
    la même panne et elles n'appellent pas la même réponse ;
  - le périmètre reste motivé : une entrée sans motif dérive, parce que
    personne n'ose retirer une ligne dont il ignore pourquoi elle est là.
"""
from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import scripts.backup_critical_state as B


def test_scope_entries_all_carry_a_reason():
    for path, why in B.CRITICAL + B.EXCLUDED:
        assert why and len(why) > 20, f"{path} : motif absent ou trop court"


def test_nothing_is_both_kept_and_excluded():
    assert not ({p for p, _ in B.CRITICAL} & {p for p, _ in B.EXCLUDED})


def test_the_sealed_forward_evidence_is_in_scope():
    """Les ledgers scellés sont append-only donc non reconstructibles : les
    perdre, c'est perdre la preuve forward elle-même."""
    assert "reports/live_alpha_lab" in {p for p, _ in B.CRITICAL}


def test_derived_and_public_data_is_out_of_scope():
    """48 Go dérivés et périmés = 60 % du volume pour 0 % du risque. Les
    sauvegarder rendrait la restauration assez lourde pour n'être jamais
    testée — la façon dont les sauvegardes échouent en pratique."""
    excluded = {p for p, _ in B.EXCLUDED}
    assert "data/enriched" in excluded
    assert "data/derivatives_backfill" in excluded


def test_pack_never_touches_the_partition_being_written(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    src = tmp_path / "src" / "symbol=XUSDT"
    for day in ("2020-01-01", today):
        d = src / f"date={day}"
        d.mkdir(parents=True)
        (d / "part-0.parquet").write_bytes(b"x" * 32)
    monkeypatch.setattr(B, "ROOT", tmp_path)
    r = B.pack(tmp_path / "out", rel="src/symbol=XUSDT")
    assert r["n_archives_created"] == 1
    assert r["n_open_partitions_skipped"] == 1
    names = [f.name for f in (tmp_path / "out").glob("*.tar.gz")]
    assert not any(today in n for n in names)


def test_pack_is_idempotent_and_leaves_the_original_alone(tmp_path, monkeypatch):
    src = tmp_path / "src" / "symbol=XUSDT" / "date=2020-01-01"
    src.mkdir(parents=True)
    (src / "part-0.parquet").write_bytes(b"y" * 64)
    monkeypatch.setattr(B, "ROOT", tmp_path)
    first = B.pack(tmp_path / "out", rel="src/symbol=XUSDT")
    second = B.pack(tmp_path / "out", rel="src/symbol=XUSDT")
    assert first["n_archives_created"] == 1
    assert second["n_archives_created"] == 0 and second["n_already_present"] == 1
    assert (src / "part-0.parquet").read_bytes() == b"y" * 64   # jamais supprimé


def test_pack_leaves_no_partial_archive_under_its_final_name(tmp_path, monkeypatch):
    src = tmp_path / "src" / "symbol=XUSDT" / "date=2020-01-01"
    src.mkdir(parents=True)
    (src / "part-0.parquet").write_bytes(b"z" * 128)
    monkeypatch.setattr(B, "ROOT", tmp_path)
    B.pack(tmp_path / "out", rel="src/symbol=XUSDT")
    assert not list((tmp_path / "out").glob("*.tmp"))
    arc = next((tmp_path / "out").glob("*.tar.gz"))
    with tarfile.open(arc) as tf:            # lisible = complète
        assert len(tf.getmembers()) >= 1


def test_verify_separates_missing_from_altered(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("bon")
    b.write_text("bon aussi")
    manifest = {"files": {
        "a.txt": {"sha256": B.sha256_file(a), "bytes": 3},
        "b.txt": {"sha256": B.sha256_file(b), "bytes": 9},
        "c.txt": {"sha256": "0" * 64, "bytes": 1},
    }}
    b.write_text("ALTÉRÉ")
    r = B.verify(manifest, tmp_path)
    assert r["n_ok"] == 1
    assert r["n_changed"] == 1 and r["changed"] == ["b.txt"]
    assert r["n_missing"] == 1 and r["missing"] == ["c.txt"]


def test_test_restore_reports_pass_on_real_scope():
    """La seule preuve qui compte : une sauvegarde jamais restaurée est une
    hypothèse. Test lent-ish mais réel, sur le périmètre réel."""
    r = B.test_restore(n_sample=12)
    assert r["status"] in ("PASS", "NO_FILES_IN_SCOPE")
    if r["status"] == "PASS":
        assert r["n_identical"] == r["n_tested"]
