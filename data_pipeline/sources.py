from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "data_sources.yml"


@dataclass(frozen=True)
class SourceSpec:
    """Normalized public data-source definition."""

    name: str
    family: str
    endpoint: str
    cadence: str
    market_type: str = "global"
    enabled: bool = True
    priority: int = 100
    rate_limit_per_minute: Optional[int] = None
    ffill_limit: Optional[str] = None
    license: str = "public"
    storage: Mapping[str, Any] = field(default_factory=dict)
    schema: List[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SourceSpec":
        required = ["name", "family", "endpoint", "cadence"]
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValueError("Source spec missing required keys: %s" % ", ".join(missing))
        return cls(
            name=str(payload["name"]),
            family=str(payload["family"]),
            endpoint=str(payload["endpoint"]),
            cadence=str(payload["cadence"]),
            market_type=str(payload.get("market_type", "global")),
            enabled=bool(payload.get("enabled", True)),
            priority=int(payload.get("priority", 100)),
            rate_limit_per_minute=(
                int(payload["rate_limit_per_minute"])
                if payload.get("rate_limit_per_minute") is not None
                else None
            ),
            ffill_limit=payload.get("ffill_limit"),
            license=str(payload.get("license", "public")),
            storage=dict(payload.get("storage") or {}),
            schema=list(payload.get("schema") or []),
            notes=str(payload.get("notes", "")),
        )


def load_source_registry(path: Optional[Path] = None, enabled_only: bool = True) -> Dict[str, SourceSpec]:
    """Load the YAML source registry keyed by source name."""

    registry_path = path or DEFAULT_REGISTRY_PATH
    if not registry_path.exists():
        raise FileNotFoundError("Data source registry not found: %s" % registry_path)

    with registry_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}

    raw_sources = payload.get("sources") or []
    specs = [SourceSpec.from_mapping(item) for item in raw_sources]
    if enabled_only:
        specs = [spec for spec in specs if spec.enabled]

    specs = sorted(specs, key=lambda item: (item.priority, item.name))
    return {spec.name: spec for spec in specs}


def source_names(path: Optional[Path] = None, enabled_only: bool = True) -> List[str]:
    return list(load_source_registry(path=path, enabled_only=enabled_only).keys())
