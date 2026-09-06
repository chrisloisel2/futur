# Le reste de l'audit — A3, A4, B2, C3, D2, E4

---

## D2 — la déflation par le nombre d'essais : le résultat le plus dur du lot

Les campagnes edge_discovery ont testé des centaines de mécanismes ; deux candidats en sont
sortis validés. Leurs t-stats étaient évalués contre le null d'**un** essai, alors qu'ils sont
le **maximum de centaines**. Un programme qui teste assez de mécanismes finit mécaniquement
par en produire un qui a l'air significatif.

`scripts/build_multiplicity_ledger.py` compte les essais à partir des artefacts sur disque, en
enregistrant pour chaque compte **sa méthode d'extraction** — un chiffre sans provenance ne
vaut pas mieux qu'un souvenir.

| | |
|---|---|
| mécanismes comptés | **904** |
| workers sans compte machine-lisible | **27** (comptés pour zéro) |
| campagnes couvertes | 4 |

**Le total est une BORNE BASSE**, et c'est important dans le mauvais sens : un compte
sous-estimé produit un haircut sous-estimé, donc les verdicts ci-dessous sont **optimistes**.

### Le verdict

| candidat | t observé | seuil E[max] à N=904 | t déflaté | p | verdict |
|---|---|---|---|---|---|
| BTC_LEAD_ALT_CASCADE | 3,315 | **3,226** | **+0,089** | **0,535** | survit de justesse |
| LIQ_REPEAT_DENSITY | — | — | — | — | *aucune dispersion conservée* |
| LIQ_REPEAT_SKEW_OVERLAY | — | — | — | — | *idem* |
| AMIHUD_ILLIQUIDITY_PREMIUM | — | — | — | — | *idem* |

**BTC_LEAD_ALT_CASCADE passe à p = 0,535, c'est-à-dire à pile ou face** — et contre une borne
basse issue de 7 workers sur 10, d'**une** campagne sur quatre. Le seuil monte à 3,447 dès
N = 2 000, un compte encore conservateur pour quatre campagnes : au-delà, il ne survit plus du
tout.

**Trois candidats validés sur quatre n'ont ni t-stat ni intervalle bootstrap conservé.** Le
registre garde `validation_net_bps` et `n_validation_independent`, mais aucune mesure de
dispersion — on ne peut pas déflater ce qu'on ne peut pas mesurer. Ce n'est pas une absence de
risque, c'est un défaut de traçabilité.

### Méthode, et pourquoi elle est vérifiée plutôt que supposée

`src/institutional/live_alpha_lab/multiplicity.py` calcule la **composante multiplicité** du
DSR : E[max] de N tirages du null (Bailey & López de Prado), retranchée du t observé. Il ne
calcule PAS le DSR complet (asymétrie et aplatissement de la série), parce que le registre ne
conserve que des agrégats — corriger ce qui est mesurable et le dire vaut mieux qu'inventer
des moments qu'on n'a pas.

Les t-stats absents sont dérivés des intervalles bootstrap. La dérivation est **recoupée** sur
le seul candidat portant les deux : BTC_LEAD_ALT_CASCADE donne **3,327 dérivé contre 3,315
déclaré**. La méthode est vérifiée, pas postulée.

---

## E4 — la suite de tests n'exécutait aucun test

Le constat de l'audit (« 14 tests hors foundry en échec ») ne correspondait pas à l'état réel,
qui était pire.

Le venv tourne sous **Python 3.8.10**, mais tout un sous-arbre — `src/futur/truth/` (le Truth
Engine), plus quelques modules alpha20/foundry — utilise la syntaxe d'union PEP 604 (`X | Y`)
au niveau module, qui exige Python 3.10. Ces modules lèvent `TypeError` **à l'import**.

Conséquence : `pytest tests/` s'interrompait sur « **22 errors during collection** » et
**n'exécutait aucun test**. La suite entière était inerte — ce qui explique que personne ne
voyait d'échecs : il n'y avait pas d'exécution.

Avec `--continue-on-collection-errors`, l'état réel apparaît : **1 541 passent, 2 échouent,
2 sautés, 22 erreurs de collecte**.

### Les deux échecs réels, et ce qu'ils avaient en commun

Aucun des deux n'était un bug de production. Les deux étaient des tests **figés sur un
présent qui a bougé** :

1. **`test_fetch_positioning_wide_merges_all_endpoints`** (le chemin funding/OI/positioning,
   celui de WHALE_LSR_SCREEN_V1 et FUNDING_BASIS_DISAGREEMENT_V2 — donc bien dans le
   périmètre). Le test utilisait des dates absolues (2026-07-16) alors que
   `fetch_positioning_wide` borne son `start` à un plancher de rétention **relatif à `now`**
   (`now − 30 j`). Passé ce délai, `start` était repoussé au-delà du `end` codé en dur et la
   fonction retournait un frame vide. Le test échouait depuis mi-août pour une raison de
   calendrier. **Corrigé** : repères relatifs à `now`.

2. **`test_fidelity_against_validator_population`**. Il compare un compte FIXE (26 750) à un
   parquet qui **grandit** — le détecteur de cascade est alimenté en continu, la population
   avait atteint 26 949. **Corrigé** en bornant la population à
   `VALIDATOR_POPULATION_CUTOFF = 2026-08-27T13:00Z`, ce qui reproduit **exactement** les
   quatre comptes publiés (26 750 / 2 485 / 24 065 / 200) — la vérification forte, celle qui
   prouve que la sélection n'a pas dérivé d'un bit.
   Une nuance conservée telle quelle : sur cette population bornée, la moyenne du bras shock
   vaut désormais **+41,99 contre +41,70 publié**. Les LABELS eux-mêmes ont bougé pour les
   événements proches de l'ancienne frontière de données. Tolérance élargie à 0,4 bps **avec
   la raison écrite**, plutôt que de supprimer le test en emportant avec lui la vérification
   des comptes.

### Le correctif de collecte

`tests/conftest.py` écarte explicitement les modules PEP 604 tant que l'interpréteur est
inférieur à 3.10, avec le motif et la liste en clair. Ce n'est pas masquer le problème : c'est
transformer une panne globale silencieuse en fait déclaré (« N modules non collectés,
interpréteur 3.8 »). Sur un interpréteur 3.10+, rien n'est ignoré.

**Ce qui reste ouvert** : soit migrer le venv en 3.10+, soit rétro-porter la syntaxe du Truth
Engine. Tant que ni l'un ni l'autre n'est fait, ce sous-système n'est couvert par aucun test
sur cette machine — et c'est désormais écrit plutôt que subi.

---

## A3 — l'overlay de vol : l'audit avait tort, et le vrai défaut est ailleurs

L'audit déduisait de `vol_overlay_multiplier: 1.0` que l'overlay n'avait jamais mordu.
Mesuré : **45,3 % des `combined_forecast_z` historiques sont > 0**, donc l'overlay mord
régulièrement (`multiplier = 1 − 0,5·max(z,0)`). Et P1_VOL_OVERLAY diverge bien de
P1_CONTROL (+1 232 contre +1 288 sur SHORT_COVERING).

Le vrai défaut : **le multiplicateur était appliqué puis immédiatement oublié**. `SUMMARY.json`
n'en portait que la valeur COURANTE, ce qui se lit « il n'a jamais mordu » alors que ça veut
seulement dire « il ne mord pas maintenant ».

**Instrumenté** : `overlay_steps`, `overlay_binding_steps`, `overlay_multiplier_min`,
`overlay_multiplier_mean`, et un `overlay_status` à **trois** états —
`NO_OVERLAY` / `OVERLAY_NOT_YET_OBSERVED` / `OVERLAY_NEVER_BINDING` / `OVERLAY_BINDING`. Un
portefeuille dont l'overlay ne mord jamais n'est pas une variante indépendante, et doit se
signaler comme tel plutôt que se faire passer pour une troisième hypothèse.

⚠ Les compteurs démarrent au 2026-09-06 : l'historique antérieur n'est pas reconstructible.

---

## A4 — le dénominateur asymétrique : normal, et maintenant testé

`alpha_denominator_high_water` ne contient que `LIQ_CASCADE_REPEAT_V1` alors que
`cumulative_pnl_by_alpha` porte aussi SHORT_COVERING. Vérifié : **ce n'est pas une lacune**,
les deux dicts ont des durées de vie différentes.

Le dénominateur est un **cliquet d'ÉPISODE**, remis à zéro dès que l'alpha n'a plus d'intent
vivant (`portfolio.py` : « point de reset naturel, pas un paramètre »). Le PnL est un **cumul
de vie**. Un alpha privé de capital n'a plus d'intent, donc plus de dénominateur, et garde son
PnL historique.

La crainte de l'audit — « alors les 1 288 $ ne sont pas comparables aux 954 $ » — ne tient pas :
le dénominateur est un dispositif de dimensionnement de budget, il n'entre pas dans
l'attribution du PnL. C'est l'invariant `sum(pnl_by_alpha) == PnL physique` (commit `58ae1455`)
qui gouverne la comparabilité.

**L'invariant réel, désormais testé** : le dénominateur ne doit JAMAIS contenir un alpha sans
intent vivant. L'inverse (un alpha du PnL sans dénominateur) est le comportement voulu.

---

## C3 — les candidats sans code : un test rouge plutôt qu'une mutation automatique

Quatre entrées ne tournent pas : `LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1`,
`MICROSTRUCTURE_OFI_CLUSTER_V1` (CODE_MISSING), `FUNDING_BASIS_DISAGREEMENT_V1`,
`CROSS_SECTIONAL_MOMENTUM_PIT_V1` (DATA_BLOCKED).

Aucune ne portait de date : « CODE_MISSING » était un état **sans durée**, donc personne ne
pouvait dire s'il datait d'hier ou de six mois, donc personne ne tranchait jamais.
`operational_status_since` est ajouté aux quatre (valeur = `built_at` du registre : elles n'ont
jamais quitté cet état).

L'audit demandait un passage **automatique** à `RETIRED_NOT_IMPLEMENTED`. Une mutation
automatique d'un registre scientifique ferait disparaître une décision humaine dans un cron.
À la place : au-delà de `STALE_UNIMPLEMENTED_DAYS = 30`, **un test échoue** tant que la
décision n'est pas écrite — implémenter, ou retirer avec `retirement_decision` + motif. Un
registre qui liste des alphas sans code dilue la lecture du scoreboard ; un test rouge, non.
On ne supprime pas l'entrée : supprimer effacerait la trace qu'un mécanisme a été envisagé et
écarté.

Le seuil se déclenchera le **2026-09-30**. Aucun candidat n'est encore concerné — le mécanisme
est armé, pas rétroactif.

---

## B2 — la décomposition du turnover n'est pas encore lisible, et ce n'est pas une erreur

| classe | USD |
|---|---|
| EXIT | 127 541 |
| ENTRY | 113 149 |
| SIGNAL_RESIZE | 14 535 |
| FILL_CONVERGENCE | 10 024 |
| **somme** | **265 249** |
| `cumulative_turnover_usd` | **2 117 002** |

L'écart de 8× n'est pas une incohérence comptable : les deux compteurs s'incrémentent
**ensemble** (`portfolio.py:747-749`). `cumulative_turnover_by_class` date du commit `3f48476`
(2026-09-05) alors que le cumul court depuis le 2026-09-01.

Conséquence pratique : la question « quelle part du turnover est mécanique et non liée au
signal » n'est **pas encore répondable**, et ne le sera qu'à partir du 2026-09-05. Sur ce
segment-là, `SIGNAL_RESIZE` + `FILL_CONVERGENCE` pèsent 9,3 % — mais sur cinq jours et deux
alphas éligibles seulement, ce n'est pas encore une mesure.
