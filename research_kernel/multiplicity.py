"""Gate 5 — the threshold is derived, never written down.

``threshold_t`` reads no constant.  It reads how many hypotheses the family has
already sealed and returns the one-sided Bonferroni threshold at a family-wise
alpha of 0.05.  The consequence is the whole point: sealing five more
hypotheses raises the bar on the first five, mechanically, without anyone
having to remember it on a disappointing evening.  A threshold stored in a file
is a threshold someone will edit.

    n = 1    ->  1.64
    n = 5    ->  2.33
    n = 10   ->  2.58
    n = 52   ->  3.10
    n = 700  ->  3.80

Why this matters here rather than in theory: across 27 configurations of
**random** baskets, one showed t = 2.77 — above the one-hypothesis bar and
above the five-hypothesis bar, on pure noise.

Contamination is recorded separately and charges **no** trial.  Multiplicity
applies to tests on the same data; looking at 2022-2025 does not inflate the
false-positive rate of a test run on 2026.  What it inflates is selection, and
selection is paid by promoting few finalists on fresh data — so a contaminated
period is burned for the family and the sealer must refuse to use it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence, Tuple

from research_kernel.mechanism_spec import FAMILIES

FAMILY_ALPHA = 0.05
DEFAULT_LEDGER_PATH = Path("reports/research_kernel/multiplicity_ledger.json")

#: A mechanism confirmable in N years must carry roughly this Sharpe.
#: t = Sharpe * sqrt(N) and the bar sits near 1.96-3.8, so the identity is
#: Sharpe >= threshold / sqrt(N); the constant below is the round-4 finding at
#: the 700-hypothesis bar over one year of daily sampling.
SHARPE_FOR_YEARS_CONSTANT = 5.60


class MultiplicityError(RuntimeError):
    pass


def threshold_t(n_hypotheses: int, alpha: float = FAMILY_ALPHA) -> float:
    """One-sided Bonferroni threshold for ``n`` sealed hypotheses."""
    if n_hypotheses < 1:
        raise MultiplicityError("n_hypotheses must be >= 1, got %r" % (n_hypotheses,))
    if not 0.0 < alpha < 1.0:
        raise MultiplicityError("alpha must be in (0, 1)")
    return float(NormalDist().inv_cdf(1.0 - alpha / float(n_hypotheses)))


def sharpe_needed_for_years(years: float, constant: float = SHARPE_FOR_YEARS_CONSTANT) -> float:
    """``Sharpe >= constant / sqrt(years)`` — what confirmability costs.

    The bottleneck measured in round 4 was never the episode rate; it was the
    Sharpe.  Use this before collecting: if the required Sharpe is out of reach
    for the family, the window is the thing to change, not the threshold.
    """
    if years <= 0:
        raise MultiplicityError("years must be > 0")
    return float(constant / (years ** 0.5))


def _hash_entry(entry: Dict[str, Any], prev_hash: str) -> str:
    payload = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


@dataclass
class MultiplicityLedger:
    """Append-only, hash-chained record of every trial a family has spent."""

    path: Path = DEFAULT_LEDGER_PATH

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # ------------------------------------------------------------- storage
    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write(self, entries: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, sort_keys=True)
        tmp.replace(self.path)

    @property
    def entries(self) -> List[Dict[str, Any]]:
        return self._read()

    # -------------------------------------------------------------- record
    def record_trial(
        self,
        family: str,
        mechanism_id: str,
        rules_hash: str,
        n_trials: int = 1,
        recorded_at: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        if family not in FAMILIES:
            raise MultiplicityError("unknown family %r" % (family,))
        if n_trials < 1:
            raise MultiplicityError("a sealed hypothesis costs at least one trial")

        entries = self._read()
        for e in entries:
            if e.get("kind") != "trial":
                continue
            if e["rules_hash"] == rules_hash and e["family"] == family:
                raise MultiplicityError(
                    "rules_hash %s is already sealed in family %s as %s — a re-seal "
                    "is not free" % (rules_hash[:12], family, e["mechanism_id"])
                )
            if e["mechanism_id"] == mechanism_id and e["rules_hash"] != rules_hash:
                raise MultiplicityError(
                    "mechanism_id %s was sealed with a different rules_hash; bump the "
                    "version instead of editing the rule" % (mechanism_id,)
                )

        entry = {
            "kind": "trial",
            "family": family,
            "mechanism_id": mechanism_id,
            "rules_hash": rules_hash,
            "n_trials": int(n_trials),
            "recorded_at": recorded_at,
            "note": note,
            "index": len(entries),
        }
        prev = entries[-1]["entry_hash"] if entries else ""
        entry["prev_hash"] = prev
        # the threshold in force for this family once this trial is counted
        entry["threshold_at_seal"] = round(
            threshold_t(self._family_size(entries, family) + int(n_trials)), 4
        )
        entry["entry_hash"] = _hash_entry(
            {k: v for k, v in entry.items() if k != "entry_hash"}, prev
        )
        entries.append(entry)
        self._write(entries)
        return entry

    def record_contamination(
        self,
        family: str,
        burned_periods: Sequence[Tuple[str, str]],
        reason: str,
        recorded_at: str = "",
    ) -> Dict[str, Any]:
        """Declare periods this family has already seen.  Charges no trial."""
        if family not in FAMILIES:
            raise MultiplicityError("unknown family %r" % (family,))
        if not burned_periods:
            raise MultiplicityError("a contamination with no burned period is a comment")
        entries = self._read()
        entry = {
            "kind": "contamination",
            "family": family,
            "burned_periods": [[str(a), str(b)] for a, b in burned_periods],
            "reason": reason,
            "recorded_at": recorded_at,
            "index": len(entries),
        }
        prev = entries[-1]["entry_hash"] if entries else ""
        entry["prev_hash"] = prev
        entry["entry_hash"] = _hash_entry(
            {k: v for k, v in entry.items() if k != "entry_hash"}, prev
        )
        entries.append(entry)
        self._write(entries)
        return entry

    # --------------------------------------------------------------- query
    @staticmethod
    def _family_size(entries: List[Dict[str, Any]], family: str) -> int:
        return sum(
            int(e.get("n_trials", 0))
            for e in entries
            if e.get("kind") == "trial" and e.get("family") == family
        )

    def family_size(self, family: str) -> int:
        return self._family_size(self._read(), family)

    def current_threshold(self, family: str, extra: int = 0) -> float:
        """Threshold in force, optionally priced for ``extra`` further seals.

        Call it with ``extra`` **before** widening a search: it quotes the cost
        of the widening before it is paid.
        """
        return threshold_t(max(1, self.family_size(family) + max(0, int(extra))))

    def burned_periods(self, family: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for e in self._read():
            if e.get("kind") == "contamination" and e.get("family") == family:
                out.extend((a, b) for a, b in e["burned_periods"])
        return out

    def is_burned(self, family: str, start: str, end: str) -> Optional[Tuple[str, str]]:
        """Return the first burned period overlapping ``[start, end)``."""
        for a, b in self.burned_periods(family):
            if str(start) < str(b) and str(a) < str(end):
                return (a, b)
        return None

    def retroactive_penalty(self) -> Dict[str, Dict[str, float]]:
        """What each sealed hypothesis now owes, family by family."""
        entries = self._read()
        out: Dict[str, Dict[str, float]] = {}
        for e in entries:
            if e.get("kind") != "trial":
                continue
            fam = e["family"]
            now = threshold_t(self._family_size(entries, fam))
            out.setdefault(fam, {})[e["mechanism_id"]] = round(
                now - float(e.get("threshold_at_seal", now)), 4
            )
        return out

    def verify(self) -> bool:
        """A hypothesis inserted after the fact, or a threshold left behind its
        own count, breaks the chain."""
        prev = ""
        entries = self._read()
        for i, e in enumerate(entries):
            if e.get("index") != i:
                raise MultiplicityError("entry %d has index %r" % (i, e.get("index")))
            if e.get("prev_hash", "") != prev:
                raise MultiplicityError("chain broken at entry %d" % i)
            expected = _hash_entry(
                {k: v for k, v in e.items() if k != "entry_hash"}, prev
            )
            if e.get("entry_hash") != expected:
                raise MultiplicityError("entry %d has been edited" % i)
            if e.get("kind") == "trial":
                size_then = self._family_size(entries[: i + 1], e["family"])
                if round(threshold_t(size_then), 4) != float(e["threshold_at_seal"]):
                    raise MultiplicityError(
                        "entry %d records threshold %.4f but its family stood at %d "
                        "hypotheses (%.4f)"
                        % (i, e["threshold_at_seal"], size_then, threshold_t(size_then))
                    )
            prev = e["entry_hash"]
        return True
