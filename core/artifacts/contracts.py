from __future__ import annotations

from typing import Mapping


CANONICAL_SCHEMA_VERSION = 2
CANONICAL_PIPELINE_LAYOUT = "pipeline_canonical_v1"
CANONICAL_PIPELINE_LABELS = ("tradeable_net", "y_long", "y_short")


def validate_pipeline_label_stats(stats: Mapping[str, object]) -> None:
    if not isinstance(stats, Mapping):
        raise TypeError("Les statistiques de labels doivent être un mapping JSON-sérialisable.")

    required_keys = {
        "n_total",
        "n_tradeable",
        "n_long",
        "n_short",
        "thr_long",
        "thr_short_with_cost",
    }
    missing = sorted(key for key in required_keys if key not in stats)
    if missing:
        raise ValueError(
            "labels.json ne respecte pas le contrat canonique: "
            f"clés manquantes {missing}"
        )

