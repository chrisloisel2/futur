#!/usr/bin/env python3
"""
scripts/validate_cascade_proxy_vs_real.py
─────────────────────────────────────────────────────────────────────────────
Valide le PROXY cascade (OI 5-min Vision) contre les VRAIES liquidations
collectées (Bybit WS + OKX REST, depuis 2026-07-04).

Pour chaque cluster réel significatif (≥ SIGNIFICANT_USD) : existe-t-il un
event proxy même symbole dans ±TOL minutes ? (recall) — et inversement pour
la précision. La fenêtre de recouvrement s'épaissit chaque jour de collecte ;
relancer régulièrement. Verdict indicatif tant que le recouvrement < 7 jours.

Sortie : reports/liq_cascade/PROXY_VS_REAL.json (+ stdout)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.engines.liq_cascade.dataset import build_event_dataset
from src.institutional.engines.liq_cascade.detector import METRICS_DIR, CascadeConfig

EVENTS_REAL = ROOT / "data" / "events" / "liquidation" / "events.parquet"
OUT = ROOT / "reports" / "liq_cascade" / "PROXY_VS_REAL.json"
SIGNIFICANT_USD = 250_000
TOL = pd.Timedelta(minutes=30)


def main():
    if not EVENTS_REAL.exists():
        print("Pas d'events réels — lancer build_live_liquidation_events.py d'abord")
        sys.exit(1)
    real = pd.read_parquet(EVENTS_REAL)
    real["event_time"] = pd.to_datetime(real["event_time"], utc=True)

    symbols = sorted(p.stem.replace("_metrics_5m", "")
                     for p in METRICS_DIR.glob("*_metrics_5m.parquet"))
    proxy = build_event_dataset(symbols, CascadeConfig())
    if proxy.empty:
        print("Pas d'events proxy")
        sys.exit(1)

    lo = max(real["event_time"].min(), proxy["event_time"].min())
    hi = min(real["event_time"].max(), proxy["event_time"].max())
    overlap_h = max((hi - lo).total_seconds() / 3600, 0)
    real_o = real[(real["event_time"] >= lo) & (real["event_time"] <= hi)
                  & real["symbol"].isin(symbols)]
    proxy_o = proxy[(proxy["event_time"] >= lo) & (proxy["event_time"] <= hi)]
    sig = real_o[real_o["total_usd"] >= SIGNIFICANT_USD]

    def matched(a: pd.DataFrame, b: pd.DataFrame) -> int:
        m = 0
        for _, r in a.iterrows():
            cand = b[(b["symbol"] == r["symbol"])
                     & ((b["event_time"] - r["event_time"]).abs() <= TOL)]
            m += int(len(cand) > 0)
        return m

    recall_sig = matched(sig, proxy_o) / len(sig) if len(sig) else None
    precision = matched(proxy_o, sig) / len(proxy_o) if len(proxy_o) else None

    res = {
        "overlap_hours": round(overlap_h, 1),
        "overlap_window": [str(lo), str(hi)],
        "real_clusters": int(len(real_o)), "real_significant": int(len(sig)),
        "proxy_events": int(len(proxy_o)),
        "recall_significant": None if recall_sig is None else round(recall_sig, 3),
        "precision_proxy": None if precision is None else round(precision, 3),
        "verdict": ("INDICATIVE_ONLY_OVERLAP_LT_7D" if overlap_h < 168
                    else "VALID_WINDOW"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
