#!/usr/bin/env python3
"""
scripts/report_liquidation_event_inventory.py
─────────────────────────────────────────────────────────────────────────────
Inventaire hebdo des events liquidation + verdict de readiness pour un moteur
offensif. Pas de modèle tant que les seuils ne sont pas atteints.

Seuils :
  DATA_NOT_READY        : < 100 events
  EVENT_DIAGNOSTIC_READY: ≥100 events, ≥30 significatifs, ≥3 actifs, ≥30 j
  ENGINE_TRAINING_READY : ≥300 events, ≥100 significatifs, ≥60 j, BTC/ETH/SOL

    python3 scripts/report_liquidation_event_inventory.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.events.live_event_builder import build_events

OUT = ROOT / "reports"


def main() -> None:
    ev = build_events()
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    L = [f"# Liquidation Event Inventory — {date}\n"]

    if ev.empty:
        L += ["**0 event collecté.** Le flux forceOrder est événementiel (rien en marché calme).",
              "Le collecteur `futur-derivatives` doit tourner en continu (systemd) pour accumuler.",
              "\n**Verdict : DATA_NOT_READY** (aucun moteur ; pipeline prêt à se remplir)."]
        (OUT / f"LIQUIDATION_INVENTORY_{date}.md").write_text("\n".join(L))
        print("\n".join(L)); return

    n = len(ev); n_sig = int(ev["significant"].sum())
    n_assets = ev["symbol"].nunique()
    span_days = (ev["event_time"].max() - ev["event_time"].min()).days
    assets = set(ev["symbol"].unique())
    by_sym = ev.groupby("symbol").agg(n=("event_id", "size"), usd=("total_usd", "sum")).round(0)
    by_side = ev["liquidation_side"].value_counts().to_dict()

    L += [f"- events: {n}  | significatifs (≥250k$): {n_sig}  | actifs: {n_assets}  | span: {span_days} j",
          f"- par side: {by_side}",
          f"- avec label forward: {int(ev['label_available'].sum())}\n",
          "## Par actif", by_sym.to_string(), ""]
    lab = ev[ev["label_available"] == 1]
    if len(lab):
        L.append("## Rendement forward moyen (events labellisés)")
        for side, gg in lab.groupby("liquidation_side"):
            L.append(f"- {side}: n={len(gg)}  fwd_1h={gg['forward_return_1h'].mean()*100:+.2f}%  "
                     f"fwd_4h={gg['forward_return_4h'].mean()*100:+.2f}%  MAE_4h={gg['MAE_4h'].mean()*100:.2f}%")

    diag = (n >= 100 and n_sig >= 30 and n_assets >= 3 and span_days >= 30)
    train = (n >= 300 and n_sig >= 100 and span_days >= 60 and {"BTCUSDT", "ETHUSDT", "SOLUSDT"} <= assets)
    verdict = "ENGINE_TRAINING_READY" if train else ("EVENT_DIAGNOSTIC_READY" if diag else "DATA_NOT_READY")
    L.append(f"\n**Verdict : {verdict}**")
    if verdict == "DATA_NOT_READY":
        L.append("(continuer l'accumulation ; pas de modèle avant ≥100 events)")

    (OUT / f"LIQUIDATION_INVENTORY_{date}.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
