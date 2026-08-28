from __future__ import annotations

import subprocess

import pytest

from alpha_foundry_v5.contracts import TimeWindow
from alpha_foundry_v5.manifest import (
    DatasetManifest,
    fingerprint_partitions,
    load_manifest,
    write_manifest,
)
from alpha_foundry_v5.repro import git_head, git_is_dirty, verify_code_commit


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)
    (tmp_path / "f.txt").write_text("v1")
    subprocess.run(["git", "add", "f.txt"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)
    return git_head(tmp_path)


def test_verify_code_commit_accepts_real_head_on_clean_tree(tmp_path):
    head = _init_repo(tmp_path)
    verify_code_commit(head, tmp_path)  # must not raise


def test_verify_code_commit_rejects_wrong_commit(tmp_path):
    _init_repo(tmp_path)
    with pytest.raises(ValueError, match="does not match git HEAD"):
        verify_code_commit("0" * 40, tmp_path)


def test_verify_code_commit_rejects_dirty_tree(tmp_path):
    head = _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("v2 -- uncommitted")
    assert git_is_dirty(tmp_path) is True
    with pytest.raises(ValueError, match="uncommitted changes"):
        verify_code_commit(head, tmp_path)


def test_load_manifest_round_trips_and_verifies(tmp_path):
    data_file = tmp_path / "part-00000.parquet"
    data_file.write_bytes(b"not a real parquet, just needs to be a stable file")
    manifest = DatasetManifest(
        schema_version="v1",
        dataset_name="test",
        window=TimeWindow(1, 10),
        domains=("book",),
        sources=("test",),
        partitions=fingerprint_partitions([str(data_file)]),
        row_count=1,
        code_commit="abc123",
        pit_policy="p",
        clock_policy="c",
    )
    out = tmp_path / "MANIFEST.json"
    write_manifest(manifest, str(out))

    loaded = load_manifest(str(out))
    assert loaded.digest == manifest.digest
    assert loaded == manifest


def test_load_manifest_digest_changes_if_partition_is_mutated_after_freeze(tmp_path):
    data_file = tmp_path / "part-00000.parquet"
    data_file.write_bytes(b"original content")
    manifest = DatasetManifest(
        schema_version="v1",
        dataset_name="test",
        window=TimeWindow(1, 10),
        domains=("book",),
        sources=("test",),
        partitions=fingerprint_partitions([str(data_file)]),
        row_count=1,
        code_commit="abc123",
        pit_policy="p",
        clock_policy="c",
    )
    out = tmp_path / "MANIFEST.json"
    write_manifest(manifest, str(out))
    loaded = load_manifest(str(out))

    data_file.write_bytes(b"mutated after the freeze")

    from alpha_foundry_v5.manifest import verify_manifest

    check = verify_manifest(loaded)
    assert check["ok"] is False
    assert str(data_file) in check["changed"]
