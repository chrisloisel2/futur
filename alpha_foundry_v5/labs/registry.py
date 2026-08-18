from __future__ import annotations

from typing import Dict

import pandas as pd

from .base import LabSpec
from .strict_catalog import LABS
from .plugins import PLUGIN_REGISTRY
from .strict_options import StrictOptionsPlugin


class LabRegistry:
    def __init__(self):
        self.specs = dict(LABS)
        self.plugins = dict(PLUGIN_REGISTRY)
        self.plugins["options"] = StrictOptionsPlugin()
        sources = [s.economic_source_id for s in self.specs.values()]
        if len(sources) != len(set(sources)):
            raise ValueError("economic_source_id must be unique across labs")

    def spec(self, lab_id: str) -> LabSpec:
        if lab_id not in self.specs:
            raise KeyError(lab_id)
        return self.specs[lab_id]

    def readiness(self, lab_id: str, frame: pd.DataFrame) -> Dict[str, object]:
        spec = self.spec(lab_id)
        status = dict(self.plugins[spec.plugin].readiness(frame, spec))
        status.update({
            "name": spec.name,
            "economic_source_id": spec.economic_source_id,
            "domains": tuple(domain.value for domain in spec.domains),
            "default_target": spec.default_target,
            "plugin": spec.plugin,
        })
        return status

    def materialize_features(self, lab_id: str, frame: pd.DataFrame) -> pd.DataFrame:
        spec = self.spec(lab_id)
        status = self.readiness(lab_id, frame)
        if not status["ready"]:
            raise ValueError("lab %s not ready: %s" % (lab_id, status))
        plugin = self.plugins[spec.plugin]
        if "symbol" not in frame.columns:
            return plugin.build_features(frame, spec)
        pieces = []
        for _symbol, group in frame.groupby("symbol", sort=False):
            ordered = group.sort_values("asof_ns", kind="mergesort") if "asof_ns" in group else group
            features = plugin.build_features(ordered, spec)
            features.index = ordered.index
            pieces.append(features)
        if not pieces:
            return pd.DataFrame(index=frame.index)
        return pd.concat(pieces, axis=0).reindex(frame.index)

    def audit(self, frame: pd.DataFrame) -> Dict[str, Dict[str, object]]:
        return {lab_id: self.readiness(lab_id, frame) for lab_id in sorted(self.specs)}
