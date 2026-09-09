"""The spec is the preregistration.

A mechanism exists as a frozen ``spec.json`` before it is ever run.  Everything
the decision depends on goes in that file and is hashed; ``run_mechanism``
refuses to promote a run whose ``rules_hash`` differs from the sealed one.

Three refusals are built into the loader because each has already cost this
project a false positive:

* a **range** of parameters ("between 2 and 4", ``[2, 3, 4]``) is a family of
  hypotheses wearing one name — refused;
* more than two **sensitivities** around the primary parameter — refused, and
  the ones that are declared are charged as trials in the multiplicity family;
* a hypothesis phrased as a **statistical coincidence** rather than a mechanism
  — refused at gate 1.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from research_kernel.cost_model import CostModel

SIDE_MODES = ("long", "short", "long_short", "market_neutral")

#: Gate 5 families.  A mechanism belongs to exactly one; the significance
#: threshold is derived from how many hypotheses that family has already sealed.
FAMILIES = (
    "funding",
    "basis",
    "cross_exchange",
    "microstructure",
    "crowd_positioning",
    "liquidation",
    "onchain",
    "news",
)

#: At most a primary value plus two sensitivities per mechanism.
MAX_SENSITIVITIES = 2

#: Gate 1 — phrasings that describe a correlation rather than a mechanism.
BANNED_HYPOTHESIS_PATTERNS = (
    r"\brsi\b",
    r"\bmacd\b",
    r"\bbollinger\b",
    r"le mod[eè]le (va )?trouver",
    r"the model will find",
    r"corr[eé]l\w* avec (le )?(return|rendement)",
    r"correlates? with (the )?returns?",
    r"feature \w+ (corr|pr[eé]dit|predicts)",
    r"quand (le )?volume monte",
    r"when volume (goes up|rises)",
    r"\bmachine learning\b",
    r"\bdeep learning\b",
)

_RANGE_TEXT_PATTERNS = (
    r"\d\s*\.\.\s*\d",
    r"\bentre\s+[\d.]+\s+et\s+[\d.]+",
    r"\bbetween\s+[\d.]+\s+and\s+[\d.]+",
    r"\bfrom\s+[\d.]+\s+to\s+[\d.]+",
    r"^\s*[\d.]+\s*(?:-|–|to|à|a)\s*[\d.]+\s*$",
)

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d|w)$")
_DURATION_UNITS = {
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}


class SpecError(ValueError):
    """The spec is not a preregistration."""


def parse_duration_seconds(token: str) -> float:
    m = _DURATION_RE.match(str(token).strip().lower())
    if not m:
        raise SpecError(
            "duration %r must look like 5s, 250ms, 15m, 4h, 1d, 2w" % (token,)
        )
    return float(m.group(1)) * _DURATION_UNITS[m.group(2)]


def _contains_range(value: Any, path: str) -> Optional[str]:
    if isinstance(value, str):
        low = value.strip().lower()
        for pat in _RANGE_TEXT_PATTERNS:
            if re.search(pat, low):
                return "%s = %r reads as a range" % (path, value)
        return None
    if isinstance(value, (list, tuple)):
        numeric = [v for v in value if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(numeric) > 1:
            return (
                "%s = %r is a list of %d numeric values, i.e. %d hypotheses"
                % (path, list(value), len(numeric), len(numeric))
            )
        for i, v in enumerate(value):
            found = _contains_range(v, "%s[%d]" % (path, i))
            if found:
                return found
        return None
    if isinstance(value, Mapping):
        for k, v in value.items():
            found = _contains_range(v, "%s.%s" % (path, k))
            if found:
                return found
    return None


def _canonical(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {k: _canonical(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    return obj


@dataclass(frozen=True)
class MechanismSpec:
    mechanism_id: str
    hypothesis: str
    economic_reason: str
    data_sources: List[str]
    universe: List[str]
    timeframe: str
    horizon: str
    side_mode: str
    entry_rule: Dict[str, Any]
    exit_rule: Dict[str, Any]
    cost_model: Dict[str, Any]
    validation_window: Dict[str, Any]
    placebo_tests: List[str]
    declustering_rule: Dict[str, Any]
    multiplicity_family: str
    kill_criteria: Dict[str, Any]
    promotion_criteria: Dict[str, Any]
    sensitivities: Dict[str, Any] = field(default_factory=dict)
    declared_data_latency_ms: Optional[float] = None
    notes: str = ""

    # ------------------------------------------------------------------ load
    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MechanismSpec":
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = sorted(set(payload) - known)
        if unknown:
            raise SpecError("unknown spec keys: %s" % ", ".join(unknown))
        spec = cls(**dict(payload))
        spec.validate()
        return spec

    @classmethod
    def from_json(cls, path: Path) -> "MechanismSpec":
        path = Path(path)
        with path.open("r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    # -------------------------------------------------------------- validate
    def validate(self) -> None:
        if not re.match(r"^[a-z0-9_]+_v\d+$", self.mechanism_id):
            raise SpecError(
                "mechanism_id %r must be snake_case ending in _v<N>" % (self.mechanism_id,)
            )
        if self.side_mode not in SIDE_MODES:
            raise SpecError("side_mode %r not in %s" % (self.side_mode, SIDE_MODES))
        if self.multiplicity_family not in FAMILIES:
            raise SpecError(
                "multiplicity_family %r not in %s" % (self.multiplicity_family, FAMILIES)
            )
        if not self.data_sources:
            raise SpecError("data_sources is empty: nothing to check at gate 0")
        if not self.universe:
            raise SpecError("universe is empty")
        if len(set(self.universe)) != len(self.universe):
            raise SpecError("universe contains duplicates")
        if not self.entry_rule:
            raise SpecError("entry_rule is empty: there is no rule to preregister")
        if not self.exit_rule:
            raise SpecError("exit_rule is empty: an entry without an exit is not a rule")

        self._validate_hypothesis()
        self._validate_no_ranges()
        self._validate_sensitivities()
        self._validate_declustering()
        self._validate_windows()
        # Parsing side effects: fail here rather than mid-run.
        self.horizon_bounds_seconds()
        CostModel.from_spec(self.cost_model)

    def _validate_hypothesis(self) -> None:
        text = ("%s %s" % (self.hypothesis, self.economic_reason)).lower()
        for pat in BANNED_HYPOTHESIS_PATTERNS:
            if re.search(pat, text):
                raise SpecError(
                    "gate 1: hypothesis matches banned pattern %r — describe who is "
                    "forced to do what, not which indicator co-moves" % (pat,)
                )
        if len(self.hypothesis.strip()) < 40:
            raise SpecError("gate 1: hypothesis is too short to be falsifiable")
        if len(self.economic_reason.strip()) < 60:
            raise SpecError(
                "gate 1: economic_reason must name the agent, the constraint and why "
                "the mispricing is not arbitraged away instantly"
            )

    def _validate_no_ranges(self) -> None:
        for name in ("entry_rule", "exit_rule", "sensitivities"):
            found = _contains_range(getattr(self, name), name)
            if found and name != "sensitivities":
                raise SpecError("gate 5: %s — declare one value, or declare sensitivities" % found)

    def _validate_sensitivities(self) -> None:
        if len(self.sensitivities) > MAX_SENSITIVITIES:
            raise SpecError(
                "at most %d sensitivities, got %d (%s)"
                % (MAX_SENSITIVITIES, len(self.sensitivities), ", ".join(sorted(self.sensitivities)))
            )

    def _validate_declustering(self) -> None:
        rule = self.declustering_rule
        if "method" not in rule:
            raise SpecError(
                "declustering_rule.method must be declared before any result is seen "
                "(first_link | complete_link | fixed_windows): the choice changes the "
                "sample size by orders of magnitude"
            )
        if "same_symbol_gap_seconds" not in rule and "window_seconds" not in rule:
            raise SpecError(
                "declustering_rule needs window_seconds (or same_symbol_gap_seconds)"
            )

    def _validate_windows(self) -> None:
        win = self.validation_window
        for key in ("start", "end"):
            if key not in win:
                raise SpecError("validation_window.%s is required" % key)
        if str(win["start"]) >= str(win["end"]):
            raise SpecError("validation_window.start must precede end")

    # ------------------------------------------------------------- derived
    def horizon_bounds_seconds(self) -> Tuple[float, float]:
        """``(min, max)`` holding time in seconds.

        ``"60s"`` gives ``(60, 60)``; ``"5s_60s"`` gives ``(5, 60)``.  The lower
        bound drives the latency budget because it is the strictest.
        """
        token = str(self.horizon).strip().lower()
        parts = token.split("_")
        if len(parts) == 1:
            v = parse_duration_seconds(parts[0])
            return (v, v)
        if len(parts) == 2:
            lo, hi = parse_duration_seconds(parts[0]), parse_duration_seconds(parts[1])
            if lo > hi:
                raise SpecError("horizon %r has its bounds inverted" % (self.horizon,))
            return (lo, hi)
        raise SpecError("horizon %r must be '<d>' or '<d_min>_<d_max>'" % (self.horizon,))

    def max_data_latency_ms(self) -> float:
        """Latency budget: ``horizon / 4``.

        A four-hour mechanism tolerates one hour of data lag; a sixty-second one
        tolerates fifteen seconds.  Measured against the shortest horizon.
        """
        lo, _ = self.horizon_bounds_seconds()
        return lo * 1000.0 / 4.0

    def n_trials(self) -> int:
        """Trials this mechanism charges to its family: primary plus sensitivities."""
        return 1 + len(self.sensitivities)

    def cost(self) -> CostModel:
        return CostModel.from_spec(self.cost_model)

    def decision_fields(self) -> Dict[str, Any]:
        return _canonical(
            {
                "mechanism_id": self.mechanism_id,
                "universe": sorted(self.universe),
                "timeframe": self.timeframe,
                "horizon": self.horizon,
                "side_mode": self.side_mode,
                "entry_rule": self.entry_rule,
                "exit_rule": self.exit_rule,
                "declustering_rule": self.declustering_rule,
                "cost_model": self.cost_model,
                "sensitivities": self.sensitivities,
                "validation_window": self.validation_window,
            }
        )

    def rules_hash(self) -> str:
        blob = json.dumps(self.decision_fields(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
