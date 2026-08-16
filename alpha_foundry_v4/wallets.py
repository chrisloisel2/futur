from __future__ import annotations

import numpy as np
import pandas as pd


def wallet_intelligence_table(trades: pd.DataFrame, min_trades: int = 20) -> pd.DataFrame:
    required = {"wallet", "signed_notional", "markout_bps"}
    missing = required.difference(trades.columns)
    if missing:
        raise ValueError("missing wallet columns: %s" % sorted(missing))
    rows = []
    for wallet, group in trades.groupby("wallet", sort=True):
        if len(group) < int(min_trades):
            continue
        signed = pd.to_numeric(group["signed_notional"], errors="coerce")
        markout = pd.to_numeric(group["markout_bps"], errors="coerce")
        valid = signed.notna() & markout.notna()
        if int(valid.sum()) < int(min_trades):
            continue
        aligned = np.sign(signed[valid]) * markout[valid]
        rows.append({"wallet": str(wallet), "trades": int(valid.sum()), "mean_signed_markout_bps": float(aligned.mean()), "hit_rate": float((aligned > 0).mean()), "median_abs_notional": float(signed[valid].abs().median())})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    mu = out["mean_signed_markout_bps"].mean()
    sigma = out["mean_signed_markout_bps"].std(ddof=0)
    if sigma > 0:
        out["alpha_z"] = (out["mean_signed_markout_bps"] - mu) / sigma
    else:
        out["alpha_z"] = 0.0
    return out.sort_values("alpha_z", ascending=False).reset_index(drop=True)


def informed_flow(trades: pd.DataFrame, wallet_scores: pd.DataFrame) -> float:
    if trades.empty or wallet_scores.empty:
        return 0.0
    score = wallet_scores.set_index("wallet")["alpha_z"].to_dict()
    total = 0.0
    for row in trades.itertuples(index=False):
        wallet = str(getattr(row, "wallet"))
        signed_notional = float(getattr(row, "signed_notional"))
        total += signed_notional * float(score.get(wallet, 0.0))
    return float(total)
