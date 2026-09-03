# Décisions de déploiement paper — candidats VALIDATED_FOR_FORWARD

Date : 2026-09-03. Source des verdicts :
`reports/edge_discovery/validation_2026-09/VALIDATION_AND_FORWARD_SCOREBOARD.md`
et `configs/validation_registry.yaml`.

Trois candidats portaient `validated_for_forward: true` au 2026-09-02. Un
candidat validé n'est pas automatiquement un alpha déployable : la validation
répond à « le mécanisme est-il réel ? », pas à « existe-t-il un véhicule
d'exécution ? ». Les trois ont donc été tranchés séparément.

| candidat | validé le | décision | alpha_id déployé |
|---|---|---|---|
| AMIHUD_ILLIQUIDITY_PREMIUM | 2026-09-02 | **déployé** (l'était déjà, mais ne tournait pas) | `AMIHUD_ILLIQUIDITY_PREMIUM_V1` |
| LIQ_REPEAT_DENSITY | 2026-09-02 | **déployé** (nouveau code) | `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` |
| LIQ_REPEAT_SKEW_OVERLAY | 2026-09-02 | **NON déployé** — pas de véhicule d'exécution | — |

---

## 1. AMIHUD_ILLIQUIDITY_PREMIUM — était figé mais ne tournait pas

Figé le 2026-09-02T11:20:10Z et décrit comme « lancé en forward ». En réalité,
au 2026-09-03 son ledger ne contenait que **24 358 décisions REPLAY s'arrêtant
au 2026-08-26, et zéro décision FORWARD_LIVE** : le runner n'avait tourné qu'une
fois, à la main, et aucune unité systemd ne le relançait.

Cause racine, commune à tout le laboratoire : les 9 producteurs de signal et la
couche portefeuille n'étaient couverts par **aucun timer**. La décision forward
la plus fraîche de tout le laboratoire datait de 38 heures. Un forward-test qui
ne tourne pas n'accumule pas de preuve — l'ETA de confirmation était donc
infini, pas 17 ans.

Corrigé par `scripts/run_live_alpha_lab_cycle.py` + `futur-live-alpha-lab.timer`
(cycle toutes les 15 min). Aucune modification de spec.

## 2. LIQ_REPEAT_DENSITY — déployé comme LIQ_CASCADE_REPEAT_SYSTEMIC_V1

Validé mais sans aucun code : « validé » depuis la veille et incapable
d'accumuler la moindre preuve forward. Implémenté à l'identique de la spec du
validateur (`density_variant.py`), avec le seuil de densité **figé en dur**
(médiane = 1) plutôt que recalculé au runtime — une médiane recalculée ferait
dériver la spec en silence sous le freeze_timestamp.

Contrôle de fidélité à la réimplémentation : sur l'historique complet, le filtre
retient 3 653 des 5 675 événements exhaustion, soit **35,6 % d'événements
isolés** — à comparer aux **33,8 % de lignes à densité nulle** rapportées par le
validateur sur sa propre population. Les deux constructions concordent.

Tourne délibérément **à côté** de son parent `LIQ_CASCADE_REPEAT_V1`, pas à sa
place : le parent trade les deux régimes (systémique +22,1bps, isolé −13,4bps),
la variante ne trade que le premier. L'écart entre les deux ledgers forward est
la mesure directe de la valeur du filtre, sur données jamais vues.

Réserve reportée telle quelle dans le registre : ETA de reconfirmation ~9,4 ans.
Le monitoring forward doit juger sur la survie du signe, du coût et du mécanisme,
**pas** sur une significativité fraîche.

## 3. LIQ_REPEAT_SKEW_OVERLAY — validé, mais délibérément NON déployé

Le rapport de validation conclut `VALIDATED_FOR_FORWARD = TRUE` **avec conditions**,
dont la n°5 est disqualifiante pour un déploiement en position :

> « No direct execution vehicle exists (confirmed) — usable only as a
> filter/monitoring input on `LIQ_CASCADE_REPEAT_V1`'s **onset** events, not
> validated as a direct sizing input on the exhaustion trade itself. »

Ce que le mécanisme prédit est la **probabilité qu'une cascade onset se
répète** — pas un rendement. Le transformer en signal de sizing sur le trade
d'exhaustion reviendrait à déployer quelque chose que la validation dit
explicitement ne pas avoir validé. C'est exactement le glissement que la
discipline du projet interdit.

S'y ajoutent : correction de signe obligatoire par rapport à la découverte
(c'est le skew **complaisant**, bas quartile, qui marque le régime à forte
probabilité de répétition — l'inverse de ce qu'annonçait le rapport d'origine) ;
ETA ~11,4 ans à effet plein, ~46 ans avec haircut 50 % ; effet nettement plus
faible en 2025 ; et 17,6 % de l'historique de cascades sans donnée de skew.

**Décision** : pas d'alpha_id, pas de position. Reste consultable comme lead.
Le remettre en discussion demanderait d'abord de démontrer un véhicule
d'exécution — ce que ce rapport dit ne pas exister.
