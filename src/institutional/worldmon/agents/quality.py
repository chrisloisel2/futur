"""
Agent QUALITY — le cœur « scientifiquement respectable ». Contrôle qualité +
provenance + détection d'anomalies AVANT tout usage de signal :
  • schéma (champs requis, types) ;
  • fraîcheur (retard de la donnée la plus récente par source) ;
  • duplication résiduelle (taux de content_hash uniques) ;
  • couverture (sources actives / attendues) ;
  • anomalies de volume (z-score du compte quotidien vs 30j).
Émet un quality_report ; un GATE booléen data_trustworthy conditionne le
correlator (pas de corrélation sur données douteuses).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

import numpy as np

from src.institutional.worldmon.bigdata_store import BigDataStore

REQUIRED = {"ts", "source", "kind"}
EXPECTED_SOURCES = {"gdelt:gdelt_vol", "gdelt:gdelt_tone", "usgs",
                    "coingecko:global"}
MAX_STALENESS_H = {"gdelt:gdelt_vol": 48, "coingecko:global": 6, "usgs": 72}


class Quality:
    name = "quality"

    def __init__(self, store: BigDataStore):
        self.store = store

    def run(self) -> Dict:
        try:
            df = self.store.events_df(since_days=90)
        except Exception as e:
            return {"agent": self.name, "ok": False, "error": str(e)}
        if df.empty:
            rep = {"agent": self.name, "ok": True, "data_trustworthy": False,
                   "reason": "0 événement"}
            self.store.write_doc("quality_reports", dict(rep))
            return rep

        now = datetime.now(timezone.utc)
        # schéma
        schema_ok = REQUIRED.issubset(set(df.columns))
        # dédup résiduel
        hashes = df.get("content_hash")
        dup_free = (float(hashes.nunique() / len(hashes)) if hashes is not None
                    and len(hashes) else 1.0)
        # fraîcheur par source
        stale = {}
        for src, g in df.groupby("source"):
            age_h = (now - g["ts"].max()).total_seconds() / 3600
            limit = MAX_STALENESS_H.get(src, 168)
            stale[src] = {"age_h": round(age_h, 1), "fresh": age_h <= limit}
        # couverture
        active = set(df["source"].unique())
        coverage = len(EXPECTED_SOURCES & active) / len(EXPECTED_SOURCES)
        # anomalie de volume (z du compte quotidien)
        daily = df.set_index("ts").resample("D").size()
        z = 0.0
        if len(daily) >= 10:
            z = float((daily.iloc[-1] - daily[:-1].mean()) / (daily[:-1].std() + 1e-9))

        trustworthy = (schema_ok and dup_free > 0.98 and coverage >= 0.5
                       and abs(z) < 6)
        rep = {"agent": self.name, "ok": True,
               "data_trustworthy": bool(trustworthy),
               "schema_ok": bool(schema_ok), "dedup_ratio": round(dup_free, 4),
               "source_coverage": round(coverage, 3),
               "volume_z_today": round(z, 2),
               "staleness": stale, "n_events_90d": int(len(df)),
               "generated_at": now.isoformat()}
        self.store.write_doc("quality_reports", dict(rep))
        return rep
