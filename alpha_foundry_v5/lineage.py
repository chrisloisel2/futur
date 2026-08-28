from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from .contracts import ExperimentSpec, ResearchStage, TimeWindow
from .hashing import atomic_write_json


class ExperimentRegistry:
    """Immutable experiment-spec registry with discovery/confirmation overlap protection."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, experiment_id: str) -> Path:
        return self.root / (str(experiment_id) + ".json")

    def list_specs(self) -> List[ExperimentSpec]:
        out = []
        for path in sorted(self.root.glob("*.json")):
            row = json.loads(path.read_text(encoding="utf-8"))
            row.pop("digest", None)
            row["stage"] = ResearchStage(row["stage"])
            row["window"] = TimeWindow(**row["window"])
            out.append(ExperimentSpec(**row))
        return out

    def register(self, spec: ExperimentSpec) -> None:
        target = self._path(spec.experiment_id)
        if target.exists():
            raise FileExistsError("experiment spec is immutable: %s" % spec.experiment_id)
        if spec.stage == ResearchStage.INDEPENDENT_CONFIRMATION:
            for prior in self.list_specs():
                if prior.hypothesis_digest != spec.hypothesis_digest:
                    continue
                if prior.stage == ResearchStage.DEV_DISCOVERY and prior.window.overlaps(spec.window):
                    raise ValueError("confirmation overlaps discovery window")
                if prior.stage == ResearchStage.DEV_DISCOVERY and int(spec.window.start_ns) <= int(prior.window.stop_ns):
                    raise ValueError("confirmation must start strictly after discovery")
        payload = asdict(spec)
        payload["stage"] = spec.stage.value
        payload["digest"] = spec.digest
        atomic_write_json(str(target), payload)
