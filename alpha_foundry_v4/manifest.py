from __future__ import annotations

from typing import Dict

from .registry import LAB_REGISTRY


def foundry_manifest() -> Dict[str, object]:
    labs = []
    for lab_id in sorted(LAB_REGISTRY):
        spec = LAB_REGISTRY[lab_id]
        labs.append({"lab_id": spec.lab_id, "name": spec.name, "hypothesis": spec.hypothesis, "payer": spec.payer, "domains": [domain.value for domain in spec.domains], "targets": list(spec.targets), "horizons_ms": list(spec.horizons_ms), "model_families": [model.value for model in spec.model_families], "execution_styles": [style.value for style in spec.execution_styles], "independence_key": spec.independence_key})
    return {"version": "alpha-foundry-v4", "lab_count": len(labs), "independence_key_count": len({lab["independence_key"] for lab in labs}), "labs": labs}
