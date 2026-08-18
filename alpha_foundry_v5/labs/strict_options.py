from __future__ import annotations

import pandas as pd

from .base import LabPlugin, LabSpec


class StrictOptionsPlugin(LabPlugin):
    plugin_name = "options"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        out = pd.DataFrame(index=frame.index)
        for column in frame.columns:
            name = str(column)
            if not name.startswith("option__"):
                continue
            if name.endswith(("_available_ts_ns", "_receive_ts_ns")):
                continue
            out[name] = pd.to_numeric(frame[column], errors="coerce")
        for column in list(out.columns):
            out[column + "__change"] = out[column].diff()
        return out
