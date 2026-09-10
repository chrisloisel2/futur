"""Gate 6 — a sealed forward window, or no promotion.

A mechanism that looks good on history is a hypothesis, not an edge.  The seal
freezes the rule, the universe, the data sources and both threshold sets before
the forward window opens, and stores the hash of the rule.  ``run_mechanism``
compares that hash on every forward run: a rule edited mid-window invalidates
the seal instead of quietly improving the result.

Two refusals worth naming:

* a window that starts in the past is not a forward window;
* a window overlapping a period the family has already looked at is refused,
  because contamination burns periods per family.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.multiplicity import MultiplicityLedger

SEALED_ROOT = Path("sealed_forwards")
ACTIVE = "active"
EXPIRED = "expired"
RESULTS = "results"


class ForwardSealError(RuntimeError):
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ForwardSeal:
    mechanism_id: str
    created_at: str
    forward_start: str
    forward_end: str
    universe: List[str]
    rules_hash: str
    data_sources: List[str]
    promotion_thresholds: Dict[str, Any]
    kill_thresholds: Dict[str, Any]
    multiplicity_family: str = ""
    threshold_t_at_seal: float = 0.0
    family_size_at_seal: int = 0
    kernel_version: str = ""
    code_commit_sha: str = ""
    notes: str = ""
    seal_hash: str = ""

    # ------------------------------------------------------------------
    def payload(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k != "seal_hash"}

    def compute_hash(self) -> str:
        blob = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def seal_id(self) -> str:
        return "%s_%s" % (self.mechanism_id, self.forward_start[:10])

    def filename(self) -> str:
        return "%s.json" % self.seal_id

    def is_active(self, now: Optional[str] = None) -> bool:
        now = now or _utcnow()
        return self.forward_start <= now < self.forward_end

    def has_elapsed(self, now: Optional[str] = None) -> bool:
        return (now or _utcnow()) >= self.forward_end

    def covers(self, spec: MechanismSpec) -> bool:
        return spec.rules_hash() == self.rules_hash

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["seal_id"] = self.seal_id
        return d

    def write(self, root: Path = SEALED_ROOT, folder: str = ACTIVE) -> Path:
        self.seal_hash = self.compute_hash()
        path = Path(root) / folder / self.filename()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        return path

    @classmethod
    def read(cls, path: Path) -> "ForwardSeal":
        with Path(path).open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        payload.pop("seal_id", None)
        seal = cls(**payload)
        if seal.seal_hash and seal.seal_hash != seal.compute_hash():
            raise ForwardSealError(
                "seal %s has been edited since it was written" % path
            )
        return seal


def seal_forward(
    spec: MechanismSpec,
    forward_start: str,
    forward_end: str,
    promotion_thresholds: Optional[Dict[str, Any]] = None,
    kill_thresholds: Optional[Dict[str, Any]] = None,
    root: Path = SEALED_ROOT,
    ledger: Optional[MultiplicityLedger] = None,
    now: Optional[str] = None,
    kernel_version: str = "",
    code_commit_sha: str = "",
    notes: str = "",
) -> ForwardSeal:
    now = now or _utcnow()
    if forward_start >= forward_end:
        raise ForwardSealError("forward_start must precede forward_end")
    if forward_start < now[:10]:
        raise ForwardSealError(
            "forward_start %s is in the past: that is a backtest, not a forward window"
            % forward_start
        )

    existing = load_seals(root, ACTIVE)
    for s in existing:
        if s.mechanism_id == spec.mechanism_id and not s.has_elapsed(now):
            raise ForwardSealError(
                "%s already has an active seal until %s"
                % (spec.mechanism_id, s.forward_end)
            )

    threshold = 0.0
    family_size = 0
    if ledger is not None:
        burned = ledger.is_burned(spec.multiplicity_family, forward_start, forward_end)
        if burned:
            raise ForwardSealError(
                "the %s family has already looked at %s..%s — that period is burned"
                % (spec.multiplicity_family, burned[0], burned[1])
            )
        family_size = ledger.family_size(spec.multiplicity_family)
        threshold = ledger.current_threshold(spec.multiplicity_family)

    seal = ForwardSeal(
        mechanism_id=spec.mechanism_id,
        created_at=now,
        forward_start=forward_start,
        forward_end=forward_end,
        universe=sorted(spec.universe),
        rules_hash=spec.rules_hash(),
        data_sources=sorted(spec.data_sources),
        promotion_thresholds=dict(promotion_thresholds or spec.promotion_criteria),
        kill_thresholds=dict(kill_thresholds or spec.kill_criteria),
        multiplicity_family=spec.multiplicity_family,
        threshold_t_at_seal=round(float(threshold), 4),
        family_size_at_seal=int(family_size),
        kernel_version=kernel_version,
        code_commit_sha=code_commit_sha,
        notes=notes,
    )
    seal.write(root, ACTIVE)
    return seal


def load_seals(root: Path = SEALED_ROOT, folder: str = ACTIVE) -> List[ForwardSeal]:
    d = Path(root) / folder
    if not d.exists():
        return []
    return [ForwardSeal.read(p) for p in sorted(d.glob("*.json"))]


def find_seal(
    mechanism_id: str, root: Path = SEALED_ROOT, folder: str = ACTIVE
) -> Optional[ForwardSeal]:
    for s in load_seals(root, folder):
        if s.mechanism_id == mechanism_id:
            return s
    return None


def verify_unchanged(spec: MechanismSpec, seal: ForwardSeal) -> None:
    if not seal.covers(spec):
        raise ForwardSealError(
            "the rule changed during the forward window: spec hash %s, seal hash %s. "
            "The seal is void; a new hypothesis costs a new trial."
            % (spec.rules_hash()[:12], seal.rules_hash[:12])
        )


def expire(seal: ForwardSeal, root: Path = SEALED_ROOT) -> Path:
    src = Path(root) / ACTIVE / seal.filename()
    dst = Path(root) / EXPIRED / seal.filename()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        src.replace(dst)
    else:
        seal.write(root, EXPIRED)
    return dst


def record_result(
    seal: ForwardSeal, payload: Dict[str, Any], root: Path = SEALED_ROOT
) -> Path:
    path = Path(root) / RESULTS / ("%s_result.json" % seal.seal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["seal_id"] = seal.seal_id
    body["seal_hash"] = seal.seal_hash or seal.compute_hash()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2, sort_keys=True, default=str)
    return path
