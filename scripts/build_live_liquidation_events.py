#!/usr/bin/env python3
"""
scripts/build_live_liquidation_events.py
─────────────────────────────────────────────────────────────────────────────
Construit l'event lake liquidation depuis les liquidations collectées (forceOrder).
À lancer périodiquement (idéalement quotidien) pour cataloguer les events + labels.

    python3 scripts/build_live_liquidation_events.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from src.institutional.events.live_event_builder import build_events

OUT = Path("data/events/liquidation/events.parquet")


def main() -> None:
    ev = build_events()
    if ev.empty:
        print("0 liquidation collectée pour l'instant (forceOrder est événementiel — "
              "déployer futur-derivatives en continu). Pipeline prêt à se remplir.")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ev.to_parquet(OUT, index=False)
    print(f"Event lake : {len(ev)} events → {OUT}  "
          f"(significatifs: {int(ev['significant'].sum())}, avec label: {int(ev['label_available'].sum())})")


if __name__ == "__main__":
    main()
