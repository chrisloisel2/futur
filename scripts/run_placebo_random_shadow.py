#!/usr/bin/env python3
"""
scripts/run_placebo_random_shadow.py
─────────────────────────────────────────────────────────────────────────────
PLACEBO_RANDOM_V1 — un alpha à signal ALÉATOIRE, qui traverse exactement le
même chemin que les vrais.

Pourquoi il existe
──────────────────
Le labelliseur de résultats (item A1) a mesuré, sur 548 décisions forward
scellées, un excess net de -13,4 bps pour SHORT_COVERING et -0,3 pour
FAR_FROM_LOW. Ces chiffres reposent entièrement sur une chaîne : une source de
prix, deux ancrages, un decluster en épisodes, une référence de marché, un
modèle de coût, un bootstrap. Chacun de ces maillons peut avoir un biais.

Aucune quantité de statistique SUR LES ALPHAS RÉELS ne peut trancher ça. Un
signal aléatoire, oui : il n'a par construction aucun edge, donc tout ce qu'il
"gagne" ou "perd" en traversant la même chaîne est un biais de la chaîne. Si le
placebo ressort à +30 bps bruts comme les vrais, le brut vient du marché et pas
du signal — ce que la référence de marché dit déjà. S'il ressort à un excess
significativement non nul, c'est la MESURE qui est biaisée, et tous les
chiffres du lab sont à relire.

C'est le seul instrument qui mesure l'infrastructure elle-même, et il coûte
un fichier.

Ce qu'il imite, et ce qu'il n'imite pas
───────────────────────────────────────
IMITE : le schéma de décision (symbole, direction, horizon), l'univers figé,
la grille temporelle 5 min des détecteurs de cascade, l'étiquetage de
provenance, le stamping de provenance de spec, le ledger append-only, et
surtout le LABELLISEUR et le modèle de coût — il est dans `outcomes.LABELABLE`
au même titre que les vrais.

N'IMITE PAS : le taux de déclenchement d'un alpha en particulier. Il émet à
cadence FIXE (`DECISIONS_PER_CYCLE` symboles distincts par cycle), plus vite
que n'importe quel vrai alpha, parce qu'un contrôle a d'autant plus de valeur
que son intervalle de confiance est étroit. Le decluster par (symbole, 24 h)
plafonne de toute façon à ~50 épisodes indépendants par jour, quel que soit le
débit brut.

Déterminisme
────────────
La graine est dérivée de la barre 5 min courante, PAS de l'horloge : relancer
le même cycle produit les mêmes décisions, donc le runner est idempotent comme
tous les autres (déduplication sur (event_time, symbol)). Un placebo qu'on
pourrait re-tirer jusqu'à obtenir le résultat voulu ne serait pas un contrôle.

AUCUN CAPITAL. `scientific_status: PLACEBO` est refusé par
`eligibility.BLOCK_PLACEBO`, une porte qui ne dépend PAS du registre de
validation — un placebo ne doit pas pouvoir être promu par une édition.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yaml

from src.institutional.live_alpha_lab.provenance import spec_provenance, stamp_event_ids

ALPHA_ID = "PLACEBO_RANDOM_V1"
HORIZON = "fwd_4h"
UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"

# Nombre de symboles DISTINCTS tirés par cycle. Volontairement au-dessus du
# débit de tout alpha réel : la valeur d'un contrôle est dans l'étroitesse de
# son intervalle.
DECISIONS_PER_CYCLE = 4

# Grille 5 min — la même que celle des détecteurs de cascade, pour que le
# placebo soit ancré sur les mêmes instants que ce qu'il contrôle.
BAR_MINUTES = 5


def load_universe() -> list:
    """Univers FIGÉ, jamais un glob() (bug d'universe-drift, 2026-08-30)."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def universe_hash(universe: list) -> str:
    return hashlib.sha256("|".join(universe).encode()).hexdigest()[:16]


def current_bar(now: pd.Timestamp) -> pd.Timestamp:
    """Dernière barre 5 min CLOSE. Jamais la barre en cours : un vrai
    détecteur ne voit un événement qu'une fois sa barre terminée, et donner au
    placebo une longueur d'avance que les vrais n'ont pas en ferait un
    contrôle plus favorable qu'eux."""
    floored = now.floor(f"{BAR_MINUTES}min")
    return floored - pd.Timedelta(minutes=BAR_MINUTES)


def draw(universe: list, bar: pd.Timestamp, n: int) -> list:
    """Tirage DÉTERMINISTE pour une barre donnée : même barre -> même tirage.
    Graine dérivée de l'horodatage de la barre et de l'alpha_id, jamais de
    l'horloge ni d'un état caché."""
    seed = int(hashlib.sha256(f"{ALPHA_ID}|{bar.isoformat()}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    picks = rng.choice(len(universe), size=min(n, len(universe)), replace=False)
    return [universe[i] for i in picks], seed


def main() -> int:
    universe = load_universe()
    uhash = universe_hash(universe)
    now = pd.Timestamp(datetime.now(timezone.utc))
    bar = current_bar(now)
    symbols, seed = draw(universe, bar, DECISIONS_PER_CYCLE)

    dec = pd.DataFrame([{
        "event_time": bar, "symbol": s,
        # LONG only, comme les cinq alphas labellisables : le contrôle doit
        # partager leur exposition directionnelle, sinon il contrôlerait autre
        # chose qu'eux.
        "direction": "LONG",
        "draw_seed": seed, "draw_size": len(symbols),
    } for s in symbols])
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec["decided_at"] = now.isoformat()
    dec["tier"] = "shadow"
    for k, v in spec_provenance(ALPHA_ID).items():
        dec[k] = v
    dec = stamp_event_ids(dec, ALPHA_ID, "event_time", "symbol")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if LEDGER.exists():
        old = pd.read_parquet(LEDGER)
        known = set(zip(pd.to_datetime(old["event_time"], utc=True), old["symbol"]))
        mask = [(pd.Timestamp(t), s) not in known
                for t, s in zip(dec["event_time"], dec["symbol"])]
        dec_new = dec[mask]
        if dec_new.empty:
            print(f"[{ALPHA_ID}] rien de nouveau (idempotent) — {len(old)} décisions connues.")
            return 0
        out = pd.concat([old, dec_new], ignore_index=True)
        n_new = len(dec_new)
    else:
        out, n_new = dec, len(dec)

    out.to_parquet(LEDGER, index=False)
    (OUT_DIR / "run_state.json").write_text(json.dumps({
        "alpha_id": ALPHA_ID, "last_run": now.isoformat(), "bar": bar.isoformat(),
        "universe_hash": uhash, "universe_size": len(universe),
        "n_decisions_total": len(out), "n_decisions_new": n_new,
        "decisions_per_cycle": DECISIONS_PER_CYCLE, "draw_seed": seed,
        "mode": "PLACEBO_NO_CAPITAL",
    }, indent=2))
    print(f"[{ALPHA_ID}] {n_new} décisions aléatoires écrites ({len(out)} total) "
          f"sur la barre {bar.isoformat()} -> {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
