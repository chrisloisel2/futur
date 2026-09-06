# Les mêmes alphas, recomptés sous trois définitions d'épisode

_Généré par `scripts/audit_decluster_versions.py`. Ne pas éditer à la main._

Trois alphas ont été déclarés « indécidables » sous le decluster v1. La question
est de savoir s'ils l'étaient vraiment, ou si l'appareil était seulement plus
myope qu'il n'avait besoin de l'être. **Rien n'est réécrit ici** : v1 reste la
colonne de référence, puisque c'est sous elle que tous les verdicts déjà publiés
ont été rendus.

## Les trois définitions

| version | règle |
|---|---|
| `v1_single_linkage` | lien simple — un épisode s'étend tant que l'écart au point PRÉCÉDENT reste sous la fenêtre. Chaîne indéfiniment. |
| `v2_complete_linkage` | lien complet — un épisode s'étend tant que l'écart à son PROPRE DÉBUT reste sous la fenêtre. Durée bornée par la fenêtre, pas de chaînage. |
| `v2_fixed_windows` | fenêtres fixes non recouvrantes ancrées sur l'époque — pas de chaînage non plus, mais la phase des bornes est arbitraire. |

Aucune n'est « la vraie ». v1 sous-estime toujours l'indépendance ; les fenêtres
fixes la surestiment quand deux décisions tombent de part et d'autre d'une borne ;
le lien complet est entre les deux et ne dépend d'aucune phase. Publier les trois
est la seule lecture qui ne cache pas le choix.

## Épisodes, par alpha et par définition

| alpha | décisions | v1 | v2 lien complet | v2 fenêtres fixes | gain v2/v1 |
|---|---|---|---|---|---|
| `LIQ_CASCADE_REPEAT_V1` | 36 | 18 | **20** | 25 | ×1.1 |
| `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` | 13 | 8 | **8** | 11 | ×1.0 |
| `LIQ_CASCADE_FAR_FROM_LOW_V1` | 75 | 43 | **45** | 48 | ×1.0 |
| `BTC_LEAD_ALT_CASCADE_V1` | 0 | 1 | **1** | 1 | ×1.0 |
| `SHORT_COVERING_CONTINUATION_V1` | 436 | 104 | **168** | 219 | ×1.6 |
| `PLACEBO_RANDOM_V1` ⚙️ | 131 | 46 | **46** | 46 | ×1.0 |
| `POSITIVE_CONTROL_ORACLE_V1` ⚙️ | 1408 | 860 | **952** | 1153 | ×1.1 |

## Ce que ça change aux verdicts

Rappel de la cible : **+29 bps d'excess** avant coût (= +15 bps nets). Un alpha
« tranche » quand la borne haute de son intervalle passe sous cette cible.

| alpha | v1 : IC / verdict | v2 : IC / verdict | statut |
|---|---|---|---|
| `LIQ_CASCADE_REPEAT_V1` | [-92.2, +52.6] 🟠 | [-84.6, +47.5] 🟠 | inchangé — toujours indécidable |
| `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` | [-122.4, +67.9] 🟠 | [-122.4, +67.9] 🟠 | inchangé — toujours indécidable |
| `LIQ_CASCADE_FAR_FROM_LOW_V1` | [-39.0, +48.3] 🟠 | [-46.8, +58.8] 🟠 | inchangé — toujours indécidable |
| `BTC_LEAD_ALT_CASCADE_V1` | n trop faible | n trop faible | ⚪ rien à lire |
| `SHORT_COVERING_CONTINUATION_V1` | [-15.9, +18.7] ✅ | [-21.4, +20.1] ✅ | inchangé — tranchait déjà |

_✅ = l'intervalle exclut la cible de +29 bps. 🟠 = il ne l'exclut pas._

**Aucun alpha ne change de statut.** Les indécidables le restent sous les trois
définitions : leur intervalle n'exclut la cible sous aucune façon de compter.
Le gain de puissance de v2 est réel mais insuffisant, ce qui déplace la
conclusion — ce n'était pas la règle de comptage, c'est la quantité de
collecte.

## Ce que ce recomptage ne fait pas

**Il ne valide aucun alpha.** Passer de « indécidable » à « tranche » ne veut dire
qu'une chose : l'intervalle exclut désormais la cible. Dans tous les cas mesurés
ici, il l'exclut **par le bas** — c'est un refus mieux fondé, pas une découverte.

**Il ne change aucun verdict déjà publié.** Le scoreboard, l'item A1 et le
contrôle positif restent calculés sous v1. Les deux colonnes coexistent pour
qu'on sache toujours sous quelle règle une décision a été prise.

**Il ne rend pas v2 obligatoire pour la suite.** Le choix de la définition doit
être scellé AVANT de regarder un résultat, comme le reste — sinon choisir la
définition qui donne le plus d'épisodes est un essai de plus, non compté.

