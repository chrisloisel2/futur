from __future__ import annotations

from typing import Sequence, Tuple

from .contracts import HypothesisSpec
from .labs.catalog import LABS


def hypothesis_grid(lab_id: str, feature_set_id: str, target_name: str = None, horizons_ms: Sequence[int] = None, confirmation_min_hours: float = 24.0) -> Tuple[HypothesisSpec, ...]:
    spec = LABS[lab_id]
    target = target_name or spec.default_target
    horizons = tuple(int(x) for x in (horizons_ms or spec.horizons_ms))
    rows = []
    for horizon in horizons:
        family_id = "%s:%s:%s" % (lab_id, spec.economic_source_id, target)
        hypothesis_id = "%s:%s:%sms" % (lab_id, target, horizon)
        rows.append(HypothesisSpec(
            hypothesis_id=hypothesis_id,
            family_id=family_id,
            lab_id=lab_id,
            economic_source_id=spec.economic_source_id,
            mechanism=spec.hypothesis_template,
            payer=spec.payer,
            domains=spec.domains,
            target_name=target,
            horizon_ms=int(horizon),
            feature_set_id=str(feature_set_id),
            model_family="ridge_baseline",
            execution_style=spec.execution_styles[0],
            max_trials=int(spec.max_trials_per_family),
            max_lookback_ms=max(1000, int(horizon)),
            confirmation_min_hours=float(confirmation_min_hours),
        ))
    return tuple(rows)
