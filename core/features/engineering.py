# compat shim — implémentation dans level_0/feature_engineering.py
# noqa: F401, F403
from core.settings import configure_project_imports

configure_project_imports()

from ai.level_0.feature_engineering import *  # noqa: F401, F403
from ai.level_0.feature_engineering import (
    compute_flow_features, compute_long_features, compute_short_features,
)
