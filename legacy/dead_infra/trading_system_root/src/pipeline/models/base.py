from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pandas as pd


@dataclass
class ModelArtifact:
    path: Path
    version: str
    run_id: str


class ModelIO(Protocol):
    def predict(self, state_df: pd.DataFrame) -> Any:
        ...

    def load(self, path: str) -> None:
        ...

    def save(self, path: str) -> None:
        ...


class BaseModel:
    def predict(self, state_df: pd.DataFrame) -> Any:
        raise NotImplementedError

    def load(self, path: str) -> None:
        raise NotImplementedError

    def save(self, path: str) -> None:
        raise NotImplementedError


class BaseCalibrator:
    def fit(self, y_true, y_prob) -> None:
        raise NotImplementedError

    def predict(self, p):
        raise NotImplementedError

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: str) -> "BaseCalibrator":
        with open(path, "rb") as f:
            return pickle.load(f)


def save_metadata(path: Path, metadata: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2))
