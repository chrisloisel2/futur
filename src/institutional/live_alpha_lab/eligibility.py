"""
src/institutional/live_alpha_lab/eligibility.py
─────────────────────────────────────────────────────────────────────────────
FORWARD_CAPITAL_ELIGIBILITY — porte UNIQUE et centrale qui décide si un alpha
a le droit de recevoir du CAPITAL forward.

Pourquoi ce module existe (root cause, audit forward 2026-09-04)
────────────────────────────────────────────────────────────────
`scripts/run_portfolio_shadow.py` ne consultait QUE
`configs/live_alpha_registry.yaml` (le registre opérationnel : est-ce que le
code tourne, quel est le statut scientifique interne de l'alpha). Il ne
consultait JAMAIS `configs/validation_registry.yaml` (le verdict de l'usine
de validation indépendante : est-ce que ce mécanisme a passé le gate).

Conséquence mesurée : SHORT_COVERING_CONTINUATION_V1 portait 100 % du capital
des 5 portefeuilles shadow alors que son candidat de validation porte
`validated_for_forward: false`, `current_status: NEEDS_MORE_RESEARCH`,
`validation_net_bps: 2.53` (t=0.41, bootstrap p05 négatif), `-11.47` au coût
de stress, tags COST_FRAGILE / MECHANISM_CONFIRMED_PRODUCT_NOT, et la phrase
explicite « le produit long autonome ne bat pas zéro […] sa valeur est celle
d'un signal RELATIF, pas d'un long directionnel ». Les deux registres se
contredisaient et RIEN dans le code ne les confrontait.

Règle
─────
Un alpha ne reçoit du capital forward que si TOUTES ces conditions tiennent :

  1. il tourne réellement          (operational_status ∈ CAPITAL_OPERATIONAL_STATUSES)
  2. son mécanisme n'est pas mort  (scientific_status ∉ NO_CAPITAL_SCIENTIFIC_STATUSES)
  3. il est relié à ≥1 candidat du VALIDATION_REGISTRY
  4. ≥1 de ces candidats porte `validated_for_forward: true`
     ET `current_status: VALIDATED_FOR_FORWARD`

FAIL CLOSED : l'absence de preuve de validation n'est PAS une preuve
d'absence de problème. Un alpha sans entrée dans le registre de validation
est bloqué (BLOCK_NO_VALIDATION_RECORD), pas laissé passer par défaut.

Ce que cette porte NE fait PAS
──────────────────────────────
Elle ne coupe JAMAIS la collecte. Un alpha bloqué continue de tourner, de
produire des décisions et de les écrire dans son `decisions.parquet` — c'est
la seule façon d'accumuler la preuve forward qui lui manque. Elle ne touche
pas non plus `operational_status` (question orthogonale : le code tourne).

Gates et overlays (WHALE_LSR_SCREEN_V1, VOL_FORECAST_LAYER_V1) ne sont PAS
soumis à cette porte : ils ne consomment pas de capital, ils en RETIRENT
(un screen ne peut que réduire une position, un overlay de vol ne peut que
réduire le sizing). Les bloquer augmenterait le risque au lieu de le réduire.
Voir `intents.NOT_A_POSITION_ALPHA`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[3]
LIVE_REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
VALIDATION_REGISTRY = ROOT / "configs" / "validation_registry.yaml"

# operational_status qui signifient « le code tourne réellement ».
CAPITAL_OPERATIONAL_STATUSES = frozenset(("SIGNAL_SHADOW", "EXECUTION_SHADOW"))

# scientific_status pour lesquels le mécanisme est mort ou en attente de respec :
# le producteur continue de tourner (collecte forward jamais coupée), mais plus
# aucun portefeuille ne lui alloue de capital. Historiquement la SEULE porte
# existante -- conservée telle quelle, elle reste correcte, elle était juste
# très insuffisante à elle seule.
NO_CAPITAL_SCIENTIFIC_STATUSES = frozenset(("REJECTED", "INVALIDATED",
                                            "INVALIDATED_PENDING_RESPEC"))

# Le seul statut du VALIDATION_REGISTRY qui autorise du capital forward.
VALIDATED_STATUS = "VALIDATED_FOR_FORWARD"


class EligibilityReason(str, Enum):
    """Pourquoi un alpha reçoit — ou ne reçoit pas — du capital forward.
    Toujours explicite : jamais un `continue` muet dans une boucle."""
    ELIGIBLE_VALIDATED = "ELIGIBLE_VALIDATED"
    NOT_A_POSITION_ALPHA = "NOT_A_POSITION_ALPHA"
    BLOCK_NOT_OPERATIONAL = "BLOCK_NOT_OPERATIONAL"
    BLOCK_SCIENTIFIC_STATUS = "BLOCK_SCIENTIFIC_STATUS"
    BLOCK_NO_VALIDATION_RECORD = "BLOCK_NO_VALIDATION_RECORD"
    BLOCK_NOT_VALIDATED_FOR_FORWARD = "BLOCK_NOT_VALIDATED_FOR_FORWARD"


@dataclass(frozen=True)
class ValidationLink:
    """Un candidat du VALIDATION_REGISTRY relié à un alpha_id du registre live."""
    candidate_id: str
    current_status: Optional[str]
    validated_for_forward: Optional[bool]
    validation_net_bps: Optional[float] = None

    @property
    def grants_capital(self) -> bool:
        """Les DEUX champs doivent concorder. Le registre est cohérent
        aujourd'hui (vérifié : 0 incohérence sur 35 candidats), mais exiger
        les deux évite qu'une édition manuelle d'un seul champ n'ouvre la
        porte par accident."""
        return self.validated_for_forward is True and self.current_status == VALIDATED_STATUS


@dataclass(frozen=True)
class ForwardEligibility:
    alpha_id: str
    eligible: bool
    reason: EligibilityReason
    detail: str
    links: Tuple[ValidationLink, ...] = ()

    def __bool__(self) -> bool:
        return self.eligible

    def as_dict(self) -> dict:
        return {
            "alpha_id": self.alpha_id,
            "eligible": self.eligible,
            "reason": self.reason.value,
            "detail": self.detail,
            "validation_candidates": [
                {"candidate_id": l.candidate_id, "current_status": l.current_status,
                 "validated_for_forward": l.validated_for_forward,
                 "validation_net_bps": l.validation_net_bps}
                for l in self.links
            ],
        }


def load_validation_index(path: Optional[Path] = None) -> Dict[str, List[ValidationLink]]:
    """alpha_id (registre live) -> candidats du VALIDATION_REGISTRY qui le visent.

    Clé de jointure : `frozen_alpha_id` (le candidat A ÉTÉ figé sous cet
    alpha_id — cas AMIHUD/BTC_LEAD) OU `existing_live_alpha` (le candidat
    décrit un alpha qui existait DÉJÀ — cas SHORT_COVERING, LIQ_REPEAT…).
    Même jointure que `scripts/compute_validation_scoreboard.py`, pour que le
    scoreboard et la porte de capital ne puissent pas diverger.

    Un même alpha_id peut recevoir PLUSIEURS candidats (LIQ_CASCADE_REPEAT_V1
    en a 4 : deux validés, deux non). C'est voulu : plusieurs mécanismes
    distincts peuvent avoir été testés contre la même implémentation live.
    """
    path = path or VALIDATION_REGISTRY
    raw = yaml.safe_load(Path(path).read_text()) or {}
    index: Dict[str, List[ValidationLink]] = {}
    for cand in raw.get("candidates", []) or []:
        key = cand.get("frozen_alpha_id") or cand.get("existing_live_alpha")
        if not key:
            continue   # candidat en amont de tout alpha_id : rien à relier
        index.setdefault(key, []).append(ValidationLink(
            candidate_id=cand.get("candidate_id", "<sans id>"),
            current_status=cand.get("current_status"),
            validated_for_forward=cand.get("validated_for_forward"),
            validation_net_bps=cand.get("validation_net_bps"),
        ))
    return index


def is_forward_eligible(alpha: dict,
                        validation_index: Optional[Dict[str, List[ValidationLink]]] = None,
                        position_alpha: bool = True) -> ForwardEligibility:
    """Porte CENTRALE : cet alpha a-t-il le droit de recevoir du capital forward ?

    `alpha` est une entrée de `configs/live_alpha_registry.yaml`.
    `position_alpha=False` pour un gate/overlay (ne consomme pas de capital).

    Aucun effet de bord : ne lit pas de ledger, n'écrit rien, ne coupe aucune
    collecte. Pure fonction du registre live + du registre de validation, donc
    testable et reproductible.
    """
    alpha_id = alpha.get("alpha_id", "<sans alpha_id>")

    if not position_alpha:
        return ForwardEligibility(
            alpha_id, True, EligibilityReason.NOT_A_POSITION_ALPHA,
            "gate/overlay : ne consomme pas de capital (il en retire), porte non applicable")

    op = alpha.get("operational_status")
    if op not in CAPITAL_OPERATIONAL_STATUSES:
        return ForwardEligibility(
            alpha_id, False, EligibilityReason.BLOCK_NOT_OPERATIONAL,
            f"operational_status={op!r} hors {sorted(CAPITAL_OPERATIONAL_STATUSES)}")

    sci = alpha.get("scientific_status")
    if sci in NO_CAPITAL_SCIENTIFIC_STATUSES:
        return ForwardEligibility(
            alpha_id, False, EligibilityReason.BLOCK_SCIENTIFIC_STATUS,
            f"scientific_status={sci!r} -> mécanisme mort ou en attente de respec")

    index = load_validation_index() if validation_index is None else validation_index
    links = tuple(index.get(alpha_id, ()))

    if not links:
        return ForwardEligibility(
            alpha_id, False, EligibilityReason.BLOCK_NO_VALIDATION_RECORD,
            "aucun candidat du VALIDATION_REGISTRY ne vise cet alpha_id "
            "(ni frozen_alpha_id ni existing_live_alpha) -- fail closed : "
            "absence de preuve != preuve d'absence de problème", links)

    granting = [l for l in links if l.grants_capital]
    if not granting:
        summary = ", ".join(
            f"{l.candidate_id}={l.current_status}/validated_for_forward={l.validated_for_forward}"
            for l in links)
        return ForwardEligibility(
            alpha_id, False, EligibilityReason.BLOCK_NOT_VALIDATED_FOR_FORWARD,
            f"aucun candidat validé pour le forward ({summary})", links)

    return ForwardEligibility(
        alpha_id, True, EligibilityReason.ELIGIBLE_VALIDATED,
        "validé par " + ", ".join(l.candidate_id for l in granting), links)


def eligibility_report(registry_path: Optional[Path] = None,
                       validation_path: Optional[Path] = None,
                       not_position_alphas: frozenset = frozenset()) -> List[ForwardEligibility]:
    """Verdict pour TOUS les alphas du registre live — utilisé par le runner
    (log par alpha, jamais un skip silencieux) et par les rapports d'audit."""
    reg = yaml.safe_load(Path(registry_path or LIVE_REGISTRY).read_text()) or {}
    index = load_validation_index(validation_path)
    return [
        is_forward_eligible(a, index, position_alpha=a.get("alpha_id") not in not_position_alphas)
        for a in reg.get("alphas", []) or []
    ]
