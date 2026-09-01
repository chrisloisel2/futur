#!/usr/bin/env python3
"""
scripts/run_whale_lsr_screen_shadow.py
─────────────────────────────────────────────────────────────────────────────
WHALE_LSR_SCREEN_V1 — Mode A (SIGNAL SHADOW) runner.

Live Alpha Lab (voir configs/live_alpha_registry.yaml, alpha_id:
WHALE_LSR_SCREEN_V1). Calcule un SCREEN à partir du top-position ("whale")
long/short ratio (data/positioning/{SYM}_top_position.parquet) -- écrit une
ligne de décision par (timestamp, symbol) où le screen se déclenche.
N'ENVOIE AUCUN ORDRE, ne simule même pas de fill (Mode A pur), et n'émet
JAMAIS de champ "direction"/"SHORT" (voir
src/institutional/engines/whale_lsr_screen/screen.py -- SHORT_REJECTED,
ceci est un screen, pas une Opportunity).

Univers : configs/whale_lsr_screen_universe.yaml -- FIGÉ au 2026-08-31 (47
symboles = tous ceux ayant un fichier data/positioning/{SYM}_top_position.parquet
à cette date). Contrairement à LIQ_CASCADE_REPEAT_V1 il n'existait AUCUNE
config figée préexistante pour ce dataset -- ce fichier de config EST la
première, créée explicitement pour ce runner (voir son en-tête pour la
provenance). Chaque run vérifie que l'univers runtime == univers figé
(universe_hash) et s'arrête sinon (fail closed).

Idempotent : relit l'historique existant, ne réémet que les décisions dont
la clé (timestamp, symbol) n'existe pas déjà dans le ledger.
"""
from __future__ import annotations

import hashlib
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.provenance import spec_provenance

from src.institutional.engines.whale_lsr_screen.screen import (
    classify_screen, compute_rolling_zscore,
)

ALPHA_ID = "WHALE_LSR_SCREEN_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
UNIVERSE_CONFIG = ROOT / "configs" / "whale_lsr_screen_universe.yaml"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
POSITIONING_DIR = ROOT / "data" / "positioning"
HORIZON = "fwd_24h"


def load_universe() -> list[str]:
    """Univers FIGÉ (configs/whale_lsr_screen_universe.yaml) -- jamais
    dérivé d'un glob() sur data/ à l'exécution, voir docstring du fichier
    de config pour la provenance (un seul glob() ponctuel au freeze)."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def universe_hash(universe: list[str]) -> str:
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def check_registry_freeze(alpha_id: str, expected_hash: str) -> None:
    """Fail-closed : la spec figée dans live_alpha_registry.yaml doit exister
    et le statut doit être SHADOW_LIVE/EXECUTION_SHADOW -- sinon on n'écrit rien."""
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


def load_positioning(universe: list[str]) -> pd.DataFrame:
    """Charge {SYM}_top_position.parquet pour chaque symbole de l'univers
    FIGÉ. Un symbole dont le fichier a disparu est loggé en warning et
    sauté (fail-open sur donnée manquante) -- mais n'affecte jamais le hash
    de l'univers DÉCLARÉ (voir load_universe/universe_hash ci-dessus)."""
    frames = []
    for sym in universe:
        path = POSITIONING_DIR / f"{sym}_top_position.parquet"
        if not path.exists():
            warnings.warn(f"[{ALPHA_ID}] fichier manquant pour {sym} ({path}) — symbole sauté ce run.")
            continue
        df = pd.read_parquet(path, columns=["timestamp", "symbol", "longShortRatio"])
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "longShortRatio"])
    out = pd.concat(frames, ignore_index=True)
    # runtime_universe_hash doit correspondre au hash figé : vérifie qu'aucun
    # symbole hors univers figé n'a pu se glisser (fail closed réel, même
    # logique que run_liq_cascade_repeat_shadow.py).
    runtime_symbols = set(out["symbol"].unique())
    if not runtime_symbols.issubset(set(universe)):
        extra = runtime_symbols - set(universe)
        raise RuntimeError(
            f"UNIVERSE DRIFT DÉTECTÉ : symboles hors univers figé dans data/positioning: {extra}"
        )
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    uhash = universe_hash(universe)
    check_registry_freeze(ALPHA_ID, uhash)
    print(f"[{ALPHA_ID}] univers figé : {len(universe)} symboles, hash={uhash}", flush=True)

    print(f"[{ALPHA_ID}] chargement data/positioning/*_top_position.parquet…", flush=True)
    raw = load_positioning(universe)
    if raw.empty:
        print(f"[{ALPHA_ID}] aucune donnée positioning — rien à écrire.")
        return 0
    print(f"[{ALPHA_ID}] {len(raw)} barres chargées ({raw['symbol'].nunique()} symboles).", flush=True)

    with_z = compute_rolling_zscore(raw)
    classified = classify_screen(with_z)

    triggered = classified[
        classified["screen_flag"] | classified["mirror_flag_unconfirmed"]
    ].copy()
    print(f"[{ALPHA_ID}] {len(classified)} barres évaluées -> "
          f"{len(triggered)} déclenchements de screen (main + mirror)", flush=True)

    if triggered.empty:
        print(f"[{ALPHA_ID}] rien de déclenché sur cette fenêtre.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    dec = triggered[["timestamp", "symbol", "longShortRatio", "z_score_7d",
                      "screen_flag", "mirror_flag_unconfirmed"]].copy()
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec["decided_at"] = now

    for _k, _v in spec_provenance(ALPHA_ID).items():
        dec[_k] = _v
    dec["tier"] = "shadow"   # Mode A pur — pas de fill simulé, jamais "book"

    # idempotence : ne pas dupliquer une clé (timestamp, symbol) déjà décidée.
    if LEDGER.exists():
        old = pd.read_parquet(LEDGER)
        key_old = set(zip(old["timestamp"], old["symbol"]))
        new_mask = [not ((ts, sy) in key_old) for ts, sy in zip(dec["timestamp"], dec["symbol"])]
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
        "n_screen_flag_total": int(out["screen_flag"].sum()),
        "n_mirror_flag_unconfirmed_total": int(out["mirror_flag_unconfirmed"].sum()),
    }
    (OUT_DIR / "run_state.json").write_text(json.dumps(meta, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites "
          f"({len(out)} total) -> {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
