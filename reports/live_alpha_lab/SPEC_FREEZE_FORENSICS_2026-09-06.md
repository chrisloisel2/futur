# Le freeze de SHORT_COVERING_CONTINUATION_V1 est-il antérieur à sa preuve ?

Item C2. L'alpha porte 57 % du PnL attribué et son `scientific_status` est `RECONSTRUCTED`.
Question posée : ce que « reconstruit » recouvre exactement, et si le freeze précède ou suit
la reconstruction. Si la spec a bougé après le freeze, les 417 décisions forward ne sont pas
de la preuve jamais-vue.

**Réponse : la spec DÉCLARÉE n'a pas bougé. La spec IMPLÉMENTÉE n'est pas vérifiable depuis
le dépôt pour 81 % des décisions forward. Ce sont deux choses différentes, et seule la
première était instrumentée.**

---

## Ce qui est vérifiable, et qui est bon

`alpha_spec_hash` vaut **une seule valeur** (`11e7bace27977e1b`) sur les 417 décisions forward
— 15 nulles au tout début, aucune autre valeur ensuite. La déclaration de l'alpha dans
`configs/live_alpha_registry.yaml` n'a donc jamais changé pendant le forward-test.

(`config_hash` prend 6 valeurs, mais il hache le registre ENTIER : il bouge quand n'importe
quel autre alpha est édité. Ce n'est pas une dérive de cette spec-là.)

## Ce qui n'est pas vérifiable, et que personne ne mesurait

`alpha_spec_hash` hache **l'entrée du registre**, pas le **code**
(`provenance.py::alpha_spec_hash`). Il prouve que la déclaration n'a pas bougé ; il ne dit
rien de l'implémentation. Or :

| | |
|---|---|
| freeze déclaré (registre) | 2026-08-31 **18:08:39** Z |
| freeze déclaré (`freeze_spec.json`) | 2026-08-31 **17:30:00** Z — **divergent** |
| première décision `FORWARD_LIVE` | 2026-08-31 **21:49:33** Z |
| commit de `state.py` / `infer.py` (`b2afea3`) | 2026-08-31 **22:34:03** Z |

**Aucun commit du moteur n'est antérieur au freeze.** Les fichiers qui définissent le seuil
(décile 10/90) et la fenêtre (720 h) sont commités **4 h 25 après** le freeze déclaré, et
**45 minutes après** la première décision forward. Le code qui a produit cette décision
n'existe dans aucun commit sous la forme où il tournait.

`working_tree_dirty` vaut `True` sur **348 des 417** décisions forward. Et l'empreinte qui
saurait dire si ce sont les *chemins de décision* qui étaient sales
(`dirty_decision_paths_sha1`) n'existe que depuis le 2026-09-05 : elle est nulle sur **336
des 417**. Pour ces 336-là, `working_tree_dirty=True` signifie seulement « quelque chose
était modifié quelque part dans le dépôt » — un rapport édité aussi bien qu'un seuil.

Enfin, `freeze_spec.json` et le registre ne s'accordent pas sur la date du freeze. Le
registre gouverne (c'est lui que lit `apply_provenance_tags.py`), donc l'artefact censé
**être** le gel porte une valeur périmée dans son propre champ principal.

## Verdict

Il n'y a **aucun indice de dérive de spec** — l'empreinte disponible est constante. Mais la
propriété que `scientific_status: FROZEN` prétend garantir, « spec figée AVANT toute
observation utilisée pour la juger », n'est pas démontrable depuis le dépôt pour cet alpha.
`RECONSTRUCTED` est donc le bon statut, et le registre était déjà honnête en l'écrivant.

Ce qui manquait n'était pas l'honnêteté, c'était la **conséquence côté capital**.

---

## Le trou bouché

`SHORT_COVERING_CONTINUATION_V1` ne recevait déjà aucun capital — mais par
`BLOCK_NOT_VALIDATED_FOR_FORWARD`, c'est-à-dire par **accident du registre de validation**,
pas à cause de son statut. Le jour où un candidat le validerait, il recevrait du capital en
restant `RECONSTRUCTED`.

`eligibility.py` porte désormais **`BLOCK_UNRESOLVED_SPEC`** : `scientific_status`
∈ `UNRESOLVED_SPEC_SCIENTIFIC_STATUSES` (`RECONSTRUCTED`) → pas de capital, quel que soit
l'avis du registre de validation. Porte **séparée** de `NO_CAPITAL_SCIENTIFIC_STATUSES` parce
que la raison est différente : là le mécanisme est mort, ici il est peut-être bon mais sa
spec n'est pas établie.

Effet mesuré, immédiat et nul sur l'allocation d'aujourd'hui : les alphas nouvellement
bloqués (`SHORT_COVERING_CONTINUATION_V1`, `CROSS_SECTIONAL_MOMENTUM_LIVE_V1/V2`,
`WHALE_LSR_SCREEN_V1`) étaient tous déjà bloqués par une autre porte. L'ensemble des alphas
éligibles est inchangé. C'est une porte **structurelle**, pas un changement de comportement.

### Comment résoudre le statut (ce qui reste à faire)

1. Réconcilier `freeze_spec.json` avec le registre sur `freeze_timestamp`, ou déclarer
   lequel fait autorité.
2. Figer une empreinte du **CODE** de décision, pas seulement de la déclaration — un hash des
   fichiers du moteur, stampé sur chaque décision, à côté d'`alpha_spec_hash`.
   `dirty_decision_paths_sha1` en est la moitié ; il manque l'empreinte du contenu.
3. Ces deux points faits, un nouveau segment forward part de zéro sous un statut `FROZEN`
   qui, lui, sera démontrable.

---

# C1 — la latence de BTC_LEAD_ALT_CASCADE_V1

## Le correctif était déjà appliqué

L'audit demandait de porter à BTC_LEAD_ALT l'option 2 déployée pour LIQ_CASCADE_REPEAT_V1.
Vérification : **c'est déjà le cas**. Son runner appelle `build_event_dataset` →
`detector.load_metrics`, qui appelle `_append_live_tail` (`detector.py:105`) — le même chemin
de code, le même flux prolongé. Mesuré ce jour, le flux qu'il lit accuse **20 minutes** de
retard, pas 45 heures.

Ses `31/31 périmées` en cumul sont **historiques** : ces 31 décisions ont été prises le
2026-09-05 à 06:57:10, sept minutes après le déploiement du splice (06:50:21), en rattrapant
un événement du 2026-09-04 12:30. Nées périmées lors du rattrapage, et définitivement dans le
cumul — exactement le phénomène que le scoreboard documente déjà pour SHORT_COVERING. Ses
colonnes 24 h sont vides parce qu'il n'a rien déclenché depuis, ce qui est normal pour un
mécanisme à ~1,15 épisode indépendant par semaine.

Pour comparaison, sur 24 h glissantes : `LIQ_CASCADE_FAR_FROM_LOW` 0,2 h de latence et
**0/19** périmées, `LIQ_CASCADE_REPEAT` 0,2 h et **0/6**, `SYSTEMIC` 0,3 h et **0/4**.

## Deux défauts trouvés en le vérifiant

### 1. `run_state.json::last_run` ne veut pas dire « dernière exécution »

`run_state.json` affichait `last_run: 2026-09-05T06:57` — 26 heures — alors que le producteur
tournait avec succès à **chaque cycle**. Les **dix** `run_*_shadow.py` écrivent leur
`run_state.json` seulement APRÈS avoir produit de nouvelles décisions ; tous ont un
`return 0` anticipé sur « aucun event » / « rien de nouveau » (idempotence) qui précède
l'écriture.

Conséquence : un alpha rare et un runner mort depuis une semaine produisent **exactement le
même artefact**. C'est l'angle mort qui avait déjà laissé AMIHUD sans aucune décision forward
après son freeze.

Corrigé au niveau du **cycle** plutôt qu'en patchant dix scripts :
`write_attempt_heartbeat()` écrit `<ALPHA>/last_attempt.json` à chaque tentative, quoi qu'il
arrive. Le scoreboard porte une colonne `last_attempt_h_ago` à côté de `last_trigger_h_ago` —
le premier dit si le producteur tourne, le second si l'alpha se déclenche.

### 2. La table `alpha_id -> colonne temps` vivait en trois copies divergentes

`apply_provenance_tags.py` (12 entrées), `compute_live_alpha_lab_scoreboard.py` (12, à jour
depuis le 2026-09-05), et `trade_trace.py` — **9 entrées, périmée** : il y manquait
`BTC_LEAD_ALT_CASCADE_V1`, `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` et
`AMIHUD_ILLIQUIDITY_PREMIUM_V1`, c'est-à-dire précisément l'alpha de cet item.

Le mode de panne est toujours le même et toujours silencieux : un alpha absent de la copie
qu'on interroge n'est pas signalé, il disparaît de la mesure. Le scoreboard portait déjà la
cicatrice du même incident en 2026-09-05 (« un angle mort de monitoring sur précisément les
deux alphas issus de la validation »).

Consolidé dans `src/institutional/live_alpha_lab/schema.py`, source unique, avec un test qui
échoue si un runner déclaré n'y figure pas.

## L'amélioration : l'exécutabilité devient bloquante

`is_forward_eligible` accepte désormais `decision_lag_median_h` et refuse le capital quand la
latence médiane **récente** dépasse l'horizon de l'alpha
(`BLOCK_NOT_EXECUTABLE`). `run_portfolio_shadow.py` mesure et transmet.

Trois choix explicites :

- **Fenêtre récente, pas cumul.** Le cumul inclut les rattrapages et ne redescend jamais : il
  condamnerait à vie un alpha réparé depuis.
- **Latence inconnue ne bloque pas.** Un alpha qui vient d'être figé n'a aucune décision
  forward, donc aucune latence mesurable ; fail-closed ici empêcherait tout nouvel alpha de
  démarrer. Le fail-closed a déjà lieu en amont (validation).
- **Comparaison à SON PROPRE horizon**, pas à une constante : 18,5 h bloque un `fwd_4h` et
  passe pour un `fwd_24h`.

La porte reste une **fonction pure** — la latence est une entrée, pas une lecture de ledger —
donc testable et reproductible, comme le reste d'`eligibility.py`.
