#!/usr/bin/env python3
"""
scripts/run_funding_basis_disagreement_shadow.py
─────────────────────────────────────────────────────────────────────────────
FUNDING_BASIS_DISAGREEMENT_V1 — Mode A (SIGNAL SHADOW) runner.

Live Alpha Lab, deuxième alpha implémenté (voir configs/live_alpha_registry.yaml
alpha_id: FUNDING_BASIS_DISAGREEMENT_V1, mécanisme M7 de
reports/edge_discovery/alpha_hunt_2026-08-30/w4_calendar_basis/REPORT.md).
Calcule le signal de désaccord funding/basis trimestriel, écrit une décision
par entrée déclusterée -- N'ENVOIE AUCUN ORDRE, ne simule même pas de fill
(Mode A pur). Pas de simulation multi-leg : aucun simulateur de mismatch de
jambe n'existe encore pour cet alpha (voir freeze_spec.json
"execution_blocked_reason") -- construire Mode B est explicitement HORS
PÉRIMÈTRE de ce script.

Univers : [BTCUSDT, ETHUSDT] -- FIGÉ, lu directement depuis l'entrée registry
de cet alpha (seuls ces deux symboles ont des futures trimestriels Binance).
Ne modifie JAMAIS panel.py/disagreement.py (le pipeline figé) ni les scripts
de recherche read-only sous reports/edge_discovery/.

Idempotent : relit l'historique existant, ne réémet que les décisions dont
(date, symbol) n'existe pas déjà dans le ledger.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.provenance import git_head_sha

from src.institutional.engines.funding_basis_disagreement.panel import build_panel
from src.institutional.engines.funding_basis_disagreement.disagreement import (
    FROZEN_HORIZON_DAYS, select_tradeable,
)

ALPHA_ID = "FUNDING_BASIS_DISAGREEMENT_V2"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
HORIZON = f"k{FROZEN_HORIZON_DAYS}d"
EXPECTED_UNIVERSE = ["BTCUSDT", "ETHUSDT"]  # seuls symboles avec futures trimestriels Binance


def universe_hash(universe: list[str]) -> str:
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def load_registry_entry(alpha_id: str) -> dict:
    reg = yaml.safe_load(REGISTRY.read_text())
    entries = [a for a in reg["alphas"] if a["alpha_id"] == alpha_id]
    if not entries:
        raise RuntimeError(f"{alpha_id} absent de {REGISTRY} — refus de tourner sans entrée figée.")
    return entries[0]


def load_universe() -> list[str]:
    """Univers FIGÉ : lu depuis l'entrée registry elle-même (pas de config
    YAML séparée pour cet alpha -- univers intrinsèquement figé à 2 symboles).
    Fail-closed si le registre dérive de EXPECTED_UNIVERSE (le seul cas
    légitime de changement d'univers est un nouvel alpha_id _V2)."""
    entry = load_registry_entry(ALPHA_ID)
    universe = sorted(entry.get("universe") or [])
    if universe != sorted(EXPECTED_UNIVERSE):
        raise RuntimeError(
            f"UNIVERS DÉRIVÉ : registry.universe={universe} != figé {sorted(EXPECTED_UNIVERSE)} "
            "— refus de tourner (règle fail-closed, section 8 de la mission)."
        )
    return universe


def check_registry_freeze(alpha_id: str) -> None:
    """Fail-closed : seul SHADOW_LIVE/EXECUTION_SHADOW peut écrire des décisions."""
    entry = load_registry_entry(alpha_id)
    if entry.get("operational_status") not in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
        raise RuntimeError(
            f"{alpha_id} operational_status={entry.get('operational_status')!r} dans le registre — "
            "seul SHADOW_LIVE/EXECUTION_SHADOW peut écrire des décisions."
        )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    uhash = universe_hash(universe)
    check_registry_freeze(ALPHA_ID)
    print(f"[{ALPHA_ID}] univers figé : {len(universe)} symboles ({universe}), hash={uhash}", flush=True)

    print(f"[{ALPHA_ID}] build_panel() par symbole (causal, jambe trimestrielle backfill "
          f"+ jambe funding/perp live derivatives_raw)…", flush=True)
    panels = []
    for sym in universe:
        p = build_panel(sym)
        print(f"[{ALPHA_ID}]   {sym}: {len(p)} jours éligibles (near_dte>=7)"
              + (f", {p['date'].min().date()}..{p['date'].max().date()}" if not p.empty else ""),
              flush=True)
        panels.append(p)

    import pandas as pd
    panel = pd.concat(panels, ignore_index=True) if panels else pd.DataFrame()
    if panel.empty:
        print(f"[{ALPHA_ID}] panel vide (data manquante pour tous les symboles) — rien à écrire.")
        return 0

    # univers drift réel : aucune ligne du panel ne doit sortir de l'univers figé
    runtime_symbols = set(panel["symbol"].unique())
    if not runtime_symbols.issubset(set(universe)):
        extra = runtime_symbols - set(universe)
        raise RuntimeError(f"UNIVERSE DRIFT DÉTECTÉ dans build_panel(): symboles hors univers figé: {extra}")

    tradeable = select_tradeable(panel)
    print(f"[{ALPHA_ID}] {len(panel)} jours-panel -> {len(tradeable)} entrées tradeable "
          f"(régime figé + décluster épisode + décluster non-chevauchement {HORIZON})", flush=True)

    if tradeable.empty:
        print(f"[{ALPHA_ID}] rien de tradeable sur cette fenêtre (attendu : signal rare, "
              f"~15-24 épisodes/an/actif au régime historique).")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    dec = tradeable.copy()
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec["decided_at"] = now

    dec["code_commit_sha"] = git_head_sha()
    dec["tier"] = "shadow"   # Mode A pur — pas de fill simulé, jamais "book"

    # idempotence : ne pas dupliquer une (date, symbol) déjà décidée.
    if LEDGER.exists():
        old = pd.read_parquet(LEDGER)
        key_old = set(zip(old["date"], old["symbol"]))
        new_mask = [not ((d, s) in key_old) for d, s in zip(dec["date"], dec["symbol"])]
        dec_new = dec[new_mask]
        if dec_new.empty:
            print(f"[{ALPHA_ID}] rien de nouveau (idempotent) — {len(old)} décisions déjà connues.")
            return 0
        out = pd.concat([old, dec_new], ignore_index=True)
        n_new = len(dec_new)
    else:
        out = dec
        n_new = len(dec)

    out.to_parquet(LEDGER, index=False)
    meta = {
        "alpha_id": ALPHA_ID, "last_run": now, "universe_hash": uhash,
        "universe_size": len(universe), "n_decisions_total": len(out),
        "n_decisions_new": n_new, "mode": "A_SIGNAL_SHADOW", "horizon": HORIZON,
    }
    (OUT_DIR / "run_state.json").write_text(json.dumps(meta, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites "
          f"({len(out)} total) -> {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
