from __future__ import annotations

from typing import Dict

import pandas as pd


class SpecialistRouter:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def route(self, state_df: pd.DataFrame) -> Dict[str, float]:
        if not self.enabled or state_df.empty:
            return {}
        return {"expert_a": 0.5, "expert_b": 0.5}
