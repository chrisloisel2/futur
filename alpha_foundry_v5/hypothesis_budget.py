from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Mapping

from .ledger import GENESIS_HASH, LedgerIntegrityError

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def _digest(payload: Mapping[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def load_hypothesis_budget_manifest(path: str) -> Dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    claimed = str(payload.get("manifest_digest") or "")
    body = dict(payload)
    body.pop("manifest_digest", None)
    if not claimed or claimed != _digest(body):
        raise ValueError("hypothesis budget manifest digest mismatch")
    if payload.get("target_free") is not True:
        raise ValueError("hypothesis budget must come from a target-free support audit")
    if not isinstance(payload.get("labs"), dict):
        raise ValueError("hypothesis budget manifest missing labs")
    return payload


def require_lab_budget(
    manifest: Mapping[str, object],
    lab_id: str,
    feature_provenance_digest: str,
) -> int:
    expected_provenance = str(manifest.get("feature_provenance_digest") or "")
    if not expected_provenance or expected_provenance != str(feature_provenance_digest):
        raise ValueError("hypothesis budget was not allocated for this feature provenance manifest")
    row = dict((manifest.get("labs") or {}).get(str(lab_id).upper()) or {})
    budget = int(row.get("max_hypothesis_tests", 0) or 0)
    if budget <= 0:
        raise ValueError("lab %s has zero hypothesis budget (%s)" % (lab_id, row.get("support_verdict")))
    return budget


@dataclass(frozen=True)
class HypothesisBudgetRecord:
    action: str
    lab_id: str
    family_id: str
    hypothesis_digest: str
    experiment_digest: str
    budget_manifest_digest: str
    max_hypothesis_tests: int
    recorded_at_ns: int
    prev_hash: str = GENESIS_HASH
    record_hash: str = ""

    def payload(self) -> Dict[str, object]:
        row = asdict(self)
        row.pop("record_hash", None)
        return row

    def expected_hash(self) -> str:
        return _digest(self.payload())


class HypothesisBudgetLedger:
    """Reserve final hypothesis tests before computation under a target-free lab budget.

    A reservation permanently consumes one unit. A crashed/aborted run is therefore
    visible and cannot be silently retried without spending another preregistered unit.
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
    def _decode(lines) -> List[HypothesisBudgetRecord]:
        rows = [HypothesisBudgetRecord(**json.loads(line)) for line in lines if line.strip()]
        previous = GENESIS_HASH
        for i, row in enumerate(rows):
            if row.action not in {"RESERVED", "COMPLETE"}:
                raise LedgerIntegrityError("invalid hypothesis budget action at row %d" % i)
            if row.prev_hash != previous or row.record_hash != row.expected_hash():
                raise LedgerIntegrityError("hypothesis budget ledger chain broken at row %d" % i)
            previous = row.record_hash
        return rows

    def records(self) -> List[HypothesisBudgetRecord]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            return self._decode(fh.readlines())

    def verify(self) -> Dict[str, object]:
        try:
            rows = self.records()
            return {
                "ok": True,
                "rows": len(rows),
                "head_hash": rows[-1].record_hash if rows else GENESIS_HASH,
            }
        except LedgerIntegrityError as exc:
            return {"ok": False, "error": str(exc)}

    def _append_locked(self, fh, provisional: HypothesisBudgetRecord) -> HypothesisBudgetRecord:
        fh.seek(0)
        rows = self._decode(fh.readlines())
        previous = rows[-1].record_hash if rows else GENESIS_HASH
        row = HypothesisBudgetRecord(**{
            **asdict(provisional),
            "prev_hash": previous,
            "record_hash": "",
        })
        record = HypothesisBudgetRecord(**{**asdict(row), "record_hash": row.expected_hash()})
        fh.seek(0, os.SEEK_END)
        fh.write(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        return record

    def reserve(
        self,
        lab_id: str,
        family_id: str,
        hypothesis_digest: str,
        experiment_digest: str,
        budget_manifest_digest: str,
        max_hypothesis_tests: int,
    ) -> HypothesisBudgetRecord:
        limit = int(max_hypothesis_tests)
        if limit <= 0:
            raise ValueError("max_hypothesis_tests must be positive")
        self.path.touch(exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as fh:
            self._lock(fh)
            try:
                fh.seek(0)
                rows = self._decode(fh.readlines())
                if any(r.action == "RESERVED" and r.experiment_digest == experiment_digest for r in rows):
                    raise ValueError("experiment already reserved in hypothesis budget ledger")
                used = sum(
                    1 for r in rows
                    if r.action == "RESERVED"
                    and r.lab_id == str(lab_id).upper()
                    and r.budget_manifest_digest == str(budget_manifest_digest)
                )
                if used >= limit:
                    raise ValueError(
                        "hypothesis budget exhausted for %s: used=%d limit=%d"
                        % (lab_id, used, limit)
                    )
                provisional = HypothesisBudgetRecord(
                    "RESERVED",
                    str(lab_id).upper(),
                    str(family_id),
                    str(hypothesis_digest),
                    str(experiment_digest),
                    str(budget_manifest_digest),
                    limit,
                    time.time_ns(),
                )
                return self._append_locked(fh, provisional)
            finally:
                self._unlock(fh)

    def complete(self, experiment_digest: str) -> HypothesisBudgetRecord:
        self.path.touch(exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as fh:
            self._lock(fh)
            try:
                fh.seek(0)
                rows = self._decode(fh.readlines())
                reserved = [
                    r for r in rows
                    if r.action == "RESERVED" and r.experiment_digest == str(experiment_digest)
                ]
                if len(reserved) != 1:
                    raise ValueError("completion requires exactly one prior hypothesis reservation")
                if any(r.action == "COMPLETE" and r.experiment_digest == str(experiment_digest) for r in rows):
                    raise ValueError("hypothesis experiment already completed")
                src = reserved[0]
                provisional = HypothesisBudgetRecord(
                    "COMPLETE",
                    src.lab_id,
                    src.family_id,
                    src.hypothesis_digest,
                    src.experiment_digest,
                    src.budget_manifest_digest,
                    src.max_hypothesis_tests,
                    time.time_ns(),
                )
                return self._append_locked(fh, provisional)
            finally:
                self._unlock(fh)

    def used(self, lab_id: str, budget_manifest_digest: str) -> int:
        return sum(
            1 for r in self.records()
            if r.action == "RESERVED"
            and r.lab_id == str(lab_id).upper()
            and r.budget_manifest_digest == str(budget_manifest_digest)
        )
