from __future__ import annotations

from typing import Dict


class DecisionLogLinker:
    def build_manifest(self, incident_id: str, inputs: Dict, outputs: Dict) -> Dict:
        return {
            "incident_id": incident_id,
            "inputs": inputs,
            "outputs": outputs,
        }
