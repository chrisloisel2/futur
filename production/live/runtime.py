from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from core.runtime import LivePredictor


@dataclass
class CanonicalLiveRuntime:
    predictor: LivePredictor
    run_dir: Optional[Path] = None

    @classmethod
    def load_latest(cls, short_enabled: bool = True) -> "CanonicalLiveRuntime":
        predictor = LivePredictor.load_latest(short_enabled=short_enabled)
        return cls(predictor=predictor)

    @classmethod
    def load_from_run(cls, run_dir: str | Path, short_enabled: bool = True) -> "CanonicalLiveRuntime":
        path = Path(run_dir)
        predictor = LivePredictor.load_from_run(path, short_enabled=short_enabled)
        return cls(predictor=predictor, run_dir=path)

    def predict(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return self.predictor.predict(row)
