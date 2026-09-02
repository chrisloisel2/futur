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

import hashlib
import subprocess
from pathlib import Path
from typing import Dict

import pandas as pd
import yaml

REPLAY = "REPLAY"
FORWARD_LIVE = "FORWARD_LIVE"

# Décisions écrites AVANT que ce module existe (2026-08-31, correction de
# discipline) n'ont pas de commit reconstructible -- valeur sentinelle
# explicite, jamais un SHA inventé.
PRE_COMMIT_DISCIPLINE = "PRE_COMMIT_DISCIPLINE_2026-08-31"

# item P1 (phase OPERATIONAL HARDENING) : P1_EQUAL_RISK et P1_CONTROL ont un
# historique divergent d'AVANT le fix root-cause de get_mark() (commit
# ed17708, root cause = _from_derivatives_raw utilisait un heuristique
# "derniers 4 fichiers" incompatible avec le vrai pattern d'écriture du
# collecteur -- cf marks.py::eligible_files_for_as_of). Cet historique n'est
# PAS réécrit (principe du projet : jamais recalculer le passé), mais ne
# doit pas non plus être mélangé silencieusement avec les données post-fix
# dans une comparaison scientifique -- les deux segments ne sont pas
# économiquement comparables (le pré-fix contient un artefact de
# non-déterminisme connu, pas une vraie différence de stratégie).
PRE_EXECUTION_TRUTH_FIX = "PRE_EXECUTION_TRUTH_FIX"
POST_EXECUTION_TRUTH_FIX = "POST_EXECUTION_TRUTH_FIX"
# Horodatage du commit ed17708 lui-même (git log -1 --format=%cI ed17708),
# pas une estimation -- la frontière est le moment où le code a changé,
# pas un round number arbitraire.
EXECUTION_TRUTH_FIX_DEPLOYED_AT = pd.Timestamp("2026-09-01T11:13:00+00:00")


def execution_truth_fix_segment(ts) -> str:
    """PRE_EXECUTION_TRUTH_FIX si ts est strictement avant le déploiement du
    fix (commit ed17708), POST_EXECUTION_TRUTH_FIX sinon. À utiliser pour
    filtrer toute comparaison scientifique entre portefeuilles/segments
    d'historique au seul segment POST -- jamais mélanger les deux."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return PRE_EXECUTION_TRUTH_FIX if t < EXECUTION_TRUTH_FIX_DEPLOYED_AT else POST_EXECUTION_TRUTH_FIX

_ROOT = Path(__file__).resolve().parents[3]


def git_head_sha() -> str:
    """SHA du commit HEAD au moment de l'écriture d'une décision -- pour que
    le code ayant produit un signal live soit toujours reconstructible
    (instruction utilisateur, correction de discipline 2026-08-31 point 10).

    ⚠ Correction 2026-08-31 (phase ECONOMIC TRUTH) : ne suffixe PLUS
    '-dirty' au SHA (c'était une provenance SILENCIEUSE -- un consommateur
    lisant juste `code_commit_sha` ne verrait rien d'anormal). L'état de
    l'arbre de travail est maintenant un champ EXPLICITE séparé, voir
    `working_tree_dirty()`. Toujours enregistrer LES DEUX."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True,
            text=True, timeout=5, check=True,
        ).stdout.strip()
    except Exception:
        return "UNKNOWN_GIT_SHA"


def working_tree_dirty() -> bool:
    """True si l'arbre de travail a des modifications non commitées (n'importe
    où dans le repo, pas seulement dans les fichiers Live Alpha Lab) --
    champ explicite, jamais encodé en silence dans code_commit_sha."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=_ROOT, capture_output=True,
            text=True, timeout=5, check=True,
        ).stdout.strip()
        return bool(out)
    except Exception:
        return True   # fail closed : incapable de vérifier -> traiter comme sale


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


_REGISTRY_PATH = _ROOT / "configs" / "live_alpha_registry.yaml"


def config_hash(registry_path: Path = _REGISTRY_PATH) -> str:
    """sha256 du FICHIER REGISTRE ENTIER -- détecte tout changement, même à
    un AUTRE alpha (utile pour repérer un run pris pendant une édition
    concurrente du fichier, comme observé plusieurs fois aujourd'hui avec
    les workers parallèles)."""
    if not registry_path.exists():
        return "REGISTRY_NOT_FOUND"
    return hashlib.sha256(registry_path.read_bytes()).hexdigest()[:16]


def alpha_spec_hash(alpha_id: str, registry_path: Path = _REGISTRY_PATH) -> str:
    """sha256 de l'entrée d'UN alpha spécifique dans le registre (dict trié,
    donc stable même si l'ordre des clés YAML change) -- détecte un
    changement de SA propre spec sans être sensible aux autres entrées."""
    if not registry_path.exists():
        return "REGISTRY_NOT_FOUND"
    reg = yaml.safe_load(registry_path.read_text())
    entries = [a for a in reg.get("alphas", []) if a.get("alpha_id") == alpha_id]
    if not entries:
        return "ALPHA_NOT_IN_REGISTRY"
    import json
    canonical = json.dumps(entries[0], sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def spec_provenance(alpha_id: str) -> Dict[str, object]:
    """Bundle des 4 champs de provenance exigés sur CHAQUE décision
    FORWARD_LIVE (instruction utilisateur, phase ECONOMIC TRUTH) :
    code_commit_sha, working_tree_dirty, config_hash, alpha_spec_hash.
    universe_hash reste calculé séparément par chaque runner (dépend de sa
    propre notion d'univers, pas générique)."""
    return {
        "code_commit_sha": git_head_sha(),
        "working_tree_dirty": working_tree_dirty(),
        "config_hash": config_hash(),
        "alpha_spec_hash": alpha_spec_hash(alpha_id),
    }


def provenance_counts(df: pd.DataFrame) -> dict:
    if "provenance" not in df.columns or df.empty:
        return {"replay_decisions": 0, "forward_decisions": 0}
    vc = df["provenance"].value_counts()
    return {
        "replay_decisions": int(vc.get(REPLAY, 0)),
        "forward_decisions": int(vc.get(FORWARD_LIVE, 0)),
    }
