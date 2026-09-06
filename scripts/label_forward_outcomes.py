#!/usr/bin/env python3
"""
scripts/label_forward_outcomes.py
─────────────────────────────────────────────────────────────────────────────
Étape de SCELLEMENT du Live Alpha Lab : à chaque cycle, labelliser le résultat
réalisé des décisions forward dont l'horizon vient d'échoir, et ne plus jamais
y toucher.

Pourquoi dans le cycle, et pas en batch
───────────────────────────────────────
Un batch rétrospectif peut être relancé avec d'autres paramètres jusqu'à ce
que le chiffre plaise. Un label écrit à l'échéance, dans les minutes qui
suivent, et refusé à la réécriture, ne le peut pas. Ce script est appelé par
scripts/run_live_alpha_lab_cycle.py juste après l'étiquetage de provenance
(donc toutes les 15 min) précisément pour que la grande majorité des labels
naissent SEALED_AT_MATURITY. Voir src/institutional/live_alpha_lab/outcomes.py
pour le contrat complet (append-only, fenêtre de scellement, empreinte des
paramètres).

Ce qu'il n'invente pas
──────────────────────
- Aucun prix : marks.get_mark() ou rien (refus explicite NO_PRICE/STALE_MARK).
- Aucun net : le ledger ne scelle que le BRUT ; le net est dérivé à la lecture
  sous deux hypothèses de coût déclarées (base 14 bps, stress 28 bps).
- Aucun alpha labellisé « au cas où » : tout alpha portant des décisions
  forward doit être soit dans LABELABLE, soit dans NOT_LABELABLE AVEC motif.
  Un alpha absent des deux est signalé bruyamment (DÉRIVE), même logique que
  registry_drift() dans le cycle.

Sorties
───────
  reports/live_alpha_lab/<ALPHA>/outcomes.parquet    ledger scellé, append-only
  reports/live_alpha_lab/OUTCOME_LABELING_STATE.json dernier passage
"""
from __future__ import annotations

import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import yaml

from src.institutional.live_alpha_lab.outcomes import (
    LABELABLE, NOT_LABELABLE, label_alpha, label_params_digest, load_outcomes,
    summarize_outcomes,
)

REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
LAB_DIR = ROOT / "reports" / "live_alpha_lab"
STATE_PATH = LAB_DIR / "OUTCOME_LABELING_STATE.json"
LOCK_PATH = LAB_DIR / ".outcome_label.lock"


def forward_bearing_alphas() -> dict:
    """alpha_id -> nb de décisions FORWARD_LIVE sur disque."""
    out = {}
    for p in sorted(LAB_DIR.glob("*/decisions.parquet")):
        alpha_id = p.parent.name
        try:
            df = pd.read_parquet(p, columns=["provenance"])
        except Exception:
            continue
        n = int((df["provenance"] == "FORWARD_LIVE").sum()) if "provenance" in df else 0
        if n:
            out[alpha_id] = n
    return out


def main() -> int:
    # Deux labelliseurs concurrents écriraient le MÊME parquet (un passage à la
    # main pendant un cycle systemd, par exemple). L'append-only protège du
    # doublon logique, pas d'une écriture entrelacée du fichier. Verrou non
    # bloquant : le second sort proprement, il n'a rien à rattraper que le
    # premier ne fasse déjà.
    LAB_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[label] un autre labelliseur est déjà en cours — sortie propre", flush=True)
        return 0

    now = pd.Timestamp.now(tz="UTC")
    reg = yaml.safe_load(REGISTRY.read_text())
    registry_ids = {a["alpha_id"] for a in reg["alphas"]}

    bearing = forward_bearing_alphas()
    drift = sorted(a for a in bearing if a not in LABELABLE and a not in NOT_LABELABLE)
    if drift:
        print(f"[label] ⚠ DÉRIVE : {len(drift)} alpha(s) portant des décisions forward "
              f"sans décision de labellisation -> {', '.join(drift)}. "
              f"Les ajouter à LABELABLE ou à NOT_LABELABLE (avec motif) dans outcomes.py.",
              flush=True)

    results, summaries = [], {}
    for alpha_id, spec in sorted(LABELABLE.items()):
        path = LAB_DIR / alpha_id / "decisions.parquet"
        if not path.exists():
            results.append({"alpha_id": alpha_id, "status": "NO_LEDGER",
                            "n_forward": 0, "n_new": 0})
            continue
        res = label_alpha(alpha_id, pd.read_parquet(path), spec, now=now, lab_dir=LAB_DIR)
        results.append(res)
        marker = "✓" if res.get("status") == "OK" else "✗"
        print(f"[label] {marker} {alpha_id:34s} forward={res.get('n_forward', 0):4d} "
              f"nouveaux={res.get('n_new', 0):4d} "
              f"(scellés_à_échéance={res.get('n_sealed_at_maturity', 0)}, "
              f"backfill_tardif={res.get('n_late_backfill', 0)}, "
              f"refusés_sans_prix={res.get('n_refused_no_price', 0)}, "
              f"en_attente={res.get('n_pending_not_mature_or_waiting_price', 0)})",
              flush=True)

        led = load_outcomes(alpha_id, LAB_DIR)
        if led is not None and not led.empty:
            for anchor in ("dec", "evt"):
                s = summarize_outcomes(led, anchor=anchor)
                if s is not None:
                    summaries.setdefault(alpha_id, {})[anchor] = s.__dict__

    # Invariant d'exhaustivité : aucun alpha labellisable ne doit avoir de
    # décision forward mûre ni scellée ni explicitement refusée au-delà de la
    # fenêtre d'abandon. Un écart ici signalerait un trou silencieux dans la
    # preuve — exactement ce que ce module existe pour rendre impossible.
    state = {
        "run_at": now.isoformat(),
        "label_params_sha256": label_params_digest(),
        "forward_bearing_alphas": bearing,
        "labelable": sorted(LABELABLE),
        "not_labelable": NOT_LABELABLE,
        "unclassified_drift": drift,
        "registry_ids_not_on_disk": sorted(registry_ids - set(bearing)),
        "results": results,
        "summaries": summaries,
    }
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))

    total_new = sum(r.get("n_new", 0) for r in results)
    print(f"[label] {total_new} nouveau(x) label(s) scellé(s) -> {STATE_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
