from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Optional

from common.logging.setup import get_logger
from infra.storage.object_store import S3ParquetReader, S3ParquetWriter, _filesystem_for_uri

logger = get_logger(__name__)


class ModelStore:
    def __init__(self, s3_prefix: str, cache_dir: str = "data/cache/models") -> None:
        self.s3_prefix = s3_prefix.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_latest(self, component: str, model_name: str, run_id: Optional[str] = None):
        run = run_id or "latest"
        base = f"{self.s3_prefix}/artifacts/models/{component}/{model_name}/{run}"
        local_path = self.cache_dir / component / model_name / run
        local_path.mkdir(parents=True, exist_ok=True)
        fs = _filesystem_for_uri(base)
        for fname in ["model.pkl", "calibrator.pkl", "metadata.json"]:
            remote = f"{base}/{fname}"
            target = local_path / fname
            try:
                with fs.open_input_file(remote) as src, open(target, "wb") as dst:
                    dst.write(src.read())
            except FileNotFoundError:
                continue
        model_file = local_path / "model.pkl"
        if model_file.exists():
            with open(model_file, "rb") as f:
                model = pickle.load(f)
            logger.info({"msg": "loaded model", "component": component, "model": model_name, "run_id": run})
            return model
        raise FileNotFoundError(f"Model not found at {base}")
