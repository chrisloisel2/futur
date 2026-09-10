"""Append-only, hash-chained record of every run the kernel has ever produced.

The point is not audit theatre.  It is that a mechanism cannot be quietly run
eleven times until one run looks good: every run is recorded with its rules
hash, its verdict and its gate failures, and the chain breaks if an entry is
removed or edited.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_LEDGER_PATH = Path("reports/research_kernel/run_ledger.jsonl")


class LedgerError(RuntimeError):
    pass


def _hash(entry: Dict[str, Any], prev: str) -> str:
    blob = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev + blob).encode("utf-8")).hexdigest()


@dataclass
class RunLedger:
    path: Path = DEFAULT_LEDGER_PATH

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        entries = self.read()
        prev = entries[-1]["entry_hash"] if entries else ""
        entry = dict(record)
        entry["index"] = len(entries)
        entry.setdefault(
            "recorded_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        entry["prev_hash"] = prev
        entry["entry_hash"] = _hash(
            {k: v for k, v in entry.items() if k != "entry_hash"}, prev
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        return entry

    def verify(self) -> bool:
        prev = ""
        for i, e in enumerate(self.read()):
            if e.get("index") != i:
                raise LedgerError("entry %d carries index %r" % (i, e.get("index")))
            if e.get("prev_hash", "") != prev:
                raise LedgerError("chain broken at entry %d" % i)
            expected = _hash({k: v for k, v in e.items() if k != "entry_hash"}, prev)
            if e.get("entry_hash") != expected:
                raise LedgerError("entry %d has been edited" % i)
            prev = e["entry_hash"]
        return True

    def runs_for(self, mechanism_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.read() if e.get("mechanism_id") == mechanism_id]

    def count_runs(self, mechanism_id: str) -> int:
        return len(self.runs_for(mechanism_id))
