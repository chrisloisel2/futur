"""
Agent SUPERVISOR — santé de la chaîne : agrège les statuts des agents, écrit un
heartbeat (agent_health) lu par le Command Center. Ne juge pas la donnée
(c'est Quality) ; il juge le PIPELINE (chaque agent a-t-il tourné/réussi).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from src.institutional.worldmon.bigdata_store import BigDataStore


class Supervisor:
    name = "supervisor"

    def __init__(self, store: BigDataStore):
        self.store = store

    def run(self, agent_reports: List[Dict]) -> Dict:
        ok = all(r.get("ok") for r in agent_reports)
        health = {
            "agent": self.name, "ok": ok,
            "pipeline_healthy": ok,
            "backend": self.store.backend,
            "total_events": self.store.count(),
            "agents": {r["agent"]: {"ok": r.get("ok"),
                                    "detail": {k: v for k, v in r.items()
                                               if k not in ("agent", "ok")}}
                       for r in agent_reports},
            "heartbeat": datetime.now(timezone.utc).isoformat(),
        }
        self.store.write_doc("agent_health", dict(health))
        return health
