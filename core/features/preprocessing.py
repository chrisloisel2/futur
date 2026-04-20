# compat shim — implémentation dans level_0/preprocessing.py
# noqa: F401, F403
from core.settings import configure_project_imports

configure_project_imports()

from ai.level_0.preprocessing import *  # noqa: F401, F403
from ai.level_0.preprocessing import (
    chronological_split, get_X, fit_scaler, load_csv,
)
