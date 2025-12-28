from __future__ import annotations

import pandas as pd


class SpecialistExpert:
    def predict(self, state_df: pd.DataFrame) -> pd.Series:
        return pd.Series(0.0, index=state_df.index)


class SpecialistStack:
    def __init__(self, experts):
        self.experts = experts

    def predict(self, state_df: pd.DataFrame):
        outputs = {}
        for name, model in self.experts.items():
            outputs[name] = model.predict(state_df)
        return outputs
