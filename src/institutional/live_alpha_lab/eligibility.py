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
  2b. sa spec est établie          (scientific_status ∉ UNRESOLVED_SPEC_SCIENTIFIC_STATUSES)
  4. il est EXÉCUTABLE             (latence médiane récente <= son propre horizon)
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

# scientific_status où la SPEC elle-même n'est pas établie (item C2).
#
# RECONSTRUCTED veut dire : un seuil ou une fenêtre a dû être reconstruit
# faute de constante publiée. Le registre le documente déjà honnêtement, et va
# jusqu'à écrire que l'`expected_net_bps` correspondant est « un contexte
# historique, PAS une cible de confirmation forward ». Mais rien n'en tirait
# la conséquence côté CAPITAL.
#
# Le trou, précisément : SHORT_COVERING_CONTINUATION_V1 est RECONSTRUCTED et
# porte 57 % du PnL attribué. Il ne reçoit aujourd'hui aucun capital -- mais
# par accident du registre de validation (BLOCK_NOT_VALIDATED_FOR_FORWARD),
# pas à cause de son statut. Le jour où un candidat le validerait, il
# recevrait du capital en restant RECONSTRUCTED, et sa preuve forward
# reposerait sur une spec reconstruite à partir des observations qui servent
# à la juger.
#
# Constaté en creusant (2026-09-06) : aucun commit de
# engines/short_covering_continuation/{state,infer}.py n'est ANTÉRIEUR à son
# freeze -- ils sont commités 4 h 25 après, et 45 min après sa première
# décision forward. `alpha_spec_hash` est bien constant sur les 417 décisions,
# mais il hache l'ENTRÉE DU REGISTRE, pas le CODE : il prouve que la
# déclaration n'a pas bougé, pas que l'implémentation n'a pas bougé.
# `working_tree_dirty` vaut True sur 348/417, et l'empreinte qui saurait dire
# si ce sont les chemins de décision qui étaient sales
# (`dirty_decision_paths_sha1`) n'existe que depuis le 2026-09-05 -- nulle sur
# 336 des 417.
#
# Porte SÉPARÉE de NO_CAPITAL_SCIENTIFIC_STATUSES parce que la raison est
# différente : là le mécanisme est mort, ici il est peut-être bon mais sa
# spec n'est pas établie. Deux motifs distincts, deux codes de refus
# distincts, jamais un seul fourre-tout.
UNRESOLVED_SPEC_SCIENTIFIC_STATUSES = frozenset(("RECONSTRUCTED",))

# Le placebo (item D3). Un alpha à signal aléatoire ne doit JAMAIS recevoir de
# capital, et surtout pas pouvoir être promu par une édition du registre de
# validation. D'où une porte propre, placée AVANT toute consultation de ce
# registre : le refus ne dépend d'aucune donnée éditable ailleurs.
PLACEBO_SCIENTIFIC_STATUSES = frozenset(("PLACEBO",))

# Le seul statut du VALIDATION_REGISTRY qui autorise du capital forward.
VALIDATED_STATUS = "VALIDATED_FOR_FORWARD"

# Horizons connus, en heures — même table que le scoreboard, dupliquée
# volontairement pour garder cette porte utilisable sans lui.
_HORIZON_HOURS = {"fwd_4h": 4.0, "fwd_24h": 24.0, "24h": 24.0,
                  "fwd_7d": 168.0, "k30d": 720.0}


class EligibilityReason(str, Enum):
    """Pourquoi un alpha reçoit — ou ne reçoit pas — du capital forward.
    Toujours explicite : jamais un `continue` muet dans une boucle."""
    ELIGIBLE_VALIDATED = "ELIGIBLE_VALIDATED"
    NOT_A_POSITION_ALPHA = "NOT_A_POSITION_ALPHA"
    BLOCK_NOT_OPERATIONAL = "BLOCK_NOT_OPERATIONAL"
    BLOCK_SCIENTIFIC_STATUS = "BLOCK_SCIENTIFIC_STATUS"
    BLOCK_UNRESOLVED_SPEC = "BLOCK_UNRESOLVED_SPEC"
    BLOCK_NO_VALIDATION_RECORD = "BLOCK_NO_VALIDATION_RECORD"
    BLOCK_NOT_VALIDATED_FOR_FORWARD = "BLOCK_NOT_VALIDATED_FOR_FORWARD"
    BLOCK_NOT_EXECUTABLE = "BLOCK_NOT_EXECUTABLE"
    BLOCK_PLACEBO = "BLOCK_PLACEBO"


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


# Fenêtre de la mesure de latence. Même valeur que RECENT_WINDOW_HOURS du
# scoreboard, et pour la même raison : le CUMUL inclut les rattrapages
# historiques (des décisions nées périmées lors d'un backfill) et ne redescend
# jamais. Un indicateur qui ne redescend pas après un incident condamnerait à
# vie un alpha réparé depuis — l'exact opposé de ce qu'une porte doit faire.
RECENT_LAG_WINDOW_HOURS = 24.0

# ── Hygiène du registre (item C3) ───────────────────────────────────────────
# operational_status qui signifient « ce candidat ne tourne pas ».
NOT_RUNNING_OPERATIONAL_STATUSES = frozenset(("CODE_MISSING", "DATA_BLOCKED"))

# Au-delà, un candidat qui ne tourne toujours pas doit avoir été TRANCHÉ :
# implémenté, ou retiré avec motif. 30 jours, c'est-à-dire largement au-delà
# du temps d'écrire un runner ; ce n'est pas une contrainte de délai, c'est un
# garde-fou contre l'oubli.
#
# Le mécanisme n'édite PAS le registre tout seul. Une mutation automatique
# d'un registre scientifique est exactement ce que ce projet évite : elle
# ferait disparaître une décision humaine dans un cron. À la place, un test
# ÉCHOUE tant que la décision n'est pas écrite. Un registre qui liste des
# alphas sans code dilue la lecture du scoreboard ; un test rouge, non.
STALE_UNIMPLEMENTED_DAYS = 30

# Le statut à écrire quand on tranche pour le retrait. On ne SUPPRIME pas
# l'entrée -- on la marque, même règle que les 7 labs retirés d'alpha_foundry_v5 :
# supprimer effacerait la trace qu'un mécanisme a été envisagé et écarté.
RETIRED_STATUS = "RETIRED_NOT_IMPLEMENTED"


def stale_unimplemented(alphas, now=None, threshold_days: int = STALE_UNIMPLEMENTED_DAYS):
    """Candidats qui ne tournent pas depuis plus de `threshold_days`, et dont
    le retrait n'a pas été tranché.

    Fonction PURE d'une liste d'entrées de registre. Renvoie une liste de
    dicts {alpha_id, operational_status, days, since} — vide si tout est en
    règle. Une entrée sans `operational_status_since` est signalée elle aussi :
    un état sans date est un état sans durée, donc invisible à ce contrôle,
    ce qui serait la façon la plus simple de le contourner."""
    import pandas as pd
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    out = []
    for a in alphas:
        op = a.get("operational_status")
        if op not in NOT_RUNNING_OPERATIONAL_STATUSES:
            continue
        if a.get("retirement_decision"):
            continue          # tranché explicitement : plus rien à signaler
        since = a.get("operational_status_since")
        if not since:
            out.append({"alpha_id": a.get("alpha_id"), "operational_status": op,
                        "days": None, "since": None,
                        "detail": "operational_status_since absent — état sans durée, "
                                  "donc invisible à ce contrôle"})
            continue
        days = (now - pd.Timestamp(since)).total_seconds() / 86400.0
        if days > threshold_days:
            out.append({"alpha_id": a.get("alpha_id"), "operational_status": op,
                        "days": round(days, 1), "since": since,
                        "detail": f"{op} depuis {days:.0f} jours (> {threshold_days}) : "
                                  f"implémenter, ou écrire `retirement_decision` avec motif "
                                  f"et passer operational_status à {RETIRED_STATUS}"})
    return out



def recent_decision_lag_median_h(decisions, time_col: str,
                                 window_hours: float = RECENT_LAG_WINDOW_HOURS,
                                 now=None) -> Optional[float]:
    """Latence médiane `decided_at - event_time`, en heures, sur les décisions
    FORWARD_LIVE PRISES dans la fenêtre récente.

    Fonction de MESURE, pas de décision : elle ne lit aucun fichier, elle prend
    le DataFrame qu'on lui donne, exactement comme `is_forward_eligible` prend
    le résultat. Renvoie None quand la mesure est impossible — jamais 0, qui se
    lirait comme « latence nulle », c'est-à-dire la valeur la plus permissive
    de toutes."""
    import pandas as pd
    if decisions is None or len(decisions) == 0:
        return None
    if time_col not in decisions.columns or "decided_at" not in decisions.columns:
        return None
    df = decisions
    if "provenance" in df.columns:
        df = df[df["provenance"] == "FORWARD_LIVE"]
    if df.empty:
        return None
    decided = pd.to_datetime(df["decided_at"], utc=True, errors="coerce")
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    recent = decided >= (now - pd.Timedelta(hours=window_hours))
    if not bool(recent.any()):
        return None
    lag = ((decided[recent]
            - pd.to_datetime(df.loc[recent, time_col], utc=True, errors="coerce"))
           .dt.total_seconds() / 3600.0).dropna()
    return None if lag.empty else round(float(lag.median()), 2)


def is_forward_eligible(alpha: dict,
                        validation_index: Optional[Dict[str, List[ValidationLink]]] = None,
                        position_alpha: bool = True,
                        decision_lag_median_h: Optional[float] = None) -> ForwardEligibility:
    """Porte CENTRALE : cet alpha a-t-il le droit de recevoir du capital forward ?

    `alpha` est une entrée de `configs/live_alpha_registry.yaml`.
    `position_alpha=False` pour un gate/overlay (ne consomme pas de capital).

    `decision_lag_median_h` (item C1) : latence RÉCENTE mesurée entre
    l'événement et la décision. Passer la mesure sur fenêtre glissante, pas le
    cumul — le cumul inclut les rattrapages historiques et ne redescend jamais,
    donc il condamnerait à vie un alpha réparé depuis. Le paramètre est une
    ENTRÉE et non une lecture de ledger : la fonction reste pure, donc testable
    et reproductible, et l'appelant reste responsable de la mesure.

    Aucun effet de bord : ne lit pas de ledger, n'écrit rien, ne coupe aucune
    collecte. Pure fonction du registre live + du registre de validation + de
    la latence qu'on lui donne.
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

    if sci in PLACEBO_SCIENTIFIC_STATUSES:
        return ForwardEligibility(
            alpha_id, False, EligibilityReason.BLOCK_PLACEBO,
            "signal aléatoire (contrôle) : jamais de capital, par construction. "
            "Il traverse la même chaîne de mesure que les vrais alphas — c'est "
            "sa raison d'être — mais il ne prend aucune position.")

    if sci in UNRESOLVED_SPEC_SCIENTIFIC_STATUSES:
        return ForwardEligibility(
            alpha_id, False, EligibilityReason.BLOCK_UNRESOLVED_SPEC,
            f"scientific_status={sci!r} -> spec reconstruite (seuil/fenêtre non publiés) : "
            f"pas de capital tant que le statut n'est pas résolu. Un alpha dont la spec a "
            f"été reconstruite À PARTIR des observations qui servent à le juger ne peut pas "
            f"produire une preuve forward jamais-vue.")

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

    # ── porte d'EXÉCUTABILITÉ (item C1) ────────────────────────────────────
    # Un alpha dont la latence médiane dépasse son propre horizon de détention
    # ne peut PAS recevoir de capital : le temps que la décision arrive, la
    # position serait déjà à liquider. Le système imprimait jusqu'ici cette
    # contradiction — `VALIDATED_FOR_FORWARD` + `eligible: true` + 100 % de
    # décisions périmées à l'arrivée — sans jamais la refuser. Mesurer un
    # défaut et continuer à allouer dessus, c'est le documenter, pas le
    # corriger.
    #
    # Latence INCONNUE ne bloque pas : au démarrage d'un alpha il n'y a aucune
    # décision forward, donc aucune latence mesurable, et fail-closed ici
    # empêcherait tout nouvel alpha de démarrer. Le fail-closed a déjà lieu en
    # amont (validation) ; celui-ci refuse ce qui est MESURÉ inexécutable.
    horizon_h = _HORIZON_HOURS.get(alpha.get("horizon"))
    if decision_lag_median_h is not None and horizon_h and decision_lag_median_h > horizon_h:
        return ForwardEligibility(
            alpha_id, False, EligibilityReason.BLOCK_NOT_EXECUTABLE,
            f"latence médiane récente {decision_lag_median_h:.1f}h > horizon "
            f"{alpha.get('horizon')} ({horizon_h:.0f}h) : la décision arrive après "
            f"l'expiration de sa propre position. Validé n'est pas exécutable.", links)

    return ForwardEligibility(
        alpha_id, True, EligibilityReason.ELIGIBLE_VALIDATED,
        "validé par " + ", ".join(l.candidate_id for l in granting), links)


def eligibility_report(registry_path: Optional[Path] = None,
                       validation_path: Optional[Path] = None,
                       not_position_alphas: frozenset = frozenset(),
                       decision_lag_median_h: Optional[Dict[str, float]] = None,
                       ) -> List[ForwardEligibility]:
    """Verdict pour TOUS les alphas du registre live — utilisé par le runner
    (log par alpha, jamais un skip silencieux) et par les rapports d'audit.

    `decision_lag_median_h` : alpha_id -> latence récente mesurée. Omis, la
    porte d'exécutabilité ne s'applique pas (voir is_forward_eligible)."""
    reg = yaml.safe_load(Path(registry_path or LIVE_REGISTRY).read_text()) or {}
    index = load_validation_index(validation_path)
    lags = decision_lag_median_h or {}
    return [
        is_forward_eligible(a, index,
                            position_alpha=a.get("alpha_id") not in not_position_alphas,
                            decision_lag_median_h=lags.get(a.get("alpha_id")))
        for a in reg.get("alphas", []) or []
    ]
