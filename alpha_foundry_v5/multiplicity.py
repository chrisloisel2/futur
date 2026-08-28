from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

from .hashing import sha256_obj
from .ledger import GENESIS_HASH, LedgerIntegrityError
from .statistics import bh_qvalues

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


@dataclass(frozen=True)
class FamilyTestRecord:
    family_id: str
    hypothesis_digest: str
    experiment_digest: str
    p_value: float
    recorded_at_ns: int
    prev_hash: str = GENESIS_HASH
    record_hash: str = ""

    def payload(self) -> Dict[str, object]:
        row = asdict(self)
        row.pop("record_hash", None)
        return row

    def expected_hash(self) -> str:
        return sha256_obj(self.payload())


class FamilyTestLedger:
    """Tamper-evident multiplicity ledger across all tests in one search family."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _lock(self, fh):
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock(self, fh):
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _decode(lines) -> List[FamilyTestRecord]:
        rows = [FamilyTestRecord(**json.loads(line)) for line in lines if line.strip()]
        previous = GENESIS_HASH
        for i, row in enumerate(rows):
            if row.prev_hash != previous or row.record_hash != row.expected_hash():
                raise LedgerIntegrityError("multiplicity ledger chain broken at row %d" % i)
            previous = row.record_hash
        return rows

    def records(self) -> List[FamilyTestRecord]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            return self._decode(fh.readlines())

    def verify(self) -> Dict[str, object]:
        try:
            rows = self.records()
            return {"ok": True, "rows": len(rows), "head_hash": rows[-1].record_hash if rows else GENESIS_HASH}
        except LedgerIntegrityError as exc:
            return {"ok": False, "error": str(exc)}

    def record(self, family_id: str, hypothesis_digest: str, experiment_digest: str, p_value: float) -> FamilyTestRecord:
        p = float(p_value)
        if not 0.0 <= p <= 1.0:
            raise ValueError("p_value must be in [0,1]")
        self.path.touch(exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as fh:
            self._lock(fh)
            try:
                fh.seek(0)
                rows = self._decode(fh.readlines())
                if any(r.experiment_digest == experiment_digest for r in rows):
                    raise ValueError("experiment already recorded in multiplicity ledger")
                previous = rows[-1].record_hash if rows else GENESIS_HASH
                provisional = FamilyTestRecord(family_id, hypothesis_digest, experiment_digest, p, time.time_ns(), previous, "")
                record = FamilyTestRecord(**{**asdict(provisional), "record_hash": provisional.expected_hash()})
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return record
            finally:
                self._unlock(fh)

    def qvalues(self, family_id: str) -> Dict[str, float]:
        rows = [r for r in self.records() if r.family_id == family_id]
        q = bh_qvalues([r.p_value for r in rows])
        return {row.experiment_digest: float(q[i]) for i, row in enumerate(rows)}

    def test_count(self, family_id: str) -> int:
        return sum(1 for row in self.records() if row.family_id == family_id)
