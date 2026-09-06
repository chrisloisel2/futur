from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .contracts import TimeWindow
from .hashing import atomic_write_json, sha256_file, sha256_obj


@dataclass(frozen=True)
class PartitionFingerprint:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class DatasetManifest:
    schema_version: str
    dataset_name: str
    window: TimeWindow
    domains: tuple[str, ...]
    sources: tuple[str, ...]
    partitions: tuple[PartitionFingerprint, ...]
    row_count: int
    code_commit: str
    pit_policy: str
    clock_policy: str
    notes: str = ""

    @property
    def digest(self) -> str:
        return sha256_obj(self)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["digest"] = self.digest
        return payload


def fingerprint_partitions(paths: Sequence[str]) -> tuple[PartitionFingerprint, ...]:
    rows: list[PartitionFingerprint] = []
    for raw in sorted(set(str(p) for p in paths)):
        path = Path(raw)
        if not path.is_file():
            raise FileNotFoundError(raw)
        rows.append(PartitionFingerprint(path=str(path), size_bytes=int(path.stat().st_size), sha256=sha256_file(str(path))))
    if not rows:
        raise ValueError("dataset manifest requires at least one partition")
    return tuple(rows)


def write_manifest(manifest: DatasetManifest, path: str) -> None:
    target = Path(path)
    if target.exists():
        raise FileExistsError("dataset manifests are immutable: %s" % target)
    atomic_write_json(str(target), manifest.to_dict())


def load_manifest(path: str) -> DatasetManifest:
    row = json.loads(Path(path).read_text(encoding="utf-8"))
    row.pop("digest", None)
    row["window"] = TimeWindow(**row["window"])
    row["domains"] = tuple(row["domains"])
    row["sources"] = tuple(row["sources"])
    row["partitions"] = tuple(PartitionFingerprint(**p) for p in row["partitions"])
    return DatasetManifest(**row)


def verify_manifest(manifest: DatasetManifest) -> dict[str, object]:
    changed = []
    missing = []
    for part in manifest.partitions:
        path = Path(part.path)
        if not path.is_file():
            missing.append(part.path)
            continue
        if int(path.stat().st_size) != int(part.size_bytes) or sha256_file(str(path)) != part.sha256:
            changed.append(part.path)
    return {"ok": not changed and not missing, "missing": tuple(missing), "changed": tuple(changed), "manifest_digest": manifest.digest}
