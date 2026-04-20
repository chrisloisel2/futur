from __future__ import annotations

import pandas as pd


def compute_macro_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    if "macro_risk_on" in df:
        out["macro_risk_on"] = df["macro_risk_on"].astype(float)
    return out
