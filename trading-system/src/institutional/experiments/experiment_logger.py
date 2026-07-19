"""
trading-system/src/institutional/experiments/experiment_logger.py
═══════════════════════════════════════════════════════════════════════════════
Contrats ExperimentRecord et ExperimentLogger.

ExperimentRecord : snapshot immuable d'une expérience ML.
ExperimentLogger : registre persistant (JSONL) des expériences.

Règle fondamentale :
    Aucune expérience n'est valide sans ExperimentRecord.
    Toute décision (REJECT/PAPER/PROMOTE…) doit être enregistrée avec
    ses métriques, ses tests de robustesse et le hash du code.

Importe depuis : institutional.contracts (Verdict, EngineID).
N'importe PAS depuis : portfolio, risk, data.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

from institutional.contracts import Verdict


# ══════════════════════════════════════════════════════════════════════════════
# ExperimentRecord
# ══════════════════════════════════════════════════════════════════════════════

_RECORD_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """
    Snapshot immuable d'une expérience ML.

    Créé au démarrage du run (decision="PENDING") puis mis à jour
    une seule fois à la fin (immutabilité → nouveau record).

    Champs obligatoires non vides :
        experiment_id, engine_name, signal_name, run_id, assets, model_type

    Champs de versioning — hash du code source pour reproductibilité :
        code_hash : sha256[:16] du fichier de modèle principal
    """

    # ── Identité ──────────────────────────────────────────────────────────────
    experiment_id: str
    run_id:        str
    timestamp:     datetime
    engine_name:   str
    signal_name:   str
    assets:        tuple[str, ...]      # immuable — pas de liste

    # ── Versioning ────────────────────────────────────────────────────────────
    features_version: str
    labels_version:   str
    model_type:       str
    model_params:     dict[str, Any]

    # ── Périodes ──────────────────────────────────────────────────────────────
    # Format : {"start": "2021-01-01", "end": "2023-12-31"}
    train_period:      dict[str, str]
    validation_period: dict[str, str]
    test_period:       dict[str, str]

    # ── Configuration ─────────────────────────────────────────────────────────
    walk_forward_config: dict[str, Any]
    cost_config:         dict[str, Any]
    risk_config:         dict[str, Any]

    # ── Résultats ─────────────────────────────────────────────────────────────
    metrics:          dict[str, float]   # {"pf": 1.35, "sharpe": 1.2, ...}
    robustness_tests: dict[str, Any]     # {"shuffle": {...}, "cost_x2": {...}}

    # ── Décision ──────────────────────────────────────────────────────────────
    decision: Verdict
    notes:    str

    # ── Artifacts ─────────────────────────────────────────────────────────────
    artifact_paths: dict[str, str]
    code_hash:      str = ""              # sha256[:16] du code — "" si non calculé

    # ── Validation ────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        errors: list[str] = []

        for fname, fval in (
            ("experiment_id", self.experiment_id),
            ("engine_name",   self.engine_name),
            ("signal_name",   self.signal_name),
            ("run_id",        self.run_id),
            ("model_type",    self.model_type),
        ):
            if not fval or not str(fval).strip():
                errors.append(f"{fname} ne peut pas être vide")

        if not self.assets:
            errors.append("assets ne peut pas être vide")

        if errors:
            raise ValueError(
                "ExperimentRecord invalide :\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def with_decision(
        self,
        decision: Verdict,
        *,
        metrics: dict[str, float] | None = None,
        robustness_tests: dict[str, Any] | None = None,
        notes: str = "",
        artifact_paths: dict[str, str] | None = None,
        code_hash: str = "",
    ) -> Self:
        """
        Retourne une copie finalisée avec decision, métriques et artifacts.
        Utiliser à la fin du run pour ne pas muter le record initial.
        """
        d = self.to_dict()
        d["decision"]        = str(decision)
        d["metrics"]         = metrics or {}
        d["robustness_tests"] = robustness_tests or {}
        d["notes"]           = notes or self.notes
        d["artifact_paths"]  = artifact_paths or {}
        d["code_hash"]       = code_hash
        return self.from_dict(d)

    def is_final(self) -> bool:
        """True si la décision a été rendue (pas PENDING)."""
        return self.decision != Verdict.PENDING

    def compute_code_hash(self, source_code: str) -> str:
        """sha256[:16] du code source (pour reproductibilité)."""
        return hashlib.sha256(source_code.encode()).hexdigest()[:16]

    # ── Sérialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "_record_version":   _RECORD_VERSION,
            "experiment_id":     self.experiment_id,
            "run_id":            self.run_id,
            "timestamp":         self.timestamp.isoformat(),
            "engine_name":       self.engine_name,
            "signal_name":       self.signal_name,
            "assets":            list(self.assets),
            "features_version":  self.features_version,
            "labels_version":    self.labels_version,
            "model_type":        self.model_type,
            "model_params":      self.model_params,
            "train_period":      self.train_period,
            "validation_period": self.validation_period,
            "test_period":       self.test_period,
            "walk_forward_config": self.walk_forward_config,
            "cost_config":       self.cost_config,
            "risk_config":       self.risk_config,
            "metrics":           self.metrics,
            "robustness_tests":  self.robustness_tests,
            "decision":          str(self.decision),
            "notes":             self.notes,
            "artifact_paths":    self.artifact_paths,
            "code_hash":         self.code_hash,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            experiment_id=str(data["experiment_id"]),
            run_id=str(data["run_id"]),
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
            engine_name=str(data["engine_name"]),
            signal_name=str(data["signal_name"]),
            assets=tuple(str(a) for a in data["assets"]),
            features_version=str(data["features_version"]),
            labels_version=str(data["labels_version"]),
            model_type=str(data["model_type"]),
            model_params=dict(data.get("model_params") or {}),
            train_period=dict(data.get("train_period") or {}),
            validation_period=dict(data.get("validation_period") or {}),
            test_period=dict(data.get("test_period") or {}),
            walk_forward_config=dict(data.get("walk_forward_config") or {}),
            cost_config=dict(data.get("cost_config") or {}),
            risk_config=dict(data.get("risk_config") or {}),
            metrics={k: float(v) for k, v in (data.get("metrics") or {}).items()},
            robustness_tests=dict(data.get("robustness_tests") or {}),
            decision=Verdict(str(data["decision"])),
            notes=str(data.get("notes") or ""),
            artifact_paths=dict(data.get("artifact_paths") or {}),
            code_hash=str(data.get("code_hash") or ""),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(json.loads(raw))

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════════
# ExperimentLogger
# ══════════════════════════════════════════════════════════════════════════════


def _new_run_id(prefix: str = "exp") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{prefix}_{ts}_{uid}"


class ExperimentLogger:
    """
    Registre persistant des expériences (JSONL).

    Chaque expérience est une ligne JSON dans le fichier registry.jsonl.
    Les records individuels sont sauvegardés dans {registry_dir}/{run_id}.json.

    Usage :
        logger = ExperimentLogger(registry_dir=Path("artifacts/experiments"))

        run_id = logger.start(
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name="trend_following_v1",
            assets=("BTCUSDT", "ETHUSDT"),
            ...
        )

        # ... training ...

        logger.finish(
            run_id=run_id,
            metrics={"pf": 1.35, "sharpe": 1.2},
            robustness_tests={"shuffle": {"pf": 0.98}},
            decision=Verdict.PAPER,
        )
    """

    def __init__(self, registry_dir: Path) -> None:
        self._dir = Path(registry_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._registry_file = self._dir / "registry.jsonl"

    @property
    def registry_dir(self) -> Path:
        return self._dir

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(
        self,
        *,
        engine_name: str,
        signal_name: str,
        assets: tuple[str, ...],
        features_version: str = "unknown",
        labels_version: str = "unknown",
        model_type: str = "unknown",
        model_params: dict[str, Any] | None = None,
        train_period: dict[str, str] | None = None,
        validation_period: dict[str, str] | None = None,
        test_period: dict[str, str] | None = None,
        walk_forward_config: dict[str, Any] | None = None,
        cost_config: dict[str, Any] | None = None,
        risk_config: dict[str, Any] | None = None,
        notes: str = "",
    ) -> str:
        """
        Démarre un run — crée et persiste un ExperimentRecord initial (PENDING).
        Retourne le run_id.
        """
        run_id = _new_run_id(prefix=engine_name[:3].lower())
        record = ExperimentRecord(
            experiment_id=run_id,
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
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
            decision=Verdict.PENDING,
            notes=notes,
            artifact_paths={},
        )
        self._persist(record)
        return run_id

    def finish(
        self,
        run_id: str,
        *,
        decision: Verdict,
        metrics: dict[str, float] | None = None,
        robustness_tests: dict[str, Any] | None = None,
        notes: str = "",
        artifact_paths: dict[str, str] | None = None,
        code_hash: str = "",
    ) -> ExperimentRecord:
        """
        Finalise un run — met à jour le record avec la décision et les métriques.
        Retourne le record finalisé.
        """
        record = self.load(run_id)
        final = record.with_decision(
            decision=decision,
            metrics=metrics,
            robustness_tests=robustness_tests,
            notes=notes,
            artifact_paths=artifact_paths,
            code_hash=code_hash,
        )
        self._persist(final)
        return final

    # ── Lecture ───────────────────────────────────────────────────────────────

    def load(self, run_id: str) -> ExperimentRecord:
        record_path = self._dir / f"{run_id}.json"
        if not record_path.exists():
            raise FileNotFoundError(
                f"ExperimentLogger : run {run_id!r} introuvable dans {self._dir}"
            )
        return ExperimentRecord.load(record_path)

    def list_all(self) -> list[dict[str, Any]]:
        """Retourne tous les records du registre (résumés, pas les records complets)."""
        if not self._registry_file.exists():
            return []
        result = []
        for line in self._registry_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return result

    def list_by_decision(self, decision: Verdict) -> list[dict[str, Any]]:
        return [r for r in self.list_all() if r.get("decision") == str(decision)]

    def list_by_engine(self, engine_name: str) -> list[dict[str, Any]]:
        return [r for r in self.list_all() if r.get("engine_name") == engine_name]

    # ── Persistance ───────────────────────────────────────────────────────────

    def _persist(self, record: ExperimentRecord) -> None:
        """Sauvegarde le record individuellement + append dans le registre."""
        record.save(self._dir / f"{record.run_id}.json")
        self._append_registry(record)

    def _append_registry(self, record: ExperimentRecord) -> None:
        """Ajoute une entrée résumée dans le fichier JSONL."""
        summary = {
            "run_id":       record.run_id,
            "timestamp":    record.timestamp.isoformat(),
            "engine_name":  record.engine_name,
            "signal_name":  record.signal_name,
            "assets":       list(record.assets),
            "model_type":   record.model_type,
            "decision":     str(record.decision),
            "metrics_summary": {
                k: round(v, 4)
                for k, v in record.metrics.items()
                if k in {"pf", "sharpe", "cagr", "auc_ovr", "hit_rate"}
            },
        }
        with open(self._registry_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, separators=(",", ":"), default=str) + "\n")
