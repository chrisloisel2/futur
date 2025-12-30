from __future__ import annotations
import json, time
from typing import Any, Dict

class JsonlLogger:
    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "a", buffering=1)

    def log(self, d: Dict[str, Any]):
        d = dict(d)
        d["ts"] = time.time()
        self.f.write(json.dumps(d) + "\n")

    def close(self):
        self.f.close()
