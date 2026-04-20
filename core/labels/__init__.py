# compat shim — implémentation dans level_0/labels.py
# noqa: F401, F403
from core.settings import configure_project_imports

configure_project_imports()

from ai.level_0.labels import *  # noqa: F401, F403
from ai.level_0.labels import (
    build_labels, build_bear_regime_label,
    compute_regime_col, compute_long_regime_col,
    compute_short_reversal_col, compute_long_reversal_col,
    get_train_labels,
)
