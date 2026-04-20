from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class AppPaths:
    project_root: Path
    core_dir: Path
    strategies_dir: Path
    production_dir: Path
    research_dir: Path
    legacy_dir: Path
    config_dir: Path
    data_dir: Path
    runs_dir: Path
    pipeline_runs_dir: Path
    research_runs_dir: Path
    legacy_runs_dir: Path
    pipeline_1m_runs_dir: Path
    local_runs_dir: Path
    tests_dir: Path

    def resolve(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return self.project_root / path

    def run_profile_dir(self, profile: str) -> Path:
        normalized = str(profile).strip().lower()
        if normalized in {"pipeline", "prod", "core"}:
            return self.pipeline_runs_dir
        if normalized in {"1m", "minute", "pipeline_minute", "research"}:
            return self.pipeline_1m_runs_dir
        if normalized in {"local", "legacy"}:
            return self.local_runs_dir
        raise KeyError(f"Profil de run inconnu: {profile!r}")


@dataclass(frozen=True)
class ServiceSettings:
    mongo_uri: str
    mongo_db: str
    s3_bucket: str
    s3_prefix: str
    binance_klines_url: str
    binance_api_key: str
    binance_api_secret: str


@dataclass(frozen=True)
class AppSettings:
    environment: str
    paths: AppPaths
    services: ServiceSettings


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    project_root = Path(__file__).resolve().parent.parent
    paths = AppPaths(
        project_root=project_root,
        core_dir=project_root / "core",
        strategies_dir=project_root / "strategies",
        production_dir=project_root / "production",
        research_dir=project_root / "research",
        legacy_dir=project_root / "legacy",
        config_dir=project_root / "legacy" / "config",
        data_dir=project_root / "data",
        runs_dir=project_root / "runs",
        pipeline_runs_dir=project_root / "runs" / "pipeline",
        research_runs_dir=project_root / "runs" / "research",
        legacy_runs_dir=project_root / "runs" / "legacy",
        pipeline_1m_runs_dir=project_root / "runs" / "research" / "pipeline_minute",
        local_runs_dir=project_root / "runs" / "legacy" / "local",
        tests_dir=project_root / "tests",
    )
    services = ServiceSettings(
        mongo_uri=os.getenv("FUTUR_MONGO_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017")),
        mongo_db=os.getenv("FUTUR_MONGO_DB", os.getenv("MONGO_DB", "market")),
        s3_bucket=os.getenv("FUTUR_S3_BUCKET", os.getenv("S3_BUCKET", "")),
        s3_prefix=os.getenv("FUTUR_S3_PREFIX", os.getenv("S3_PREFIX", "")),
        binance_klines_url=os.getenv(
            "FUTUR_BINANCE_KLINES_URL",
            os.getenv("BINANCE_KLINES_URL", "https://api.binance.com/api/v3/klines"),
        ),
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_api_secret=os.getenv("BINANCE_API_SECRET", ""),
    )
    return AppSettings(
        environment=os.getenv("FUTUR_ENV", os.getenv("APP_ENV", "dev")),
        paths=paths,
        services=services,
    )


def configure_project_imports(extra_paths: Optional[Iterable[str | Path]] = None) -> Path:
    settings = get_settings()
    base_paths = [
        settings.paths.project_root,
        settings.paths.legacy_dir,
        settings.paths.legacy_dir / "ai" / "models",
    ]

    if extra_paths:
        base_paths.extend(settings.paths.resolve(path) for path in extra_paths)

    for path in base_paths:
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    return settings.paths.project_root
