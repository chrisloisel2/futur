from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .hashing import sha256_obj

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


GENESIS_HASH = "0" * 64


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
    metric: Optional[float] = None
    prev_hash: str = GENESIS_HASH
    record_hash: str = ""

    def hash_payload(self) -> Dict[str, object]:
        row = asdict(self)
        row.pop("record_hash", None)
        return row

    def expected_hash(self) -> str:
        return sha256_obj(self.hash_payload())


class SearchBudgetExceeded(RuntimeError):
    pass


class LedgerIntegrityError(RuntimeError):
    pass


class SearchLedger:
    """Append-only, hash-chained trial ledger.

    A selectable trial must be RESERVED before any computation. COMPLETE rows
    are accepted only for an existing matching reservation. Every row hashes
    the previous row, making post-hoc deletion/reordering/editing detectable.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _lock(self, fh) -> None:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock(self, fh) -> None:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _decode_lines(lines) -> List[TrialRecord]:
        rows = [TrialRecord(**json.loads(line)) for line in lines if line.strip()]
        previous = GENESIS_HASH
        for i, row in enumerate(rows):
            if row.prev_hash != previous:
                raise LedgerIntegrityError("ledger chain broken at row %d" % i)
            if row.record_hash != row.expected_hash():
                raise LedgerIntegrityError("ledger row hash mismatch at row %d" % i)
            previous = row.record_hash
        return rows

    def records(self) -> List[TrialRecord]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            return self._decode_lines(fh.readlines())

    def verify(self) -> Dict[str, object]:
        try:
            rows = self.records()
            return {"ok": True, "rows": len(rows), "head_hash": rows[-1].record_hash if rows else GENESIS_HASH}
        except LedgerIntegrityError as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _latest_by_trial(records: List[TrialRecord]) -> Dict[str, TrialRecord]:
        latest: Dict[str, TrialRecord] = {}
        for row in records:
            latest[row.trial_id] = row
        return latest

    def family_trial_count(self, family_id: str) -> int:
        latest = self._latest_by_trial(self.records())
        return sum(1 for row in latest.values() if row.family_id == family_id)

    @staticmethod
    def _make_record(**kwargs) -> TrialRecord:
        provisional = TrialRecord(**kwargs)
        return TrialRecord(**{**asdict(provisional), "record_hash": provisional.expected_hash()})

    def reserve(self, trial_id: str, family_id: str, hypothesis_digest: str, experiment_digest: str, config: Dict[str, object], stage: str, max_trials: int) -> TrialRecord:
        config_digest = sha256_obj(config)
        self.path.touch(exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as fh:
            self._lock(fh)
            try:
                fh.seek(0)
                existing = self._decode_lines(fh.readlines())
                latest = self._latest_by_trial(existing)
                if trial_id in latest:
                    raise ValueError("trial_id already exists: %s" % trial_id)
                count = sum(1 for row in latest.values() if row.family_id == family_id)
                if count >= int(max_trials):
                    raise SearchBudgetExceeded("family %s exhausted search budget %s" % (family_id, max_trials))
                prev_hash = existing[-1].record_hash if existing else GENESIS_HASH
                record = self._make_record(trial_id=trial_id, family_id=family_id, hypothesis_digest=hypothesis_digest, experiment_digest=experiment_digest, config_digest=config_digest, stage=str(stage), status="RESERVED", reserved_at_ns=time.time_ns(), completed_at_ns=0, metric=None, prev_hash=prev_hash, record_hash="")
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return record
            finally:
                self._unlock(fh)

    def complete(self, trial_id: str, family_id: str, hypothesis_digest: str, experiment_digest: str, config: Dict[str, object], stage: str, metric: float) -> TrialRecord:
        config_digest = sha256_obj(config)
        self.path.touch(exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as fh:
            self._lock(fh)
            try:
                fh.seek(0)
                existing = self._decode_lines(fh.readlines())
                matches = [r for r in existing if r.trial_id == trial_id]
                reserved = next((r for r in matches if r.status == "RESERVED"), None)
                if reserved is None:
                    raise ValueError("cannot complete unreserved trial: %s" % trial_id)
                if any(r.status == "COMPLETE" for r in matches):
                    raise ValueError("trial already complete: %s" % trial_id)
                expected = (family_id, hypothesis_digest, experiment_digest, config_digest, str(stage))
                observed = (reserved.family_id, reserved.hypothesis_digest, reserved.experiment_digest, reserved.config_digest, reserved.stage)
                if observed != expected:
                    raise ValueError("completion does not match reservation")
                prev_hash = existing[-1].record_hash if existing else GENESIS_HASH
                value = None if metric is None or not float(metric) == float(metric) else float(metric)
                record = self._make_record(trial_id=trial_id, family_id=family_id, hypothesis_digest=hypothesis_digest, experiment_digest=experiment_digest, config_digest=config_digest, stage=str(stage), status="COMPLETE", reserved_at_ns=reserved.reserved_at_ns, completed_at_ns=time.time_ns(), metric=value, prev_hash=prev_hash, record_hash="")
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return record
            finally:
                self._unlock(fh)

    def effective_trials(self, family_id: str) -> int:
        return self.family_trial_count(family_id)
