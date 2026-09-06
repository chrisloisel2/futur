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
    data_dir: Path
    runs_dir: Path
    pipeline_runs_dir: Path
    research_runs_dir: Path
    pipeline_1m_runs_dir: Path
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
        data_dir=project_root / "data",
        runs_dir=project_root / "runs",
        pipeline_runs_dir=project_root / "runs" / "pipeline",
        research_runs_dir=project_root / "runs" / "research",
        pipeline_1m_runs_dir=project_root / "runs" / "research" / "pipeline_minute",
        tests_dir=project_root / "tests",
    )
    services = ServiceSettings(
        mongo_uri=os.getenv("FUTUR_MONGO_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017")),
        mongo_db=os.getenv("FUTUR_MONGO_DB", os.getenv("MONGODB_DB", os.getenv("MONGO_DB", "trader"))),
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
    root_str   = str(settings.paths.project_root)

    extra_list: list[str] = []
    if extra_paths:
        extra_list = [str(settings.paths.resolve(p)) for p in extra_paths]

    # Extras vont EN FIN de sys.path (fallback seulement).
    for path_str in extra_list:
        if path_str not in sys.path:
            sys.path.append(path_str)

    # project_root TOUJOURS en première position — quel que soit PYTHONPATH.
    # Déplace project_root à l'index 0 si déjà présent, sinon insère.
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)

    return settings.paths.project_root
