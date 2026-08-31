#!/usr/bin/env python3
"""
scripts/run_liq_cascade_repeat_shadow.py
─────────────────────────────────────────────────────────────────────────────
LIQ_CASCADE_REPEAT_V1 — Mode A (SIGNAL SHADOW) runner.

Live Alpha Lab, premier alpha implémenté (voir configs/live_alpha_registry.yaml
alpha_id: LIQ_CASCADE_REPEAT_V1). Calcule le signal, écrit une Opportunity par
décision — n'envoie AUCUN ordre, ne simule même pas de fill (Mode A pur).

Ne touche JAMAIS reports/liq_cascade/shadow/ (le ledger LIQ_CASCADE existant,
en cours depuis plus longtemps) : écrit dans son propre dossier
reports/live_alpha_lab/LIQ_CASCADE_REPEAT_V1/. Ne modifie JAMAIS
detector.py/dataset.py (pipeline figé, réutilisé tel quel).

Univers : configs/portfolio_v1_1_parallel_50.yaml — FIGÉ. Ne JAMAIS dériver
l'univers d'un glob() sur un dossier de données (c'est le bug corrigé le
2026-08-30 dans run_event_shadow_daily.py/train_event_engine.py, voir
tests/test_universe_drift_guard.py). Chaque run vérifie que l'univers
runtime == univers figé (universe_hash) et s'arrête sinon (fail closed,
section 8 de la mission Live Alpha Lab).

Idempotent : relire l'historique existant, ne réémettre que les décisions
dont `decided_at` d'origine n'existe pas déjà pour cet `event_time`+`symbol`.
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

from src.institutional.engines.liq_cascade.dataset import build_event_dataset
from src.institutional.engines.liq_cascade.detector import CascadeConfig
from src.institutional.engines.liq_cascade.repeat_variant import select_tradeable

ALPHA_ID = "LIQ_CASCADE_REPEAT_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
HORIZON = "fwd_4h"


def load_universe() -> list[str]:
    """Univers FIGÉ — jamais dérivé d'un glob() sur data/, voir docstring."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def universe_hash(universe: list[str]) -> str:
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def check_registry_freeze(alpha_id: str, expected_hash: str) -> None:
    """Fail-closed : la spec figée dans live_alpha_registry.yaml doit exister
    et référencer le même détecteur/univers que ce run — sinon on n'écrit rien."""
    reg = yaml.safe_load(REGISTRY.read_text())
    entries = [a for a in reg["alphas"] if a["alpha_id"] == alpha_id]
    if not entries:
        raise RuntimeError(f"{alpha_id} absent de {REGISTRY} — refus de tourner sans entrée figée.")
    entry = entries[0]
    if entry.get("operational_status") not in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
        raise RuntimeError(
            f"{alpha_id} operational_status={entry.get('operational_status')!r} dans le registre — "
            "seul SHADOW_LIVE/EXECUTION_SHADOW peut écrire des décisions."
        )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    uhash = universe_hash(universe)
    check_registry_freeze(ALPHA_ID, uhash)
    print(f"[{ALPHA_ID}] univers figé : {len(universe)} symboles, hash={uhash}", flush=True)

    print(f"[{ALPHA_ID}] build_event_dataset() (pipeline figé, inchangé)…", flush=True)
    ev = build_event_dataset(universe, CascadeConfig(), detector_fn=None)
    if ev.empty:
        print(f"[{ALPHA_ID}] aucun event — data manquante ou univers vide, rien à écrire.")
        return 0

    # runtime_universe_hash doit correspondre au hash figé au-dessus du run —
    # ev est construit à partir du MÊME `universe` list, donc par construction
    # get_universe() ci-dessus est la seule source de vérité (pas de second
    # calcul divergent possible ici) ; on revalide quand même explicitement
    # que ev ne contient aucun symbole hors univers (fail closed réel).
    runtime_symbols = set(ev["symbol"].unique())
    if not runtime_symbols.issubset(set(universe)):
        extra = runtime_symbols - set(universe)
        raise RuntimeError(
            f"UNIVERSE DRIFT DÉTECTÉ : symboles hors univers figé dans build_event_dataset(): {extra}"
        )

    tradeable = select_tradeable(ev)
    print(f"[{ALPHA_ID}] {len(ev)} events LIQ_CASCADE bruts -> "
          f"{len(tradeable)} exhaustion LONG_CASCADE tradeable", flush=True)

    if tradeable.empty:
        print(f"[{ALPHA_ID}] rien de tradeable sur cette fenêtre.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    dec = tradeable[["event_time", "symbol", "kind", "n_events_sym_24h",
                     "repeat_bucket", "direction", "oi_drop_z", "px_ret_30m"]].copy()
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec["decided_at"] = now

    dec["code_commit_sha"] = git_head_sha()
    dec["tier"] = "shadow"   # Mode A pur — pas de fill simulé, jamais "book"

    # idempotence : ne pas dupliquer un (event_time, symbol) déjà décidé.
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
    meta = {
        "alpha_id": ALPHA_ID, "last_run": now, "universe_hash": uhash,
        "universe_size": len(universe), "n_decisions_total": len(out),
        "n_decisions_new": n_new, "mode": "A_SIGNAL_SHADOW",
    }
    (OUT_DIR / "run_state.json").write_text(json.dumps(meta, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites "
          f"({len(out)} total) -> {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
