from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Mapping

import pandas as pd

from .hashing import atomic_write_json, sha256_file, sha256_obj


class ArtifactStore:
    """Immutable per-experiment artifact directory with a cryptographic seal."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def experiment_dir(self, experiment_id: str) -> Path:
        return self.root / str(experiment_id)

    def _ensure_open(self, experiment_id: str) -> Path:
        out = self.experiment_dir(experiment_id)
        if (out / "SEAL.json").exists():
            raise RuntimeError("experiment artifacts are sealed: %s" % experiment_id)
        out.mkdir(parents=True, exist_ok=True)
        return out

    def write_json(self, experiment_id: str, name: str, payload: Mapping[str, object]) -> str:
        out = self._ensure_open(experiment_id) / name
        if out.exists():
            raise FileExistsError(str(out))
        atomic_write_json(str(out), dict(payload))
        return str(out)

    def write_parquet(self, experiment_id: str, name: str, frame: pd.DataFrame) -> str:
        out = self._ensure_open(experiment_id) / name
        if out.exists():
            raise FileExistsError(str(out))
        frame.to_parquet(out, index=False)
        return str(out)

    def seal(self, experiment_id: str, metadata: Mapping[str, object]) -> Dict[str, object]:
        out = self._ensure_open(experiment_id)
        files = []
        for path in sorted(p for p in out.iterdir() if p.is_file() and p.name != "SEAL.json"):
            files.append({"name": path.name, "size_bytes": int(path.stat().st_size), "sha256": sha256_file(str(path))})
        payload = {"experiment_id": experiment_id, "files": files, "metadata": dict(metadata)}
        payload["seal_digest"] = sha256_obj(payload)
        atomic_write_json(str(out / "SEAL.json"), payload)
        return payload

    def verify(self, experiment_id: str) -> Dict[str, object]:
        seal_path = self.experiment_dir(experiment_id) / "SEAL.json"
        if not seal_path.exists():
            return {"ok": False, "reason": "unsealed"}
        payload = json.loads(seal_path.read_text(encoding="utf-8"))
        changed = []
        for item in payload.get("files", []):
            path = self.experiment_dir(experiment_id) / item["name"]
            if not path.is_file() or int(path.stat().st_size) != int(item["size_bytes"]) or sha256_file(str(path)) != item["sha256"]:
                changed.append(item["name"])
        copy = dict(payload)
        expected = copy.pop("seal_digest", "")
        return {"ok": not changed and sha256_obj(copy) == expected, "changed": tuple(changed), "seal_digest": expected}
