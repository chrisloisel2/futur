"""
src/alpha20/costs/borrow_registry.py — coût d'emprunt RÉEL daté (étape 2).

Le 8 %/an actuel est une hypothèse : ici chaque taux est un snapshot daté par
(venue, asset). Sans snapshot réel (API margin signée ou relevé), on sert le
défaut assumed étiqueté — le simulateur et le governor peuvent stresser ×4
(scénario borrow_x4) depuis n'importe quelle source.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from src.alpha20 import ROOT, load_config

SNAP_DIR = ROOT / "data" / "alpha20" / "cost_snapshots"


def save_borrow(venue: str, asset: str, rate_ann: float, source: str) -> Path:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    p = SNAP_DIR / f"borrow_{venue}_{asset}.json"
    hist = json.loads(p.read_text()) if p.exists() else []
    hist.append({"venue": venue, "asset": asset, "rate_ann": rate_ann,
                 "as_of": datetime.now(timezone.utc).date().isoformat(),
                 "source": source})
    p.write_text(json.dumps(hist, indent=1))
    return p


def effective_borrow(venue: str, asset: str) -> Dict:
    p = SNAP_DIR / f"borrow_{venue}_{asset}.json"
    if p.exists():
        hist = json.loads(p.read_text())
        if hist:
            return sorted(hist, key=lambda s: s["as_of"])[-1]
    d = load_config()["costs"]["assumed_defaults"]["borrow_ann"]
    return {"venue": venue, "asset": asset, "rate_ann": float(d["rate"]),
            "as_of": str(d["as_of"]), "source": "assumed"}
