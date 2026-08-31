#!/usr/bin/env python3
"""
scripts/compute_live_alpha_lab_scoreboard.py
─────────────────────────────────────────────────────────────────────────────
Scoreboard Live Alpha Lab — lit configs/live_alpha_registry.yaml +
reports/live_alpha_lab/*/decisions.parquet (colonne `provenance`, voir
scripts/apply_provenance_tags.py) et écrit un scoreboard qui NE CONFOND
JAMAIS "le programme tourne" (operational_status) avec "l'alpha est
confirmé" (scientific_status), et sépare explicitement replay/forward.

Exécuter apply_provenance_tags.py AVANT ce script si des runners ont tourné
depuis le dernier calcul.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
LAB_DIR = ROOT / "reports" / "live_alpha_lab"
OUT_MD = LAB_DIR / "SCOREBOARD.md"


def load_decisions(alpha_id: str):
    p = LAB_DIR / alpha_id / "decisions.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


def row_for(entry: dict) -> dict:
    alpha_id = entry["alpha_id"]
    df = load_decisions(alpha_id)
    replay = forward = 0
    if df is not None and "provenance" in df.columns:
        vc = df["provenance"].value_counts()
        replay = int(vc.get("REPLAY", 0))
        forward = int(vc.get("FORWARD_LIVE", 0))
    elif df is not None:
        replay = len(df)   # pas encore tagué -- traité comme tout-replay par prudence (fail closed)
    return {
        "alpha_id": alpha_id,
        "family": entry.get("family"),
        "scientific_status": entry.get("scientific_status", "?"),
        "operational_status": entry.get("operational_status", "?"),
        "freeze_timestamp": entry.get("freeze_timestamp"),
        "replay_decisions": replay,
        "forward_decisions": forward,
        # Mode A pur partout à ce stade -> pas de fills simulés -> pas de "trades" réels.
        "forward_trades": 0,
        "forward_independent_episodes": None,   # nécessite un decluster par famille, pas encore calculé ici
        "risk_bucket": entry.get("risk_bucket"),
        "correlation_family": entry.get("correlation_family"),
    }


def main() -> int:
    reg = yaml.safe_load(REGISTRY.read_text())
    rows = [row_for(a) for a in reg["alphas"]]

    lines = [
        "# Live Alpha Lab — scoreboard",
        "",
        f"Généré : {datetime.now(timezone.utc).isoformat()}",
        "",
        "⚠ `operational_status=SIGNAL_SHADOW` signifie UNIQUEMENT que le signal tourne réellement.",
        "Ça ne dit RIEN sur la validité de l'alpha — voir `scientific_status`. Seule la colonne",
        "`forward_decisions` (event_time > freeze_timestamp) compte comme preuve jamais-vue ;",
        "`replay_decisions` est du backfill historique, pas une preuve forward.",
        "",
        "| alpha_id | family | scientific_status | operational_status | freeze_timestamp | replay | forward | risk_bucket |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r["scientific_status"], r["alpha_id"])):
        lines.append(
            f"| {r['alpha_id']} | {r['family']} | {r['scientific_status']} | "
            f"{r['operational_status']} | {r['freeze_timestamp']} | "
            f"{r['replay_decisions']} | **{r['forward_decisions']}** | {r['risk_bucket']} |"
        )

    total_forward = sum(r["forward_decisions"] for r in rows)
    lines += [
        "",
        f"**Total forward_decisions toutes familles : {total_forward}**"
        + (" — attendu à ce stade, le correctif de discipline vient d'être appliqué "
           "(tous les freeze_timestamp sont à J0 ou récents)." if total_forward == 0 else "."),
    ]

    OUT_MD.write_text("\n".join(lines) + "\n")
    print(f"Scoreboard écrit -> {OUT_MD}")
    print(f"Total forward_decisions : {total_forward}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
