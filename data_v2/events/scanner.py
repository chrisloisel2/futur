"""
data_v2/events/scanner.py
─────────────────────────────────────────────────────────────────────────────
Event Scanner V1 statistics + classification, per reports/
EVENT_SCANNER_V1_PROTOCOL.md's "Statistics computed" and "Classification"
sections. No ML anywhere in this module -- it only aggregates labelled
events (data_v2.events.labels) into N/expectancy/PF/MFE/MAE/cost-adjusted
figures and applies the pre-registered KILL/WEAK/CANDIDATE rule.

Do not run build_scan_report against real Data V2 output until
reports/DATA_V2_READINESS.json says DATA_V2_READY: true (see the protocol's
own "Order of operations" section) -- this module has no way to enforce
that itself (it just aggregates whatever labelled events it's given), so
the caller is responsible for gating on the readiness report first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

HORIZONS = ("15m", "1h", "4h", "8h")

# cost model: 2x taker fee (open+close) + 1 tick slippage estimate per side,
# expressed in the same log-return-ish units as residual_ret_h. Matches the
# "cost_rt" convention already used elsewhere on this project (e.g.
# scripts/backtest_ctrend_v1.py's cost_rt=0.0030 round-trip default) --
# reused here rather than inventing a new figure. cost x2 doubles it.
COST_X1 = 0.0030
COST_X2 = COST_X1 * 2

MIN_YEAR_N_FOR_CONSISTENCY_CHECK = 20
MIN_POOLED_N = 100
CANDIDATE_PF_MIN = 1.15

LARGE_ALT_TIER_SIZE = 20  # top-20 by 30d median quote volume at event time, per protocol


def assign_asset_tier(symbol: str, *, is_large_alt: bool) -> str:
    if symbol == "BTCUSDT":
        return "BTC"
    if symbol == "ETHUSDT":
        return "ETH"
    return "large_alt" if is_large_alt else "small_alt"


@dataclass
class HorizonStats:
    horizon: str
    n: int
    gross_expectancy: float
    net_expectancy_cost_x1: float
    net_expectancy_cost_x2: float
    win_rate: float
    profit_factor: float
    mean_mfe: float
    mean_mae: float

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon, "n": self.n,
            "gross_expectancy": self.gross_expectancy,
            "net_expectancy_cost_x1": self.net_expectancy_cost_x1,
            "net_expectancy_cost_x2": self.net_expectancy_cost_x2,
            "win_rate": self.win_rate, "profit_factor": self.profit_factor,
            "mean_mfe": self.mean_mfe, "mean_mae": self.mean_mae,
        }


def _horizon_stats(returns: pd.Series, mfe: pd.Series, mae: pd.Series, horizon: str) -> HorizonStats:
    r = returns.dropna()
    n = len(r)
    if n == 0:
        return HorizonStats(horizon, 0, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan)
    gross = float(r.mean())
    wins = r[r > 0]
    losses = r[r < 0]
    win_rate = float((r > 0).mean())
    profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else np.nan
    return HorizonStats(
        horizon=horizon, n=n,
        gross_expectancy=gross,
        net_expectancy_cost_x1=gross - COST_X1,
        net_expectancy_cost_x2=gross - COST_X2,
        win_rate=win_rate, profit_factor=profit_factor,
        mean_mfe=float(mfe.dropna().mean()) if mfe.notna().any() else np.nan,
        mean_mae=float(mae.dropna().mean()) if mae.notna().any() else np.nan,
    )


@dataclass
class FamilyReport:
    family: str
    n_total: int
    by_horizon: dict = field(default_factory=dict)
    by_year: dict = field(default_factory=dict)
    by_tier: dict = field(default_factory=dict)
    classification: str = "KILL"
    classification_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "family": self.family, "n_total": self.n_total,
            "by_horizon": {h: s.to_dict() for h, s in self.by_horizon.items()},
            "by_year": {y: {h: s.to_dict() for h, s in hs.items()} for y, hs in self.by_year.items()},
            "by_tier": {t: {h: s.to_dict() for h, s in hs.items()} for t, hs in self.by_tier.items()},
            "classification": self.classification,
            "classification_reason": self.classification_reason,
        }


def _classify(by_year_cost_x1: dict, pooled_cost_x1: float, pooled_cost_x2: float,
              pooled_pf: float, pooled_n: int) -> tuple[str, str]:
    if pooled_n < MIN_POOLED_N:
        return "KILL", f"pooled N={pooled_n} < {MIN_POOLED_N}"
    if pooled_cost_x1 is not None and pooled_cost_x1 <= 0:
        return "KILL", "net expectancy (cost x1) <= 0 pooled"

    nonzero_signs = {np.sign(v["net_expectancy_cost_x1"]) for v in by_year_cost_x1.values()
                      if v["n"] >= MIN_YEAR_N_FOR_CONSISTENCY_CHECK and v["net_expectancy_cost_x1"] != 0}
    if len(nonzero_signs) > 1:
        return "KILL", "sign flips between years with >=20 events"

    if pooled_cost_x2 is None or pooled_cost_x2 <= 0 or (pooled_pf or 0) < CANDIDATE_PF_MIN:
        return "WEAK", f"cost x2 net={pooled_cost_x2}, PF={pooled_pf} < {CANDIDATE_PF_MIN}"

    return "CANDIDATE", f"cost x2 net={pooled_cost_x2:.5f} > 0, PF={pooled_pf:.2f} >= {CANDIDATE_PF_MIN}, N={pooled_n}"


def build_family_report(
    labelled_events: pd.DataFrame, *, family: str, tier_by_symbol: Optional[dict] = None
) -> FamilyReport:
    """labelled_events: output of data_v2.events.labels.label_events(_multi_symbol),
    must carry a 'timestamp' column and residual_ret_h/MFE_h/MAE_h for each
    horizon in HORIZONS, plus 'symbol'."""
    n_total = len(labelled_events)
    by_horizon = {}
    by_year = {}
    by_tier = {}

    if n_total == 0:
        return FamilyReport(family=family, n_total=0, classification="KILL",
                             classification_reason="no events detected")

    years = pd.to_datetime(labelled_events["timestamp"]).dt.year
    for horizon in HORIZONS:
        ret_col, mfe_col, mae_col = f"residual_ret_{horizon}", f"MFE_{horizon}", f"MAE_{horizon}"
        by_horizon[horizon] = _horizon_stats(labelled_events[ret_col], labelled_events[mfe_col], labelled_events[mae_col], horizon)

        for year in sorted(years.unique()):
            year_mask = years == year
            stats = _horizon_stats(
                labelled_events.loc[year_mask, ret_col], labelled_events.loc[year_mask, mfe_col],
                labelled_events.loc[year_mask, mae_col], horizon,
            )
            by_year.setdefault(str(year), {})[horizon] = stats

        if tier_by_symbol:
            tiers = labelled_events["symbol"].map(tier_by_symbol)
            for tier in sorted(tiers.dropna().unique()):
                tier_mask = tiers == tier
                stats = _horizon_stats(
                    labelled_events.loc[tier_mask, ret_col], labelled_events.loc[tier_mask, mfe_col],
                    labelled_events.loc[tier_mask, mae_col], horizon,
                )
                by_tier.setdefault(tier, {})[horizon] = stats

    pooled = by_horizon["1h"]  # classification anchored on the 1h horizon
    by_year_1h = {y: hs["1h"].to_dict() for y, hs in by_year.items()}
    classification, reason = _classify(
        by_year_1h, pooled.net_expectancy_cost_x1, pooled.net_expectancy_cost_x2,
        pooled.profit_factor, pooled.n,
    )

    return FamilyReport(
        family=family, n_total=n_total, by_horizon=by_horizon, by_year=by_year, by_tier=by_tier,
        classification=classification, classification_reason=reason,
    )
