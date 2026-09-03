#!/usr/bin/env python3
"""
scripts/run_liq_cascade_repeat_systemic_shadow.py
─────────────────────────────────────────────────────────────────────────────
LIQ_CASCADE_REPEAT_SYSTEMIC_V1 — Mode A (SIGNAL SHADOW) runner.

Alpha issu du candidat LIQ_REPEAT_DENSITY, VALIDÉ INDÉPENDAMMENT le 2026-09-02
(reports/edge_discovery/validation_2026-09/LIQ_REPEAT_DENSITY/REPORT.md,
verdict VALIDATED_FOR_FORWARD) et resté sans code jusqu'ici : il était donc
« validé » sans accumuler la moindre preuve forward. Ce runner le met
réellement en paper trading.

Réutilise sans les modifier : le détecteur figé (detector.py / dataset.py), le
classificateur de répétition (repeat_variant.py), l'univers figé
(configs/portfolio_v1_1_parallel_50.yaml). N'ajoute que le filtre de densité
market-wide (density_variant.py), dont le seuil est une constante figée issue
du rapport de validation — jamais recalculée au runtime.

Rapport au parent LIQ_CASCADE_REPEAT_V1 : ce n'est PAS un remplacement et le
parent n'est pas modifié. Les deux tournent côte à côte dans le laboratoire,
sur le même risk_bucket (LIQUIDATION_FAMILY) et la même correlation_family
(LIQ_CASCADE_DETECTOR) — c'est la couche portefeuille qui gère la
déduplication de risque. Le sous-ensemble tradé ici est strictement inclus dans
celui du parent : la comparaison des deux ledgers forward est précisément ce qui
mesurera, en données jamais vues, si retirer le bucket isolé paie vraiment.

Idempotent (append-only, déduplication sur (event_time, symbol)) — sûr à
relancer à chaque cycle.
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

from src.institutional.live_alpha_lab.provenance import spec_provenance, stamp_event_ids

from src.institutional.engines.liq_cascade.dataset import build_event_dataset
from src.institutional.engines.liq_cascade.density_variant import (
    DENSITY_KIND, DENSITY_SYSTEMIC_MIN, DENSITY_WINDOW_MINUTES,
    select_tradeable_systemic)
from src.institutional.engines.liq_cascade.detector import CascadeConfig
from src.institutional.engines.liq_cascade.repeat_variant import select_tradeable

ALPHA_ID = "LIQ_CASCADE_REPEAT_SYSTEMIC_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
HORIZON = "fwd_4h"


def load_universe() -> list:
    """Univers FIGÉ — jamais dérivé d'un glob() sur data/ (bug d'universe-drift
    corrigé le 2026-08-30, cf tests/test_universe_drift_guard.py)."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def universe_hash(universe: list) -> str:
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def check_registry_freeze(alpha_id: str) -> None:
    """Fail-closed : pas d'entrée figée dans le registre = pas d'écriture."""
    reg = yaml.safe_load(REGISTRY.read_text())
    entries = [a for a in reg["alphas"] if a["alpha_id"] == alpha_id]
    if not entries:
        raise RuntimeError(f"{alpha_id} absent de {REGISTRY} — refus de tourner sans entrée figée.")
    entry = entries[0]
    if entry.get("operational_status") not in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
        raise RuntimeError(
            f"{alpha_id} operational_status={entry.get('operational_status')!r} — "
            "seul SIGNAL_SHADOW/EXECUTION_SHADOW peut écrire des décisions."
        )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    uhash = universe_hash(universe)
    check_registry_freeze(ALPHA_ID)
    print(f"[{ALPHA_ID}] univers figé : {len(universe)} symboles, hash={uhash}", flush=True)

    ev = build_event_dataset(universe, CascadeConfig(), detector_fn=None)
    if ev.empty:
        print(f"[{ALPHA_ID}] aucun event — rien à écrire.")
        return 0

    runtime_symbols = set(ev["symbol"].unique())
    if not runtime_symbols.issubset(set(universe)):
        raise RuntimeError(
            f"UNIVERSE DRIFT DÉTECTÉ : symboles hors univers figé : "
            f"{runtime_symbols - set(universe)}"
        )

    base = select_tradeable(ev)
    # La densité se calcule sur `ev` COMPLET (tous les symboles de l'univers),
    # pas sur `base` : c'est une mesure de l'état du MARCHÉ au moment t, pas une
    # propriété du sous-ensemble tradeable. La restreindre à `base` sous-estimerait
    # systématiquement la densité et ferait basculer des épisodes systémiques
    # vers le bucket isolé.
    tradeable = select_tradeable_systemic(ev, base)
    print(f"[{ALPHA_ID}] {len(ev)} events bruts -> {len(base)} exhaustion -> "
          f"{len(tradeable)} systémiques (densité {DENSITY_KIND} >= {DENSITY_SYSTEMIC_MIN} "
          f"sur {DENSITY_WINDOW_MINUTES}min)", flush=True)

    if tradeable.empty:
        print(f"[{ALPHA_ID}] rien de tradeable sur cette fenêtre.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    dec = tradeable[["event_time", "symbol", "kind", "n_events_sym_24h",
                     "repeat_bucket", "density_60m", "density_regime",
                     "direction", "oi_drop_z", "px_ret_30m"]].copy()
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec = stamp_event_ids(dec, ALPHA_ID, "event_time", "symbol")
    dec["decided_at"] = now

    for _k, _v in spec_provenance(ALPHA_ID).items():
        dec[_k] = _v
    dec["tier"] = "shadow"

    if LEDGER.exists():
        import pandas as pd
        old = pd.read_parquet(LEDGER)
        key_old = set(zip(old["event_time"], old["symbol"]))
        new_mask = [not ((et, sy) in key_old) for et, sy in zip(dec["event_time"], dec["symbol"])]
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
    (OUT_DIR / "run_state.json").write_text(json.dumps({
        "alpha_id": ALPHA_ID, "last_run": now, "universe_hash": uhash,
        "universe_size": len(universe), "n_decisions_total": len(out),
        "n_decisions_new": n_new, "mode": "A_SIGNAL_SHADOW",
        "density_window_minutes": DENSITY_WINDOW_MINUTES,
        "density_systemic_min": DENSITY_SYSTEMIC_MIN,
    }, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites ({len(out)} total) -> {LEDGER}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
