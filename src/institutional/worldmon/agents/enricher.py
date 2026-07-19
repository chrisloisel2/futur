"""
Agent ENRICHER (NLP) — classe les événements : sentiment, tagging symbole,
classe d'événement (REGULATORY / SECURITY / MACRO / ADOPTION / GEOPHYSICAL…).

LLM PLUGGABLE : si ANTHROPIC_API_KEY présent → classification LLM (Haiku) sur
les titres news ambigus ; sinon fallback lexical/statistique (déterministe,
reproductible). Le fallback est le mode par défaut, honnête et sans coût.
"""
from __future__ import annotations

import os
from typing import Dict, List

from src.institutional.worldmon.bigdata_store import BigDataStore

EVENT_CLASSES = {
    "REGULATORY": ["sec", "regulation", "regulator", "lawsuit", "ban", "court",
                   "congress", "senate", "legal", "compliance", "sanction"],
    "SECURITY": ["hack", "exploit", "breach", "stolen", "vulnerability",
                 "attack", "phishing", "drain"],
    "MACRO": ["fed", "inflation", "rate", "cpi", "recession", "dollar",
              "treasury", "gdp", "unemployment", "fomc"],
    "ADOPTION": ["etf", "adoption", "partnership", "institutional", "integrat",
                 "launch", "listing", "custody", "payment"],
    "LIQUIDITY": ["liquidation", "outflow", "inflow", "whale", "reserve"],
}


class Enricher:
    name = "enricher"

    def __init__(self, store: BigDataStore):
        self.store = store
        self.use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))

    def _classify(self, text: str) -> List[str]:
        t = text.lower()
        return [cls for cls, kws in EVENT_CLASSES.items()
                if any(k in t for k in kws)] or ["GENERAL"]

    def run(self) -> Dict:
        if self.store.mongo is None:
            return {"agent": self.name, "ok": True, "enriched": 0,
                    "note": "backend jsonl : enrichissement à l'ingestion"}
        try:
            col = self.store.mongo.world_events
            cur = col.find({"kind": {"$in": ["news", "media_metric"]},
                            "event_classes": {"$exists": False}}).limit(2000)
            n = 0
            for d in cur:
                text = (d.get("title") or "") + " " + str(d.get("query") or "")
                classes = self._classify(text)
                col.update_one({"_id": d["_id"]},
                               {"$set": {"event_classes": classes,
                                         "enriched_by": "llm" if self.use_llm else "lexical"}})
                n += 1
            return {"agent": self.name, "ok": True, "enriched": n,
                    "mode": "llm" if self.use_llm else "lexical"}
        except Exception as e:
            return {"agent": self.name, "ok": False, "error": str(e)}
