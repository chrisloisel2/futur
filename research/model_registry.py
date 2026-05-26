"""
research/model_registry.py — Model Registry (versioning + staging)

Usage:
  registry.register("long_xgb", model, metadata={"fold": "2024", "pf": 1.55})
  model, meta = registry.get_latest("long_xgb", stage="production")
  registry.promote("long_xgb", version="2", stage="production")
"""
from __future__ import annotations

import json
import pickle
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


_MODELS_DIR = Path(__file__).parent / "models"

STAGES = ("staging", "production", "archived")


class ModelRegistry:
    def __init__(self, models_dir: Optional[Path] = None):
        self._dir = Path(models_dir) if models_dir else _MODELS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        model: Any,
        metadata: dict[str, Any] = {},
        scaler: Any = None,
        stage: str = "staging",
    ) -> str:
        version = self._next_version(name)
        dest = self._version_dir(name, version)
        dest.mkdir(parents=True, exist_ok=True)

        with open(dest / "model.pkl", "wb") as f:
            pickle.dump(model, f)

        if scaler is not None:
            with open(dest / "scaler.pkl", "wb") as f:
                pickle.dump(scaler, f)

        meta = {
            "name":       name,
            "version":    version,
            "stage":      stage,
            "registered": datetime.now(timezone.utc).isoformat(),
            "has_scaler": scaler is not None,
            **metadata,
        }
        (dest / "metadata.json").write_text(json.dumps(meta, indent=2))
        return version

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, name: str, version: str) -> tuple[Any, dict]:
        dest = self._version_dir(name, version)
        if not dest.exists():
            raise FileNotFoundError(f"{name} v{version} not found")

        with open(dest / "model.pkl", "rb") as f:
            model = pickle.load(f)

        meta = json.loads((dest / "metadata.json").read_text())
        scaler_path = dest / "scaler.pkl"
        if scaler_path.exists():
            with open(scaler_path, "rb") as f:
                meta["_scaler"] = pickle.load(f)

        return model, meta

    def get_latest(self, name: str, stage: Optional[str] = None) -> tuple[Any, dict]:
        versions = self.list_versions(name)
        if not versions:
            raise FileNotFoundError(f"No versions for model '{name}'")
        if stage:
            versions = [v for v in versions if v.get("stage") == stage]
            if not versions:
                raise FileNotFoundError(f"No '{stage}' version for model '{name}'")
        latest = versions[-1]  # sorted ascending by version number
        return self.get(name, latest["version"])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def promote(self, name: str, version: str, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}")
        dest = self._version_dir(name, version)
        meta_path = dest / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["stage"] = stage
        meta["promoted_at"] = datetime.now(timezone.utc).isoformat()
        meta_path.write_text(json.dumps(meta, indent=2))

    def archive_all_except(self, name: str, keep_version: str) -> None:
        for v in self.list_versions(name):
            if v["version"] != keep_version and v.get("stage") == "production":
                self.promote(name, v["version"], "archived")

    # ------------------------------------------------------------------
    # Listing & comparison
    # ------------------------------------------------------------------

    def list_versions(self, name: str) -> list[dict]:
        model_dir = self._dir / name
        if not model_dir.exists():
            return []
        versions = []
        for p in sorted(model_dir.iterdir()):
            meta_path = p / "metadata.json"
            if meta_path.exists():
                versions.append(json.loads(meta_path.read_text()))
        return versions

    def list_models(self) -> list[str]:
        return [d.name for d in self._dir.iterdir() if d.is_dir()]

    def diff(self, name: str, v1: str, v2: str) -> dict:
        _, m1 = self.get(name, v1)
        _, m2 = self.get(name, v2)
        shared_keys = set(m1) & set(m2) - {"_scaler", "registered", "promoted_at"}
        return {
            k: {"v1": m1.get(k), "v2": m2.get(k)}
            for k in shared_keys
            if m1.get(k) != m2.get(k)
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _next_version(self, name: str) -> str:
        existing = self.list_versions(name)
        if not existing:
            return "1"
        last = max(int(v["version"]) for v in existing)
        return str(last + 1)

    def _version_dir(self, name: str, version: str) -> Path:
        return self._dir / name / version


# Module-level singleton
registry = ModelRegistry()
