from __future__ import annotations

from pathlib import Path
from typing import Dict

import json


class DashboardsExporter:
    def __init__(self, out_dir: str = "artifacts/monitoring/reports"):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def export(self, run_id: str, reports: Dict) -> Path:
        path = self.out_dir / f"monitoring_{run_id}.json"
        path.write_text(json.dumps(reports, default=str, indent=2))
        return path
