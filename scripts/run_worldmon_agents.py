#!/usr/bin/env python3
"""
scripts/run_worldmon_agents.py
─────────────────────────────────────────────────────────────────────────────
Orchestrateur du World Monitor : exécute la chaîne d'agents autonomes une fois
(timer systemd). Chaque agent s'isole ; le superviseur écrit le heartbeat.

  Ingestor → Enricher → Quality → Correlator → Supervisor

  python3 scripts/run_worldmon_agents.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.institutional.worldmon.bigdata_store import BigDataStore
from src.institutional.worldmon.agents.ingestor import Ingestor
from src.institutional.worldmon.agents.enricher import Enricher
from src.institutional.worldmon.agents.quality import Quality
from src.institutional.worldmon.agents.correlator import Correlator
from src.institutional.worldmon.agents.supervisor import Supervisor


def main():
    t0 = time.time()
    store = BigDataStore()
    print(f"[worldmon] backend = {store.backend}", flush=True)
    reports = []
    for Agent, kw in ((Ingestor, {}), (Enricher, {}), (Quality, {}),
                      (Correlator, {})):
        a = Agent(store)
        r = a.run(**kw)
        reports.append(r)
        print(f"  [{r['agent']}] {json.dumps({k: v for k, v in r.items() if k != 'agent'}, default=str)[:220]}",
              flush=True)
    health = Supervisor(store).run(reports)
    print(f"[worldmon] pipeline_healthy={health['pipeline_healthy']} "
          f"events={health['total_events']} runtime={time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
