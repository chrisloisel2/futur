"""
research/experiment_tracker.py — Experiment Tracking (JSON-backed)

Run lifecycle:
  run_id = tracker.start_run("walk_forward_v5", params={"folds": 4})
  tracker.log_metrics(run_id, {"pf_median": 1.18, "n_ok": 2})
  tracker.end_run(run_id, status="completed")

Compare:
  best = tracker.best_run("pf_median", higher_is_better=True)
  df   = tracker.compare_runs(metric="pf_median")
"""
from __future__ import annotations

import json
import uuid
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
import functools


_RUNS_DIR = Path(__file__).parent / "runs"


class ExperimentTracker:
    def __init__(self, runs_dir: Optional[Path] = None):
        self._dir = Path(runs_dir) if runs_dir else _RUNS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(
        self,
        name: str,
        params: dict[str, Any] = {},
        tags: dict[str, str] = {},
    ) -> str:
        run_id = uuid.uuid4().hex[:12]
        run = {
            "run_id":    run_id,
            "name":      name,
            "status":    "running",
            "params":    params,
            "tags":      tags,
            "metrics":   {},    # key → list of (step, value)
            "artifacts": [],
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time":  None,
            "duration_s": None,
        }
        self._write(run_id, run)
        return run_id

    def log_metric(self, run_id: str, key: str, value: float, step: int = 0) -> None:
        run = self._read(run_id)
        run["metrics"].setdefault(key, [])
        run["metrics"][key].append({"step": step, "value": value})
        self._write(run_id, run)

    def log_metrics(self, run_id: str, metrics: dict[str, float], step: int = 0) -> None:
        run = self._read(run_id)
        for k, v in metrics.items():
            run["metrics"].setdefault(k, [])
            run["metrics"][k].append({"step": step, "value": v})
        self._write(run_id, run)

    def log_param(self, run_id: str, key: str, value: Any) -> None:
        run = self._read(run_id)
        run["params"][key] = value
        self._write(run_id, run)

    def log_artifact(self, run_id: str, path: str) -> None:
        run = self._read(run_id)
        run["artifacts"].append(str(path))
        self._write(run_id, run)

    def end_run(self, run_id: str, status: str = "completed") -> None:
        run = self._read(run_id)
        now = datetime.now(timezone.utc)
        run["status"] = status
        run["end_time"] = now.isoformat()
        start = datetime.fromisoformat(run["start_time"])
        run["duration_s"] = round((now - start).total_seconds(), 1)
        self._write(run_id, run)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict:
        return self._read(run_id)

    def list_runs(self, name: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        runs = []
        for p in self._dir.glob("*.json"):
            try:
                r = json.loads(p.read_text())
                if name and r.get("name") != name:
                    continue
                if status and r.get("status") != status:
                    continue
                runs.append(r)
            except Exception:
                continue
        runs.sort(key=lambda r: r.get("start_time", ""), reverse=True)
        return runs

    def best_run(self, metric: str, higher_is_better: bool = True) -> Optional[dict]:
        runs = self.list_runs(status="completed")
        candidates = []
        for r in runs:
            vals = r["metrics"].get(metric)
            if vals:
                last_val = vals[-1]["value"]
                candidates.append((last_val, r))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=higher_is_better)
        return candidates[0][1]

    def compare_runs(
        self,
        metric: str,
        n_best: int = 10,
        higher_is_better: bool = True,
    ) -> list[dict]:
        runs = self.list_runs(status="completed")
        rows = []
        for r in runs:
            vals = r["metrics"].get(metric)
            if vals:
                rows.append({
                    "run_id":   r["run_id"],
                    "name":     r["name"],
                    metric:     vals[-1]["value"],
                    "params":   r["params"],
                    "start":    r["start_time"][:10],
                })
        rows.sort(key=lambda x: x[metric], reverse=higher_is_better)
        return rows[:n_best]

    def last_metric(self, run_id: str, key: str) -> Optional[float]:
        run = self._read(run_id)
        vals = run["metrics"].get(key)
        return vals[-1]["value"] if vals else None

    # ------------------------------------------------------------------
    # Decorator
    # ------------------------------------------------------------------

    def track(self, name: str, params: dict = {}):
        """
        @tracker.track("walk_forward_v5", params={"folds": 4})
        def run_experiment(df, ...):
            return {"pf_median": 1.18, "n_ok": 2}
        """
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                run_id = self.start_run(name, params={**params, **kwargs})
                try:
                    result = fn(*args, **kwargs)
                    if isinstance(result, dict):
                        self.log_metrics(run_id, {
                            k: v for k, v in result.items()
                            if isinstance(v, (int, float))
                        })
                    self.end_run(run_id, "completed")
                    return result
                except Exception as e:
                    self.log_param(run_id, "_error", str(e))
                    self.end_run(run_id, "failed")
                    raise
            return wrapper
        return decorator

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _path(self, run_id: str) -> Path:
        return self._dir / f"{run_id}.json"

    def _write(self, run_id: str, data: dict) -> None:
        self._path(run_id).write_text(json.dumps(data, indent=2))

    def _read(self, run_id: str) -> dict:
        p = self._path(run_id)
        if not p.exists():
            raise FileNotFoundError(f"Run {run_id} not found")
        return json.loads(p.read_text())


# Module-level singleton
tracker = ExperimentTracker()
