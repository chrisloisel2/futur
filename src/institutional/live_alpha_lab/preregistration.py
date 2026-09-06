"""PRÉ-ENREGISTREMENT — cinq hypothèses scellées avant d'ouvrir la donnée.

Le problème que ça résout
─────────────────────────
Le round 4 de la chasse a testé ~700 mécanismes et n'en a validé aucun. Ce
n'est pas un accident de malchance : à 700 essais, le seuil qu'il aurait fallu
franchir n'était plus celui qu'on regardait. Un essai qu'on ne compte pas est
un essai qu'on s'offre gratuitement, et l'addition se paie en faux positifs.

Ce module rend l'addition automatique.

LES TROIS PROPRIÉTÉS, ET POURQUOI CHACUNE

  1. LE SEUIL EST DÉRIVÉ DU COMPTE, JAMAIS ÉCRIT.
     `threshold_t()` ne lit aucune constante : il lit le NOMBRE d'hypothèses
     enregistrées et en déduit le seuil. Conséquence directe, et c'est tout
     l'objet du module : le jour où on enregistre cinq hypothèses de plus « juste
     pour voir », le seuil des cinq PREMIÈRES monte aussi, mécaniquement, sans
     que personne ait à s'en souvenir ni à accepter de le faire.

     C'est la seule défense qui tienne contre soi-même. Un seuil écrit dans un
     fichier de configuration est un seuil qu'on rééditera à 23 h un soir de
     déception.

  2. LA SIXIÈME EST REFUSÉE.
     Pas « déconseillée » : `register_batch` lève. On peut ouvrir un nouveau
     lot — rien ne l'interdit et l'interdire serait malhonnête — mais alors le
     compte cumulé monte, donc le seuil monte, et `retroactive_penalty()` dit
     exactement ce que ça coûte aux hypothèses déjà scellées.

  3. LE SCELLEMENT EST ANTÉRIEUR AU RÉSULTAT, ET VÉRIFIABLE.
     Chaque lot porte le SHA du code, l'empreinte de la donnée sur laquelle il
     sera testé, et un hachage chaîné. Une hypothèse ajoutée après coup casse la
     chaîne. Ce n'est pas une promesse, c'est une vérification.

CE QUE CE MODULE NE FAIT PAS
    Il n'empêche pas de tricher — rien ne l'empêche. Il rend la triche VISIBLE :
    un ledger dont la chaîne est rompue, ou dont le compte a monté sans que le
    seuil suive, se lit en une commande. La discipline reste humaine ; ce qui
    change, c'est qu'elle laisse une trace.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[3]
LEDGER = ROOT / "reports" / "live_alpha_lab" / "PREREGISTRATION_LEDGER.jsonl"

GENESIS_HASH = "0" * 64

# Le plafond par lot. Cinq, parce qu'un lot qu'on peut étendre n'est pas un lot.
MAX_HYPOTHESES_PER_BATCH = 5

# Le taux d'erreur de première espèce qu'on s'autorise sur la FAMILLE entière,
# pas par hypothèse. C'est le seul niveau auquel la question a un sens quand on
# teste plusieurs choses.
FAMILY_ALPHA = 0.05

# Unilatéral : on cherche un edge POSITIF. Un mécanisme qui perd significativement
# de l'argent n'est pas une découverte, c'est le même mécanisme retourné, et le
# compter comme un succès bilatéral offrirait un deuxième billet de loterie par
# hypothèse.
ONE_SIDED = True


class PreregistrationError(ValueError):
    """Levé plutôt que contourné. Toutes les portes ici sont fail-closed."""


@dataclass(frozen=True)
class Hypothesis:
    """Une hypothèse, avec UN seul jeu de paramètres.

    `params` est une chaîne de nombres, pas une plage. Une plage est une famille
    d'hypothèses déguisée en une seule, et c'est la façon la plus courante de
    tester quinze choses en croyant en tester une.
    """
    hypothesis_id: str
    family: str
    claim: str
    payer_who: str
    payer_why: str
    payer_stop: str
    residual_definition: str
    params: str
    horizon_days: float
    feeds: Tuple[str, ...]
    direction: str = "LONG_SHORT_DOLLAR_NEUTRAL"

    def __post_init__(self) -> None:
        for name in ("hypothesis_id", "claim", "payer_who", "payer_why",
                     "payer_stop", "residual_definition", "params"):
            if not str(getattr(self, name)).strip():
                raise PreregistrationError(
                    "%s: champ %r vide. Une hypothèse dont le payeur ou les paramètres "
                    "ne sont pas écrits n'est pas pré-enregistrée, elle est esquissée."
                    % (self.hypothesis_id, name))
        if float(self.horizon_days) <= 0:
            raise PreregistrationError("%s: horizon non positif" % self.hypothesis_id)
        if not self.feeds:
            raise PreregistrationError("%s: aucun flux déclaré" % self.hypothesis_id)
        # Le garde-fou le plus utile du lot : détecter une plage déguisée.
        lowered = str(self.params).lower()
        for smell in (" à ", " a ", "entre ", "..", "ou ", "range", "grid", "optimis"):
            if smell in lowered and any(ch.isdigit() for ch in lowered):
                if smell in (" à ", " a ", "entre ", ".."):
                    raise PreregistrationError(
                        "%s: `params` ressemble à une PLAGE (%r). Un seul jeu de "
                        "paramètres, des nombres. Une plage est une famille "
                        "d'hypothèses comptée comme une seule."
                        % (self.hypothesis_id, self.params))

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                       default=str).encode()).hexdigest()[:16]


def threshold_t(n_hypotheses: int, alpha: float = FAMILY_ALPHA,
                one_sided: bool = ONE_SIDED) -> float:
    """Le seuil de |t| à franchir, DÉRIVÉ du nombre d'hypothèses.

    Correction de Bonferroni sur la famille : chaque hypothèse est jugée à
    `alpha / n`, de sorte que la probabilité de crier victoire au moins une fois
    à tort reste `alpha` pour l'ensemble.

        n = 1  -> 1.64      n = 5  -> 2.33      n = 10 -> 2.58
        n = 20 -> 2.80      n = 700 -> 3.84

    La dernière ligne est ce que le round 4 aurait dû franchir.

    Aucune constante n'est stockée nulle part : ce seuil est recalculé à chaque
    lecture du ledger. Ajouter une hypothèse relève la barre de toutes les
    autres, y compris de celles déjà scellées, et personne n'a à y penser.
    """
    n = int(n_hypotheses)
    if n < 1:
        raise PreregistrationError("le seuil n'est pas défini pour %d hypothèse(s)" % n)
    if not 0.0 < float(alpha) < 1.0:
        raise PreregistrationError("alpha hors ]0,1[ : %r" % alpha)
    per_test = float(alpha) / n
    quantile = 1.0 - (per_test if one_sided else per_test / 2.0)
    return float(NormalDist().inv_cdf(quantile))


def _record_trials(record: Mapping[str, object]) -> int:
    """Ce qu'un enregistrement coûte au budget d'essais.

    Une hypothèse scellée coûte 1. Une DÉCISION DE CONCEPTION coûte ce qu'elle
    déclare : 0 si elle a été prise en aveugle du résultat, N si un t-stat l'a
    informée. C'est le mécanisme qui donne des dents à la ligne « ce balayage
    entre-t-il dans mon budget » — répondre « oui, 27 » relève le seuil de
    toutes les hypothèses, immédiatement et sans intervention.
    """
    return len(record.get("hypotheses", [])) + int(record.get("trials_charged", 0) or 0)


def _chain_head(path: Path) -> Tuple[str, int]:
    """(hachage de tête, nombre d'essais cumulés)."""
    if not path.is_file():
        return GENESIS_HASH, 0
    previous, total = GENESIS_HASH, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        previous = str(record["record_hash"])
        total += _record_trials(record)
    return previous, total


def registered_count(path: Path = LEDGER) -> int:
    return _chain_head(Path(path))[1]


def current_threshold(path: Path = LEDGER, extra: int = 0) -> Dict[str, object]:
    """Le seuil en vigueur, et ce qu'il deviendrait avec `extra` de plus.

    `extra` existe pour qu'on puisse REGARDER le coût d'ajouter des hypothèses
    avant de le payer. C'est la seule façon honnête de poser la question « et si
    j'en testais cinq de plus » : en lisant d'abord ce que ça fait au seuil.
    """
    n = registered_count(path)
    out: Dict[str, object] = {
        "n_registered": n,
        "threshold_t": round(threshold_t(n), 4) if n else None,
        "alpha_family": FAMILY_ALPHA,
        "one_sided": ONE_SIDED,
    }
    if extra:
        after = n + int(extra)
        out.update({
            "n_if_extended": after,
            "threshold_t_if_extended": round(threshold_t(after), 4),
            "cost_of_extension_t": round(threshold_t(after) - threshold_t(max(1, n)), 4),
        })
    return out


def retroactive_penalty(path: Path = LEDGER) -> Dict[str, object]:
    """Ce que le compte actuel impose aux lots DÉJÀ scellés.

    Une hypothèse scellée quand le compte était de 5 est jugée aujourd'hui au
    seuil du compte courant. Cette fonction rend cette dette visible lot par lot
    au lieu de la laisser implicite — c'est elle qu'il faut regarder le jour où
    on veut « juste en tester cinq de plus ».
    """
    target = Path(path)
    if not target.is_file():
        return {"batches": [], "current_threshold_t": None}
    now = registered_count(target)
    batches = []
    seen = 0
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        seen += _record_trials(record)
        batches.append({
            "batch_id": record["batch_id"],
            "n_hypotheses": len(record.get("hypotheses", [])),
            "threshold_at_seal": round(threshold_t(seen), 4),
            "threshold_now": round(threshold_t(now), 4),
            "penalty_t": round(threshold_t(now) - threshold_t(seen), 4),
        })
    return {"batches": batches, "n_registered": now,
            "current_threshold_t": round(threshold_t(now), 4) if now else None}


def register_batch(batch_id: str, family: str, hypotheses: Sequence[Hypothesis],
                   dataset_digest: str, code_commit_sha: str,
                   path: Path = LEDGER, notes: str = "") -> Dict[str, object]:
    """Scelle un lot. Refuse la sixième, et refuse un lot déjà scellé.

    Le seuil enregistré est celui du compte CUMULÉ après ce lot — pas celui du
    lot seul. Enregistrer cinq hypothèses quand il en existe déjà cinq donne un
    seuil de dix, et c'est le bon.
    """
    target = Path(path)
    items = list(hypotheses)
    if not items:
        raise PreregistrationError("un lot vide n'est pas un pré-enregistrement")
    if len(items) > MAX_HYPOTHESES_PER_BATCH:
        raise PreregistrationError(
            "%d hypothèses dans un lot, le plafond est %d. Ce refus est la raison "
            "d'être de ce ledger : au-delà, on ne teste plus une idée, on ratisse. "
            "Ouvrir un second lot est possible — et fera monter le seuil de TOUTES "
            "les hypothèses, y compris celles déjà scellées (voir "
            "retroactive_penalty())." % (len(items), MAX_HYPOTHESES_PER_BATCH))
    ids = [h.hypothesis_id for h in items]
    if len(set(ids)) != len(ids):
        raise PreregistrationError("identifiants dupliqués dans le lot : %r" % ids)

    previous, total_before = _chain_head(target)
    if target.is_file():
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line)["batch_id"] == str(batch_id):
                raise PreregistrationError(
                    "lot %r déjà scellé : le ledger est append-only, un lot ne se "
                    "réécrit pas." % batch_id)
            if line.strip():
                known = {h["hypothesis_id"] for h in json.loads(line).get("hypotheses", [])}
                clash = known.intersection(ids)
                if clash:
                    raise PreregistrationError(
                        "hypothèse(s) déjà scellée(s) sous un autre lot : %r" % sorted(clash))

    total_after = total_before + len(items)
    payload: Dict[str, object] = {
        "batch_id": str(batch_id),
        "family": str(family),
        "n_hypotheses": len(items),
        "n_registered_cumulative": total_after,
        # Le seuil est CALCULÉ, jamais fourni par l'appelant.
        "threshold_t": round(threshold_t(total_after), 4),
        "alpha_family": FAMILY_ALPHA,
        "one_sided": ONE_SIDED,
        "correction": "bonferroni_sur_le_compte_cumule",
        "dataset_digest": str(dataset_digest),
        "code_commit_sha": str(code_commit_sha),
        "sealed_before_any_result": True,
        "hypotheses": [asdict(h) for h in items],
        "notes": str(notes),
        "prev_hash": previous,
    }
    payload["record_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str
                   ).encode()).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                default=str) + "\n")
    return payload


def record_design_decision(decision_id: str, description: str, blind_to_outcome: bool,
                           evidence: str, trials_charged: int = 0,
                           path: Path = LEDGER, notes: str = "") -> Dict[str, object]:
    """Scelle une décision de CONCEPTION, et son coût en essais.

    Toute décision prise en regardant la donnée est soit aveugle au résultat —
    et alors elle est gratuite — soit informée par un t-stat, et alors elle
    consomme des essais comme n'importe quelle hypothèse.

    `blind_to_outcome=True` exige `trials_charged=0` ET une `evidence` : la
    preuve que le choix ne pouvait pas dépendre du résultat. Une affirmation
    d'aveuglement sans preuve vérifiable est une promesse, et les promesses ne
    tiennent pas six mois. La forme la plus solide d'`evidence` est un test
    qui perturbe les résultats et montre que le choix ne bouge pas.

    `blind_to_outcome=False` exige `trials_charged >= 1` : une décision
    informée par un résultat mais facturée zéro est exactement le mécanisme qui
    fait arriver à 904 essais sans s'en rendre compte.
    """
    if blind_to_outcome and int(trials_charged) != 0:
        raise PreregistrationError(
            "%s : une décision aveugle au résultat ne peut pas coûter d'essais "
            "(trials_charged=%d)" % (decision_id, trials_charged))
    if not blind_to_outcome and int(trials_charged) < 1:
        raise PreregistrationError(
            "%s : une décision informée par un résultat coûte au moins un essai. "
            "En facturer zéro est précisément la façon dont on arrive à 904 sans "
            "s'en apercevoir." % decision_id)
    if not str(evidence).strip():
        raise PreregistrationError(
            "%s : aucune preuve fournie. Une affirmation d'aveuglement sans preuve "
            "vérifiable est une promesse, pas un contrôle." % decision_id)

    target = Path(path)
    previous, total_before = _chain_head(target)
    if target.is_file():
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("batch_id") == str(decision_id):
                raise PreregistrationError("décision %r déjà scellée" % decision_id)

    total_after = total_before + int(trials_charged)
    payload: Dict[str, object] = {
        "batch_id": str(decision_id),
        "record_type": "DESIGN_DECISION",
        "family": "design",
        "description": str(description),
        "blind_to_outcome": bool(blind_to_outcome),
        "evidence": str(evidence),
        "trials_charged": int(trials_charged),
        "n_hypotheses": 0,
        "hypotheses": [],
        "n_registered_cumulative": total_after,
        "threshold_t": round(threshold_t(max(1, total_after)), 4),
        "alpha_family": FAMILY_ALPHA,
        "one_sided": ONE_SIDED,
        "correction": "bonferroni_sur_le_compte_cumule",
        "notes": str(notes),
        "prev_hash": previous,
    }
    payload["record_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str
                   ).encode()).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                default=str) + "\n")
    return payload


def record_contamination(record_id: str, family: str, burned_periods: Sequence[Tuple[str, str]],
                         untouched_periods: Sequence[Tuple[str, str]], what_was_measured: str,
                         n_looks: int, path: Path = LEDGER, notes: str = "") -> Dict[str, object]:
    """Déclare qu'une période a été REGARDÉE, et pour quelle famille.

    POURQUOI CE TYPE D'ENREGISTREMENT EXISTE
        Une contamination ne rentre proprement dans aucune des deux branches de
        `record_design_decision`. Ce n'est pas une décision de conception
        informée par un résultat ; c'est le constat qu'une période a cessé
        d'être vierge. Et sa conséquence n'est pas un seuil plus haut, c'est
        une PARTITION DE DONNÉES : on ne confirme pas sur ce qu'on a déjà
        regardé.

    POURQUOI ELLE NE FACTURE PAS D'ESSAIS
        La multiplicité s'applique aux tests menés sur les MÊMES données. Des
        centaines de regards sur 2022-2025 n'augmentent pas le taux de faux
        positifs d'un test mené sur 2026, qui n'a pas été vu. Ce qu'ils
        inflatent, c'est la SÉLECTION — d'où la règle qui compense : peu de
        finalistes promus, et la promotion se paie sur un échantillon frais.

        Facturer en plus des essais reviendrait à payer deux fois la même
        chose, et rendrait la confirmation impossible plutôt que rigoureuse.

    CE QUE CET ENREGISTREMENT OBLIGE
        `burned_periods` est consulté par le scellement : sceller une période
        déclarée brûlée pour cette famille doit être refusé. Un scellement qui
        ment est pire que pas de scellement, parce qu'il produit un chiffre
        auquel on va croire.
    """
    if not burned_periods:
        raise PreregistrationError("%s : aucune période brûlée déclarée — ce n'est pas une "
                                   "contamination, c'est une note" % record_id)
    if not str(what_was_measured).strip():
        raise PreregistrationError("%s : dire CE QUI a été mesuré est obligatoire, sinon la "
                                   "portée de la contamination est invérifiable" % record_id)

    target = Path(path)
    previous, total_before = _chain_head(target)
    if target.is_file():
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("batch_id") == str(record_id):
                raise PreregistrationError("contamination %r déjà déclarée" % record_id)

    payload: Dict[str, object] = {
        "batch_id": str(record_id),
        "record_type": "CONTAMINATION",
        "family": str(family),
        "burned_periods": [[str(a), str(b)] for a, b in burned_periods],
        "untouched_periods": [[str(a), str(b)] for a, b in untouched_periods],
        "what_was_measured": str(what_was_measured),
        "n_looks_at_a_tstat": int(n_looks),
        "trials_charged": 0,
        "why_zero_trials": (
            "la multiplicité s'applique aux tests sur les MÊMES données ; ces regards "
            "n'inflatent pas le taux de faux positifs d'un test mené sur une période "
            "non vue. Ils inflatent la SÉLECTION, qui se paie en promouvant peu de "
            "finalistes sur un échantillon frais, pas en relevant le seuil deux fois."),
        "n_hypotheses": 0,
        "hypotheses": [],
        "n_registered_cumulative": total_before,
        "threshold_t": round(threshold_t(max(1, total_before)), 4),
        "alpha_family": FAMILY_ALPHA,
        "one_sided": ONE_SIDED,
        "correction": "bonferroni_sur_le_compte_cumule",
        "notes": str(notes),
        "prev_hash": previous,
    }
    payload["record_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str
                   ).encode()).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                default=str) + "\n")
    return payload


def burned_periods(family: str, path: Path = LEDGER) -> List[Tuple[str, str]]:
    """Les périodes qu'on ne peut plus sceller comme vierges pour cette famille.

    À consulter par TOUTE fonction de scellement — c'est la porte physique que
    la Phase 1 doit poser. Sans elle, la déclaration de contamination est un
    commentaire.
    """
    target = Path(path)
    if not target.is_file():
        return []
    out: List[Tuple[str, str]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("record_type") != "CONTAMINATION":
            continue
        if str(record.get("family")) != str(family):
            continue
        out.extend((str(a), str(b)) for a, b in record.get("burned_periods", []))
    return out


def verify_ledger(path: Path = LEDGER) -> Dict[str, object]:
    """La chaîne tient-elle, et le seuil de chaque lot est-il celui qu'il devait ?"""
    target = Path(path)
    if not target.is_file():
        return {"ok": True, "batches": 0, "n_registered": 0}
    previous, seen = GENESIS_HASH, 0
    batches = 0
    for index, line in enumerate(target.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        record = json.loads(line)
        expected = dict(record)
        digest = expected.pop("record_hash")
        if str(record["prev_hash"]) != previous:
            return {"ok": False, "error": "chaîne rompue au lot %d" % index}
        recomputed = hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":"),
                       default=str).encode()).hexdigest()
        if recomputed != digest:
            return {"ok": False, "error": "lot %d modifié après scellement" % index}
        seen += len(record.get("hypotheses", []))
        # Comparaison à l'arrondi STOCKÉ, pas à la valeur brute : le ledger
        # garde 4 décimales pour rester lisible, et comparer un arrondi à une
        # valeur pleine ferait échouer la vérification sur chaque lot honnête.
        if abs(float(record["threshold_t"]) - round(threshold_t(max(1, seen)), 4)) > 1e-9:
            return {"ok": False,
                    "error": "lot %d porte un seuil qui ne correspond pas à son compte "
                             "cumulé (%s au lieu de %.4f)"
                             % (index, record["threshold_t"], threshold_t(seen))}
        previous = digest
        batches += 1
    return {"ok": True, "batches": batches, "n_registered": seen,
            "head_hash": previous, "current_threshold_t": round(threshold_t(seen), 4) if seen else None}
