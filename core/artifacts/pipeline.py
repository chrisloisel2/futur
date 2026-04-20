from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from core.settings import get_settings


CANONICAL_LAYOUT_VERSION = 2
DEFAULT_RUNS_DIR = get_settings().paths.pipeline_runs_dir


@dataclass(frozen=True)
class ComponentFiles:
    directory: Optional[Path]
    model: Optional[Path] = None
    scaler: Optional[Path] = None
    metadata: Optional[Path] = None
    calibrator: Optional[Path] = None


def load_json(path: Path, required: bool = True) -> Optional[dict]:
    if path.exists():
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    if required:
        raise FileNotFoundError(f"JSON requis introuvable : {path}")
    return None


def load_pickle(path: Path, required: bool = True) -> Any:
    if path.exists():
        with open(path, "rb") as handle:
            return pickle.load(handle)
    if required:
        raise FileNotFoundError(f"Artefact requis introuvable : {path}")
    return None


def normalize_runs_root(path: Path) -> Path:
    path = Path(path)
    if path == Path("."):
        return DEFAULT_RUNS_DIR
    if (path / "runs" / "pipeline").exists():
        return path / "runs" / "pipeline"
    if path.name == "runs" and (path / "pipeline").exists():
        return path / "pipeline"
    return path


def load_pipeline_manifest(run_dir: Path) -> dict:
    return load_json(Path(run_dir) / "manifest.json", required=False) or {}


def load_pipeline_summary(run_dir: Path) -> dict:
    return load_json(Path(run_dir) / "pipeline_summary.json", required=False) or {}


def load_pipeline_config(run_dir: Path) -> dict:
    config_doc = load_json(Path(run_dir) / "config.json", required=False) or {}
    if isinstance(config_doc.get("pipeline_config"), dict):
        return config_doc["pipeline_config"]
    if isinstance(config_doc.get("config"), dict):
        return config_doc["config"]
    if config_doc:
        return config_doc

    summary = load_pipeline_summary(run_dir)
    if isinstance(summary.get("config"), dict):
        return summary["config"]
    return {}


def component_enabled(metadata: Optional[dict], default: bool = True) -> bool:
    if not metadata:
        return default
    if "enabled_for_inference" in metadata:
        return bool(metadata["enabled_for_inference"])
    if "enabled" in metadata:
        return bool(metadata["enabled"])
    return default


def resolve_filter_thresholds(run_dir: Path, metadata: Optional[dict]) -> tuple[float, float]:
    cfg = load_pipeline_config(run_dir)
    meta = metadata or {}
    thr_long = meta.get(
        "threshold_long",
        meta.get(
            "recommended_threshold_long",
            meta.get("calibrated_threshold_long", cfg.get("filter_threshold_long", 0.40)),
        ),
    )
    thr_short = meta.get(
        "threshold_short",
        meta.get(
            "recommended_threshold_short",
            meta.get("calibrated_threshold_short", cfg.get("filter_threshold_short", 0.45)),
        ),
    )
    return float(thr_long), float(thr_short)


def resolve_edge_threshold(
    run_dir: Path,
    side: str,
    metadata: Optional[dict],
    default: float,
) -> float:
    cfg = load_pipeline_config(run_dir)
    meta = metadata or {}
    return float(
        meta.get(
            "threshold",
            meta.get(
                "recommended_threshold",
                meta.get(
                    f"direction_threshold_{side}",
                    cfg.get(f"direction_threshold_{side}", default),
                ),
            ),
        )
    )


def resolve_regime_threshold(metadata: Optional[dict], default: float = 0.70) -> float:
    meta = metadata or {}
    return float(meta.get("threshold", meta.get("activation_threshold", default)))


def resolve_filter_component(run_dir: Path) -> ComponentFiles:
    filter_dir = Path(run_dir) / "filter"
    return ComponentFiles(
        directory=filter_dir if filter_dir.exists() else None,
        model=_first_existing([filter_dir / "model.pkl", filter_dir / "filter_model.pkl"]),
        scaler=_first_existing([filter_dir / "scaler.pkl", filter_dir / "filter_scaler.pkl"]),
        metadata=_first_existing([filter_dir / "metadata.json", filter_dir / "metrics.json"]),
    )


def resolve_edge_component(run_dir: Path, side: str) -> ComponentFiles:
    canonical_dir = Path(run_dir) / f"edge_{side}"
    legacy_dir = Path(run_dir) / side
    directory = canonical_dir if canonical_dir.exists() else legacy_dir if legacy_dir.exists() else None
    return ComponentFiles(
        directory=directory,
        model=_first_existing([
            canonical_dir / "model.pkl",
            canonical_dir / "best_model.pkl",
            legacy_dir / "model.pkl",
            legacy_dir / "best_model.pkl",
        ]),
        scaler=_first_existing([
            canonical_dir / "scaler.pkl",
            legacy_dir / "scaler.pkl",
        ]),
        metadata=_first_existing([
            canonical_dir / "metadata.json",
            canonical_dir / "calibration_metrics.json",
            canonical_dir / "metrics.json",
            legacy_dir / "metadata.json",
            legacy_dir / "calibration_metrics.json",
            legacy_dir / "metrics.json",
        ]),
        calibrator=_first_existing([
            canonical_dir / "calibrator.pkl",
            legacy_dir / "calibrator.pkl",
        ]),
    )


def resolve_regime_component(run_dir: Path) -> ComponentFiles:
    regime_dir = Path(run_dir) / "regime"
    return ComponentFiles(
        directory=regime_dir if regime_dir.exists() else None,
        model=_first_existing([regime_dir / "model.pkl", regime_dir / "bear_regime_model.pkl"]),
        scaler=_first_existing([regime_dir / "scaler.pkl", regime_dir / "bear_regime_scaler.pkl"]),
        metadata=_first_existing([regime_dir / "metadata.json", regime_dir / "bear_regime_metrics.json"]),
    )


def is_valid_pipeline_run(run_dir: Path) -> bool:
    filter_component = resolve_filter_component(run_dir)
    long_component = resolve_edge_component(run_dir, "long")
    short_component = resolve_edge_component(run_dir, "short")
    return bool(filter_component.model and (long_component.model or short_component.model))


def find_latest_pipeline_run(path: Path) -> Optional[Path]:
    runs_root = normalize_runs_root(Path(path))
    if not runs_root.exists():
        return None

    for candidate in sorted(runs_root.iterdir(), reverse=True):
        if candidate.is_dir() and is_valid_pipeline_run(candidate):
            return candidate
    return None


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None
