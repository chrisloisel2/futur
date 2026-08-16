from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

from .hashing import sha256_obj

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    family_id: str
    hypothesis_digest: str
    experiment_digest: str
    config_digest: str
    stage: str
    status: str
    reserved_at_ns: int
    completed_at_ns: int = 0
    metric: float = float("nan")

    @property
    def digest(self) -> str:
        return sha256_obj(self)


class SearchBudgetExceeded(RuntimeError):
    pass


class SearchLedger:
    """Append-only trial ledger. A trial must be reserved before it is computed."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _lock(self, fh) -> None:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock(self, fh) -> None:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def records(self) -> List[TrialRecord]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(TrialRecord(**json.loads(line)))
        return out

    def family_trial_count(self, family_id: str) -> int:
        return len({r.trial_id for r in self.records() if r.family_id == family_id and r.status in {"RESERVED", "COMPLETE"}})

    def reserve(self, trial_id: str, family_id: str, hypothesis_digest: str, experiment_digest: str, config: Dict[str, object], stage: str, max_trials: int) -> TrialRecord:
        config_digest = sha256_obj(config)
        self.path.touch(exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as fh:
            self._lock(fh)
            try:
                fh.seek(0)
                existing = [TrialRecord(**json.loads(line)) for line in fh if line.strip()]
                if any(r.trial_id == trial_id for r in existing):
                    raise ValueError("trial_id already exists: %s" % trial_id)
                count = len({r.trial_id for r in existing if r.family_id == family_id and r.status in {"RESERVED", "COMPLETE"}})
                if count >= int(max_trials):
                    raise SearchBudgetExceeded("family %s exhausted search budget %s" % (family_id, max_trials))
                record = TrialRecord(trial_id, family_id, hypothesis_digest, experiment_digest, config_digest, str(stage), "RESERVED", time.time_ns())
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return record
            finally:
                self._unlock(fh)

    def complete(self, trial_id: str, family_id: str, hypothesis_digest: str, experiment_digest: str, config: Dict[str, object], stage: str, metric: float) -> TrialRecord:
        record = TrialRecord(trial_id, family_id, hypothesis_digest, experiment_digest, sha256_obj(config), str(stage), "COMPLETE", 0, time.time_ns(), float(metric))
        with self.path.open("a", encoding="utf-8") as fh:
            self._lock(fh)
            try:
                fh.write(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                self._unlock(fh)
        return record

    def effective_trials(self, family_id: str) -> int:
        latest = {}
        for row in self.records():
            if row.family_id == family_id:
                latest[row.trial_id] = row
        return len(latest)
