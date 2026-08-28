from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

from core.settings import get_settings


@dataclass(frozen=True)
class TrainingProfile:
    key: str
    module_name: str
    strategy_slug: str
    zone: str
    timeframe: str
    horizon_minutes: int
    default_run_root: str
    aliases: tuple[str, ...]
    official_labels: tuple[str, ...] = ()

    @property
    def default_runs_dir(self) -> str:
        return str(get_settings().paths.run_profile_dir(self.key))


TRAINING_PROFILES = {
    "pipeline": TrainingProfile(
        key="pipeline",
        module_name="strategies.pipeline_hourly.profile",
        strategy_slug="pipeline_hourly",
        zone="prod",
        timeframe="1h",
        horizon_minutes=60,
        default_run_root="runs/pipeline",
        aliases=("pipeline", "prod", "core"),
        official_labels=("tradeable_net", "y_long", "y_short"),
    ),
    "1m": TrainingProfile(
        key="1m",
        module_name="strategies.pipeline_minute.profile",
        strategy_slug="pipeline_minute",
        zone="research",
        timeframe="1m",
        horizon_minutes=60,
        default_run_root="runs/research/pipeline_minute",
        aliases=("1m", "minute"),
    ),
}

_PROFILE_ALIASES = {
    alias: profile.key
    for profile in TRAINING_PROFILES.values()
    for alias in profile.aliases
}


def get_training_profile(name: str) -> TrainingProfile:
    normalized = _PROFILE_ALIASES.get(name, name)
    try:
        return TRAINING_PROFILES[normalized]
    except KeyError as exc:
        raise KeyError(f"Profil d'entraînement inconnu: {name!r}") from exc


def resolve_profile_and_args(argv: Iterable[str]) -> Tuple[str, list[str]]:
    args = list(argv)
    if not args:
        return "pipeline", []

    first = args[0]
    if first in ("-h", "--help", "help"):
        return "__help__", []

    if first.startswith("-"):
        return "pipeline", args

    profile = _PROFILE_ALIASES.get(first)
    if profile is not None:
        return profile, args[1:]

    return "pipeline", args


def format_train_help() -> str:
    lines = [
        "train.py — Point d'entrée unique du training",
        "",
        "Usage canonique :",
        "    python train.py pipeline ...",
        "    python train.py 1m ...",
        "    python train.py local ...",
        "",
        "Profils disponibles :",
    ]
    for profile in TRAINING_PROFILES.values():
        labels = ", ".join(profile.official_labels) if profile.official_labels else "n/a"
        lines.append(
            f"    {profile.key:<8} : strategy={profile.strategy_slug} zone={profile.zone} "
            f"timeframe={profile.timeframe} horizon={profile.horizon_minutes}m "
            f"runs={profile.default_run_root} labels={labels}"
        )
    return "\n".join(lines)
