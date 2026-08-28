from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

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
    domains: Tuple[str, ...]
    sources: Tuple[str, ...]
    partitions: Tuple[PartitionFingerprint, ...]
    row_count: int
    code_commit: str
    pit_policy: str
    clock_policy: str
    notes: str = ""

    @property
    def digest(self) -> str:
        return sha256_obj(self)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["digest"] = self.digest
        return payload


def fingerprint_partitions(paths: Sequence[str]) -> Tuple[PartitionFingerprint, ...]:
    rows: List[PartitionFingerprint] = []
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


def verify_manifest(manifest: DatasetManifest) -> Dict[str, object]:
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
