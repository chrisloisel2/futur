"""
src/institutional/live_alpha_lab/provenance.py
─────────────────────────────────────────────────────────────────────────────
Discipline correction (2026-08-31, instruction utilisateur explicite) : le
fait qu'un script "tourne en temps réel" ne rend pas ses décisions
FORWARD_LIVE. Une décision n'est FORWARD_LIVE que si l'ÉVÉNEMENT qu'elle
décrit (pas le moment où le script a tourné) est postérieur au
freeze_timestamp de l'alpha ET que les données d'entrée proviennent
réellement d'une observation reçue après le freeze.

Toutes les décisions dont l'event_time (ou équivalent) est <= freeze_timestamp
sont REPLAY — même si `decided_at` (le moment où le script a matériellement
tourné) est très récent. C'est exactement ce qui s'est produit le premier
jour : LIQ_CASCADE_REPEAT_V1 a écrit 5664 décisions couvrant 2020-10..2026-08,
alors que son freeze_timestamp était 2026-08-31 — la quasi-totalité de ce
volume est un backfill historique (REPLAY), pas une preuve forward.

Ne réécrit JAMAIS une ligne existante autrement qu'en lui ajoutant la colonne
`provenance` — aucune valeur passée n'est modifiée ou supprimée.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pandas as pd

REPLAY = "REPLAY"
FORWARD_LIVE = "FORWARD_LIVE"

# Décisions écrites AVANT que ce module existe (2026-08-31, correction de
# discipline) n'ont pas de commit reconstructible -- valeur sentinelle
# explicite, jamais un SHA inventé.
PRE_COMMIT_DISCIPLINE = "PRE_COMMIT_DISCIPLINE_2026-08-31"

_ROOT = Path(__file__).resolve().parents[3]


def git_head_sha(dirty_suffix: bool = True) -> str:
    """SHA du commit HEAD au moment de l'écriture d'une décision -- pour que
    le code ayant produit un signal live soit toujours reconstructible
    (instruction utilisateur, correction de discipline 2026-08-31 point 10).

    Si l'arbre de travail est sale (modifications non commitées), suffixe
    '-dirty' : un SHA seul ne suffirait pas à reconstruire exactement le code
    qui a tourné dans ce cas -- le dire explicitement plutôt que mentir par
    omission."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True,
            text=True, timeout=5, check=True,
        ).stdout.strip()
    except Exception:
        return "UNKNOWN_GIT_SHA"
    if dirty_suffix:
        try:
            dirty = subprocess.run(
                ["git", "status", "--porcelain"], cwd=_ROOT, capture_output=True,
                text=True, timeout=5, check=True,
            ).stdout.strip()
            if dirty:
                sha += "-dirty"
        except Exception:
            pass
    return sha


def tag_provenance(df: pd.DataFrame, time_col: str, freeze_timestamp) -> pd.DataFrame:
    """Ajoute (ou recalcule idempotemment) la colonne `provenance` sur `df`.

    `time_col` doit être la colonne représentant le moment de l'ÉVÉNEMENT réel
    décrit par la ligne (event_time, timestamp, date…) — PAS `decided_at`
    (moment d'exécution du script, non pertinent pour cette classification).
    """
    freeze = pd.Timestamp(freeze_timestamp)
    if freeze.tzinfo is None:
        freeze = freeze.tz_localize("UTC")
    out = df.copy()
    t = pd.to_datetime(out[time_col], utc=True)
    out["provenance"] = pd.Series(REPLAY, index=out.index, dtype="object")
    out.loc[t > freeze, "provenance"] = FORWARD_LIVE
    return out


def provenance_counts(df: pd.DataFrame) -> dict:
    if "provenance" not in df.columns or df.empty:
        return {"replay_decisions": 0, "forward_decisions": 0}
    vc = df["provenance"].value_counts()
    return {
        "replay_decisions": int(vc.get(REPLAY, 0)),
        "forward_decisions": int(vc.get(FORWARD_LIVE, 0)),
    }
