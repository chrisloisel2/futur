from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from .base import LabSpec
from .plugins import PLUGIN_REGISTRY
from .strict_catalog import LABS
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

    def readiness(self, lab_id: str, frame: pd.DataFrame) -> dict[str, object]:
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

    def materialize_features(self, lab_id: str, frame: pd.DataFrame, feature_columns: Sequence[str]) -> pd.DataFrame:
        """feature_columns is the frozen FeatureSet's own columns (feature_sets.py) --
        required, not optional, because the plugin must never see support/readiness/
        target columns that only happen to be present in `frame` for other reasons
        (support_projection_columns, target-construction columns, or simply every
        column in an unfiltered dataset). `readiness()` below still runs against the
        FULL frame -- it legitimately needs support/clock columns to check data
        availability; only what reaches plugin.build_features() is restricted to
        asof_ns + symbol + feature_columns.
        """
        spec = self.spec(lab_id)
        status = self.readiness(lab_id, frame)
        if not status["ready"]:
            raise ValueError("lab %s not ready: %s" % (lab_id, status))
        plugin = self.plugins[spec.plugin]
        model_columns = [c for c in ("asof_ns", "symbol") if c in frame.columns]
        model_columns += [c for c in feature_columns if c in frame.columns and c not in model_columns]
        model_frame = frame[model_columns]
        if "symbol" not in model_frame.columns:
            return plugin.build_features(model_frame, spec)
        pieces = []
        for _symbol, group in model_frame.groupby("symbol", sort=False):
            ordered = group.sort_values("asof_ns", kind="mergesort") if "asof_ns" in group else group
            features = plugin.build_features(ordered, spec)
            features.index = ordered.index
            pieces.append(features)
        if not pieces:
            return pd.DataFrame(index=model_frame.index)
        return pd.concat(pieces, axis=0).reindex(model_frame.index)

    def audit(self, frame: pd.DataFrame) -> dict[str, dict[str, object]]:
        return {lab_id: self.readiness(lab_id, frame) for lab_id in sorted(self.specs)}
