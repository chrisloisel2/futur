"""
src/institutional/experiments/experiment_logger.py
─────────────────────────────────────────────────────────────────────────────
Tracking des expériences — chaque run est enregistré et versionnés.

Aucune expérience n'est valide sans record.
Le registre sert à comparer les runs et éviter de répéter les mêmes tests.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.institutional.contracts import ExperimentRecord, RobustnessScore

REGISTRY_ROOT = Path(__file__).parents[3] / "artifacts" / "institutional" / "experiments"


def _generate_run_id(prefix: str = "exp") -> str:
    return f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


class ExperimentLogger:
    """
    Enregistre et charge les expériences ML institutionnelles.

    Usage
    -----
    logger = ExperimentLogger()
    run_id = logger.start(engine_name="INSTITUTIONAL", signal_name="trend_following_v1")

    # ... training ...

    logger.finish(
        run_id=run_id,
        metrics={"pf": 1.35, "sharpe": 1.2},
        robustness_tests={"shuffle": {"pf": 0.98}, "cost_x2": {"pf": 1.10}},
        decision="PAPER",
    )
    """

    def __init__(self, registry_dir: Optional[Path] = None):
        self.registry_dir = Path(registry_dir or REGISTRY_ROOT)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._registry_file = self.registry_dir / "experiments_registry.json"
        self._registry = self._load_registry()

    def start(
        self,
        engine_name: str,
        signal_name: str,
        assets: List[str],
        features_version: str = "unknown",
        labels_version: str = "unknown",
        model_type: str = "unknown",
        model_params: Optional[Dict[str, Any]] = None,
        train_period: Optional[Dict[str, str]] = None,
        validation_period: Optional[Dict[str, str]] = None,
        test_period: Optional[Dict[str, str]] = None,
        walk_forward_config: Optional[Dict[str, Any]] = None,
        cost_config: Optional[Dict[str, Any]] = None,
        risk_config: Optional[Dict[str, Any]] = None,
        notes: str = "",
    ) -> str:
        """Démarre un run et retourne le run_id."""
        run_id = _generate_run_id(prefix=f"{engine_name[:3].lower()}")

        record = ExperimentRecord(
            experiment_id=run_id,
            timestamp=pd.Timestamp.utcnow(),
            engine_name=engine_name,
            signal_name=signal_name,
            assets=assets,
            features_version=features_version,
            labels_version=labels_version,
            model_type=model_type,
            model_params=model_params or {},
            train_period=train_period or {},
            validation_period=validation_period or {},
            test_period=test_period or {},
            walk_forward_config=walk_forward_config or {},
            cost_config=cost_config or {"cost_bps": 10.0},
            risk_config=risk_config or {},
            metrics={},
            robustness_tests={},
            decision="PENDING",
            notes=notes,
            artifact_paths={},
        )

        # Sauvegarder le record (état initial)
        record_path = self.registry_dir / f"{run_id}.json"
        record.save(record_path)

        # Ajouter au registre
        self._registry[run_id] = {
            "timestamp": str(record.timestamp),
            "engine_name": engine_name,
            "signal_name": signal_name,
            "assets": assets,
            "decision": "PENDING",
            "path": str(record_path),
        }
        self._save_registry()

        return run_id

    def finish(
        self,
        run_id: str,
        metrics: Dict[str, float],
        robustness_tests: Dict[str, Any],
        decision: str,
        artifact_paths: Optional[Dict[str, str]] = None,
        notes: str = "",
    ) -> None:
        """Finalise un run avec les métriques et la décision."""
        record_path = self.registry_dir / f"{run_id}.json"
        if not record_path.exists():
            raise FileNotFoundError(f"Run {run_id} non trouvé")

        record = ExperimentRecord.load(record_path)
        record.metrics = metrics
        record.robustness_tests = robustness_tests
        record.decision = decision
        record.artifact_paths = artifact_paths or {}
        if notes:
            record.notes = notes
        record.validate()
        record.save(record_path)

        # Mettre à jour le registre
        if run_id in self._registry:
            self._registry[run_id]["decision"] = decision
            self._registry[run_id]["metrics_summary"] = {
                k: round(v, 4) for k, v in metrics.items()
                if k in ["pf", "sharpe", "cagr", "auc_ovr"]
            }
        self._save_registry()

    def list_experiments(
        self,
        engine_name: Optional[str] = None,
        decision: Optional[str] = None,
        signal_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retourne les expériences filtrées."""
        results = []
        for run_id, meta in self._registry.items():
            if engine_name and meta.get("engine_name") != engine_name:
                continue
            if decision and meta.get("decision") != decision:
                continue
            if signal_name and meta.get("signal_name") != signal_name:
                continue
            results.append({**meta, "run_id": run_id})
        return sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True)

    def load(self, run_id: str) -> ExperimentRecord:
        record_path = self.registry_dir / f"{run_id}.json"
        return ExperimentRecord.load(record_path)

    def get_best_experiments(self, metric: str = "pf", top_n: int = 5) -> List[Dict]:
        """Retourne les N meilleures expériences selon une métrique."""
        all_exp = self.list_experiments()
        scored = []
        for exp in all_exp:
            score = exp.get("metrics_summary", {}).get(metric, 0)
            scored.append((score, exp))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:top_n]]

    def _load_registry(self) -> Dict[str, Any]:
        if self._registry_file.exists():
            return json.loads(self._registry_file.read_text())
        return {}

    def _save_registry(self) -> None:
        self._registry_file.write_text(json.dumps(self._registry, indent=2, default=str))
