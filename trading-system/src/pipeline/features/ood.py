from __future__ import annotations

import pandas as pd


def compute_ood_scores(df: pd.DataFrame, reference: pd.DataFrame | None = None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    ref_mean = reference.mean() if reference is not None and not reference.empty else 0
    ref_std = reference.std() if reference is not None and not reference.empty else 1
    out = pd.DataFrame(index=df.index)
    for col in df.select_dtypes(include='number').columns:
        out[f"ood_z_{col}"] = (df[col] - ref_mean.get(col, 0)) / (ref_std.get(col, 1) + 1e-9)
    out["symbol"] = df.get("symbol")
    return out
