"""
src/institutional/engines/vol_forecast_layer/panel.py
─────────────────────────────────────────────────────────────────────────────
Orchestration : construit le panel journalier complet de
VOL_FORECAST_LAYER_V1 -- une ligne par jour calendaire UTC où atm_iv_traded
est disponible (features/BTC_daily.parquet, la série la plus courte des
sources utilisées).

Colonnes produites (voir freeze_spec.json pour le détail complet) :
  day (event_time), forecast_made_at, forecast_horizon, target_period_start,
  target_period_end, target_realized_at,
  rv_iv_spread(+_z/_oriented_z), far_otm_put_share(+_z/_oriented_z),
  block_count_24h(+_z/_oriented_z), combined_forecast_z, n_signals_available,
  forecast_direction, confidence,
  current_realized_vol (sameday_rv), atm_iv_traded, atm_iv_z, iv_regime,
  funding_rate_mean, funding_ann_pct, funding_regime,
  actual_realized_rv (NULL -- rempli plus tard par backfill.py),
  rv_backfilled_at (NULL).

Ne modifie jamais les fichiers sous data/ -- lecture seule partout. Réutilise
(n'importe, ne réimplémente pas) src/institutional/engines/
funding_basis_disagreement/panel.py::load_live_perp_funding_daily pour la
jambe funding, exactement la même source/formule que
FUNDING_BASIS_DISAGREEMENT_V2 (funding_ann_pct = funding_rate_mean*3*365*100).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.institutional.engines.funding_basis_disagreement.panel import (
    load_live_perp_funding_daily,
)
from src.institutional.engines.vol_forecast_layer.combine import (
    Z_WINDOW_DAYS, add_causal_zscores, causal_zscore, combine_forecast,
)
from src.institutional.engines.vol_forecast_layer.options_signals import (
    compute_daily_options_flow_signals, load_atm_iv_daily,
)
from src.institutional.engines.vol_forecast_layer.realized_vol import (
    compute_daily_realized_vol,
)

FORECAST_HORIZON = "24h"
FUNDING_NEAR_ZERO_ANN_PCT = 3.0   # |funding_ann_pct| < 3.0 -> NEAR_ZERO -- seuil déclaré arbitraire, pas fit
IV_REGIME_Z_THRESHOLD = 1.0        # défaut d'ingénierie, pas fit

PANEL_COLUMNS = [
    "day", "event_time", "forecast_made_at", "forecast_horizon",
    "target_period_start", "target_period_end", "target_realized_at",
    "rv_iv_spread", "rv_iv_spread_z", "rv_iv_spread_oriented_z",
    "far_otm_put_share", "far_otm_put_share_z", "far_otm_put_share_oriented_z",
    "block_count_24h", "block_count_24h_z", "block_count_24h_oriented_z",
    "combined_forecast_z", "n_signals_available", "forecast_direction", "confidence",
    "current_realized_vol", "atm_iv_traded", "atm_iv_z", "iv_regime",
    "funding_rate_mean", "funding_ann_pct", "funding_regime",
    "actual_realized_rv", "rv_backfilled_at",
]


def build_daily_panel(symbol: str = "BTCUSDT", currency: str = "BTC") -> pd.DataFrame:
    atm_iv = load_atm_iv_daily()
    if atm_iv.empty:
        return pd.DataFrame(columns=PANEL_COLUMNS)

    rv = compute_daily_realized_vol(symbol)
    flow = compute_daily_options_flow_signals(currency)
    funding = load_live_perp_funding_daily(symbol)

    panel = atm_iv.merge(
        rv[["day", "sameday_rv"]] if not rv.empty else pd.DataFrame(columns=["day", "sameday_rv"]),
        on="day", how="left",
    )
    panel = panel.merge(
        flow[["day", "far_otm_put_share", "block_count_24h"]] if not flow.empty
        else pd.DataFrame(columns=["day", "far_otm_put_share", "block_count_24h"]),
        on="day", how="left",
    )
    if not funding.empty:
        funding = funding.rename(columns={"date": "day"})
        funding["day"] = pd.to_datetime(funding["day"], utc=True).dt.floor("D")
        panel = panel.merge(funding[["day", "funding_rate_mean"]], on="day", how="left")
    else:
        panel["funding_rate_mean"] = np.nan

    panel = panel.sort_values("day").reset_index(drop=True)

    # M2 : rv_iv_spread = atm_iv_traded - sameday_rv (NULL si RV manquante ce jour)
    panel["rv_iv_spread"] = panel["atm_iv_traded"] - panel["sameday_rv"]

    panel = add_causal_zscores(panel)
    panel = combine_forecast(panel)

    # IV state
    panel["atm_iv_z"] = causal_zscore(panel["atm_iv_traded"], window_days=Z_WINDOW_DAYS)
    panel["iv_regime"] = panel["atm_iv_z"].apply(_iv_regime)

    # funding state (réutilise EXACTEMENT la formule de funding_basis_disagreement/panel.py)
    panel["funding_ann_pct"] = panel["funding_rate_mean"] * 3 * 365 * 100.0
    panel["funding_regime"] = panel["funding_ann_pct"].apply(_funding_regime)

    # Cadence de décision : un forecast/jour, émis à la clôture du jour `day`,
    # portant sur la RV réalisée du jour SUIVANT (target_period_start..end).
    panel["event_time"] = panel["day"]
    panel["forecast_made_at"] = panel["day"]
    panel["forecast_horizon"] = FORECAST_HORIZON
    panel["target_period_start"] = panel["day"] + pd.Timedelta(days=1)
    panel["target_period_end"] = panel["day"] + pd.Timedelta(days=2)
    panel["target_realized_at"] = panel["target_period_end"]

    panel["current_realized_vol"] = panel["sameday_rv"]
    panel["actual_realized_rv"] = np.nan
    panel["rv_backfilled_at"] = None   # object dtype (string ISO plus tard), jamais Timestamp/NaT mélangé

    for c in PANEL_COLUMNS:
        if c not in panel.columns:
            panel[c] = np.nan
    return panel[PANEL_COLUMNS].reset_index(drop=True)


def _iv_regime(z):
    if pd.isna(z):
        return None
    if z > IV_REGIME_Z_THRESHOLD:
        return "HIGH"
    if z < -IV_REGIME_Z_THRESHOLD:
        return "LOW"
    return "MID"


def _funding_regime(v):
    if pd.isna(v):
        return None
    if abs(v) < FUNDING_NEAR_ZERO_ANN_PCT:
        return "NEAR_ZERO"
    return "POSITIVE" if v > 0 else "NEGATIVE"
