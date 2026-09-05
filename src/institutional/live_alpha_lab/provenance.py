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
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Optional

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


# ═══════════════════════════════════════════════════════════════════════════
# 2026-09-05 — PÉRIMÈTRE du stamp `working_tree_dirty` (décision utilisateur)
# ═══════════════════════════════════════════════════════════════════════════
# Avant : `git status --porcelain` GLOBAL. Or le dépôt suit 35 fichiers d'état
# runtime sous reports/ (state.json, cycle_log, scoreboards…) réécrits à chaque
# cycle de 15 min : l'arbre était sale en permanence, donc TOUTES les décisions
# portaient dirty=True, y compris juste après un commit propre du code. Un
# drapeau toujours levé n'informe plus personne -- c'est le contraire de la
# provenance.
#
# Après : le stamp ne regarde que ce qui INFLUENCE RÉELLEMENT une décision :
#   - src/       : moteurs, live_alpha_lab (portfolio, intents, eligibility…)
#   - scripts/   : les runners et le cycle
#   - configs/   : les DEUX registres (alphas + validation) et les runners
#   - reports/live_alpha_lab/*/freeze_spec.json : la spec figée de chaque alpha
#   - reports/live_alpha_lab/DEPLOYMENT_DECISIONS_*.md : les décisions de déploiement
# Vérifié le 2026-09-05 : les runners du lab n'importent AUCUN paquet hors
# `src` (data_pipeline/, core/, ai/ ne servent qu'au paper trading legacy,
# arrêté) -- ils sont donc volontairement hors périmètre. reports/ n'est PAS
# sorti du suivi git : seul le stamp change de périmètre.
#
# Le changement de périmètre est lui-même une frontière de segment : chaque
# décision porte `working_tree_dirty_scope` pour qu'un consommateur sache si
# un dirty=False ancien (V1, global) et un dirty=False nouveau (V2, périmètre)
# veulent dire la même chose -- ils ne le veulent pas.
DECISION_CODE_PATHSPECS = (
    "src/",
    "scripts/",
    "configs/",
    "reports/live_alpha_lab/*/freeze_spec.json",
    "reports/live_alpha_lab/DEPLOYMENT_DECISIONS_*.md",
)
WORKING_TREE_DIRTY_SCOPE = "DECISION_CODE_V2"      # V1 = porcelain global, < 2026-09-05
GIT_STATUS_UNAVAILABLE = "<GIT_STATUS_UNAVAILABLE>"


def dirty_decision_paths() -> List[str]:
    """Chemins modifiés / non suivis DANS LE PÉRIMÈTRE DE DÉCISION (voir
    DECISION_CODE_PATHSPECS). Liste vide = code de décision identique à HEAD.

    `--untracked-files=all` : un nouveau runner non encore `git add` est un
    changement de code de décision, il doit compter -- et être nommé.
    Fail closed : si git ne répond pas, on renvoie un marqueur explicite
    plutôt qu'une liste vide qui se lirait comme « propre »."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--",
             *DECISION_CODE_PATHSPECS],
            cwd=_ROOT, capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except Exception:
        return [GIT_STATUS_UNAVAILABLE]
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:            # renommage : garder la destination
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip())
    return sorted(paths)


def dirty_decision_paths_sha1() -> str:
    """Empreinte scalaire de la liste ci-dessus, stockable en colonne parquet
    (les runners diffusent chaque champ de provenance en `df[k] = v` : une
    liste ne passerait pas). Chaîne vide = propre."""
    paths = dirty_decision_paths()
    if not paths:
        return ""
    return hashlib.sha1("\n".join(paths).encode()).hexdigest()[:16]


def working_tree_dirty() -> bool:
    """True si le CODE DE DÉCISION diffère de HEAD (périmètre
    DECISION_CODE_PATHSPECS), champ explicite, jamais encodé en silence dans
    code_commit_sha. Fail closed : git injoignable -> sale."""
    return bool(dirty_decision_paths())


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
    dirty_paths = dirty_decision_paths()
    return {
        "code_commit_sha": git_head_sha(),
        "working_tree_dirty": bool(dirty_paths),
        # 2026-09-05 : périmètre du drapeau + empreinte des chemins sales
        # (scalaires uniquement -- diffusés en colonne parquet par les runners)
        "working_tree_dirty_scope": WORKING_TREE_DIRTY_SCOPE,
        "dirty_decision_paths_sha1": (
            hashlib.sha1("\n".join(dirty_paths).encode()).hexdigest()[:16] if dirty_paths else ""),
        "config_hash": config_hash(),
        "alpha_spec_hash": alpha_spec_hash(alpha_id),
    }


def stamp_event_ids(df: pd.DataFrame, alpha_id: str, time_col: str, symbol_col: Optional[str] = None) -> pd.DataFrame:
    """item P1 (phase OPERATIONAL HARDENING) : raw_event_id/feature_snapshot_id
    pour trade_trace.py -- "corriger progressivement" la lacune, jamais
    backfiller un faux ID sur d'anciennes lignes (celles-ci restent
    NOT_AVAILABLE, cf trade_trace.py). Pour une NOUVELLE ligne, les deux ID
    sont dérivés DÉTERMINISTIQUEMENT du contenu réel de la ligne -- jamais
    un UUID aléatoire opaque qui serait, lui, un ID fabriqué.

    À appeler AVANT d'ajouter les colonnes de provenance/exécution
    (decided_at, code_commit_sha, tier…) : feature_snapshot_id doit
    refléter le contenu ÉCONOMIQUE de la décision (les features qui l'ont
    produite), pas le moment où le script a tourné.

    raw_event_id : hash de (alpha_id, symbol, event_time) -- identifiant
    canonique de l'événement marché source, reconstructible par quiconque
    connaît ce triplet. `symbol_col=None` (alpha market-wide, ex.
    VOL_FORECAST_LAYER_V1 -- pas de colonne symbole par ligne) : le
    sentinel explicite "MARKET_WIDE" est utilisé à la place, jamais un
    symbole inventé.
    feature_snapshot_id : hash du contenu COMPLET de la ligne au moment de
    l'appel -- une empreinte vérifiable, pas un compteur arbitraire :
    recalculer les mêmes features à partir des mêmes données brutes doit
    reproduire le même hash.
    """
    out = df.copy()

    def _raw_event_id(row) -> str:
        symbol = row[symbol_col] if symbol_col is not None else "MARKET_WIDE"
        key = f"{alpha_id}|{symbol}|{pd.Timestamp(row[time_col]).isoformat()}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _feature_snapshot_id(row) -> str:
        payload = json.dumps({k: str(v) for k, v in row.items()}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    out["raw_event_id"] = out.apply(_raw_event_id, axis=1)
    out["feature_snapshot_id"] = out.apply(_feature_snapshot_id, axis=1)
    return out


def provenance_counts(df: pd.DataFrame) -> dict:
    if "provenance" not in df.columns or df.empty:
        return {"replay_decisions": 0, "forward_decisions": 0}
    vc = df["provenance"].value_counts()
    return {
        "replay_decisions": int(vc.get(REPLAY, 0)),
        "forward_decisions": int(vc.get(FORWARD_LIVE, 0)),
    }
