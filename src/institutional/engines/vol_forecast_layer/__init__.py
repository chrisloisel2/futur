"""Moteur VOL_FORECAST_LAYER -- combinaison des 3 signaux options Deribit
DISCOVERY-stage (M2 rv_iv_spread, M6 far_otm_put_share, M17 block_count_24h,
reports/edge_discovery/alpha_hunt_2026-08-30/w6_options/REPORT.md) en UN
forecast quotidien de volatilité réalisée forward -- PAS une stratégie de
trading, PAS d'ordre simulé. Voir configs/live_alpha_registry.yaml
(alpha_id: VOL_FORECAST_LAYER_V1) et reports/live_alpha_lab/
VOL_FORECAST_LAYER_V1/freeze_spec.json pour la spécification complète.
"""
from src.institutional.engines.vol_forecast_layer.combine import (  # noqa: F401
    DIRECTION_Z_THRESHOLD, IC_CONFIDENCE_ANCHOR, ORIENTATION_SIGN,
    REFERENCE_ABS_IC, SIGNAL_COLUMNS, Z_WINDOW_DAYS,
    add_causal_zscores, causal_zscore, combine_forecast,
)
from src.institutional.engines.vol_forecast_layer.options_signals import (  # noqa: F401
    FAR_OTM_PUT_MONEYNESS, compute_daily_options_flow_signals, load_atm_iv_daily,
)
from src.institutional.engines.vol_forecast_layer.realized_vol import (  # noqa: F401
    ANNUALIZATION_FACTOR, compute_daily_realized_vol, load_hourly_returns,
)
from src.institutional.engines.vol_forecast_layer.panel import (  # noqa: F401
    FORECAST_HORIZON, PANEL_COLUMNS, build_daily_panel,
)
from src.institutional.engines.vol_forecast_layer.backfill import (  # noqa: F401
    backfill_actual_realized_rv,
)
