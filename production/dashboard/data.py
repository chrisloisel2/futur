from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.artifacts.pipeline import (
    find_latest_pipeline_run,
    load_pipeline_config,
    load_pipeline_manifest,
    load_pipeline_summary,
)
from core.settings import get_settings


def load_pipeline_dashboard_snapshot(run_dir: Optional[str | Path] = None) -> dict:
    if run_dir is None:
        resolved = find_latest_pipeline_run(get_settings().paths.pipeline_runs_dir)
        if resolved is None:
            raise FileNotFoundError("Aucun run pipeline canonique valide trouvé.")
        run_path = resolved
    else:
        run_path = Path(run_dir)

    return {
        "run_dir": str(run_path),
        "manifest": load_pipeline_manifest(run_path),
        "config": load_pipeline_config(run_path),
        "summary": load_pipeline_summary(run_path),
    }
