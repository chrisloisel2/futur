from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .hashing import atomic_write_json, sha256_obj
from .labs.base import LabSpec

# Same per-plugin selection heuristic alpha_foundry_v5_discover.py's load_frame() used to
# recompute on every run. Moved here so it can be resolved ONCE against a real dataset's
# column list and then frozen -- feature_set_id becomes a pointer to that frozen list, not
# a label for "whatever this heuristic happens to produce today."
_EVENT_TOKENS: tuple[str, ...] = (
    "signed_notional", "flow_imbalance", "cvd", "absorption", "interarrival_cv",
    "trades_per_second", "flow_acceleration", "flow_jerk", "ofi", "queue_imbalance",
    "cancel", "remove", "queue_pressure", "replenishment", "depletion", "book_event_intensity",
)
_SHOCK_TOKENS: tuple[str, ...] = ("spread_bps", "depth_", "notional_to_move", "dispersion_bps")
_LEVERAGE_TOKENS: tuple[str, ...] = ("open_interest", "funding", "basis", "premium", "liquidation")


def resolve_feature_columns(spec: LabSpec, all_columns: Sequence[str]) -> tuple[str, ...]:
    """The explicit, lab-specific (well, currently plugin-specific -- see P0-3 notes in
    docs/) column list a discovery/confirmation run for this lab should consume. Callers
    must freeze the result via write_feature_set() before using it in a hypothesis --
    resolving fresh on every run is exactly the bug this module exists to remove.
    """
    selected: set[str] = set()
    for column in all_columns:
        name = str(column)
        lower = name.lower()
        if spec.plugin == "cross_venue":
            if name.endswith(("__price_dislocation_bps", "__dislocation_bps", "__price_mid")):
                selected.add(name)
        elif spec.plugin == "event_microstructure":
            if any(token in lower for token in _EVENT_TOKENS):
                selected.add(name)
        elif spec.plugin == "shock_propagation":
            if any(token in lower for token in _SHOCK_TOKENS):
                selected.add(name)
        elif spec.plugin == "leverage" and any(token in lower for token in _LEVERAGE_TOKENS):
            selected.add(name)
    if "price_fair_value" in all_columns:
        selected.add("price_fair_value")
    return tuple(c for c in all_columns if c in selected)


@dataclass(frozen=True)
class FeatureSet:
    feature_set_id: str
    lab_id: str
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.feature_set_id or not self.lab_id:
            raise ValueError("feature_set_id and lab_id are required")
        if not self.columns:
            raise ValueError("a feature set must select at least one column")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("feature set columns must be unique")

    @property
    def digest(self) -> str:
        return sha256_obj(self)


def write_feature_set(feature_set: FeatureSet, path: str) -> None:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"feature sets are immutable: {target}")
    payload = asdict(feature_set)
    payload["digest"] = feature_set.digest
    atomic_write_json(str(target), payload)


def load_feature_set(path: str) -> FeatureSet:
    row = json.loads(Path(path).read_text(encoding="utf-8"))
    row.pop("digest", None)
    row["columns"] = tuple(row["columns"])
    return FeatureSet(**row)
