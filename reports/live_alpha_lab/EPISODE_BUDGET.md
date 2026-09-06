# Budget d'épisodes — combien la famille en produit, et quel edge devient visible

_Généré par `scripts/audit_episode_budget.py`. Ne pas éditer à la main._

Calculé **avant** d'écrire le moindre signal, et **aveugle au résultat** : les
paniers sont tirés au hasard. Ce qui est mesuré ici est la dispersion d'un livre
dollar-neutre de cette forme — une propriété du marché et du design, pas d'une
prédiction. C'est le calcul qui manquait aux quatre alphas indécidables.

| grandeur | valeur |
|---|---|
| historique | 2020-01-01 → 2026-08-31 |
| symboles | 49 |
| barres horaires | 58 440 |
| fenêtre de decluster du lab | 24 h |
| hypothèses à sceller | 5 → seuil `t > 2.33` |

## Le premier paramètre de conception n'est pas l'horizon, c'est le pas

`episodes.decluster` chaîne par lien simple. Un rééquilibrage à un pas
**inférieur ou égal** à la fenêtre de 24 h fond tout l'historique en un seul
épisode — six ans compris. Les lignes concernées sont marquées ⛔ : leur nombre
d'épisodes NOMINAL est celui de la colonne, mais leur nombre RÉEL est 1.

| horizon | pas | panier | épisodes | σ / épisode | erreur-type | edge min. détectable |
|---|---|---|---|---|---|---|
| 1 j | 1 j ⛔ | 5/5 | ~~2199~~ → **1** | 323 bps | 6.9 bps | **21.8 bps** |
| 1 j | 1 j ⛔ | 10/10 | ~~1987~~ → **1** | 169 bps | 3.8 bps | **12.0 bps** |
| 1 j | 1 j ⛔ | 15/15 | ~~1286~~ → **1** | 133 bps | 3.7 bps | **11.7 bps** |
| 1 j | 2 j | 5/5 | 1099 | 290 bps | 8.8 bps | **27.8 bps** |
| 1 j | 2 j | 10/10 | 993 | 168 bps | 5.3 bps | **16.9 bps** |
| 1 j | 2 j | 15/15 | 643 | 129 bps | 5.1 bps | **16.1 bps** |
| 1 j | 3 j | 5/5 | 733 | 424 bps | 15.7 bps | **49.6 bps** |
| 1 j | 3 j | 10/10 | 662 | 180 bps | 7.0 bps | **22.2 bps** |
| 1 j | 3 j | 15/15 | 429 | 138 bps | 6.7 bps | **21.1 bps** |
| 1 j | 5 j | 5/5 | 440 | 272 bps | 13.0 bps | **41.1 bps** |
| 1 j | 5 j | 10/10 | 398 | 153 bps | 7.7 bps | **24.3 bps** |
| 1 j | 5 j | 15/15 | 257 | 121 bps | 7.5 bps | **23.9 bps** |
| 2 j | 2 j | 5/5 | 1099 | 410 bps | 12.4 bps | **39.2 bps** |
| 2 j | 2 j | 10/10 | 993 | 243 bps | 7.7 bps | **24.4 bps** |
| 2 j | 2 j | 15/15 | 643 | 193 bps | 7.6 bps | **24.1 bps** |
| 2 j | 3 j | 5/5 | 732 | 522 bps | 19.3 bps | **61.2 bps** |
| 2 j | 3 j | 10/10 | 660 | 254 bps | 9.9 bps | **31.3 bps** |
| 2 j | 3 j | 15/15 | 428 | 181 bps | 8.8 bps | **27.8 bps** |
| 2 j | 5 j | 5/5 | 440 | 367 bps | 17.5 bps | **55.5 bps** |
| 2 j | 5 j | 10/10 | 397 | 263 bps | 13.2 bps | **41.9 bps** |
| 2 j | 5 j | 15/15 | 257 | 179 bps | 11.2 bps | **35.4 bps** |
| 3 j | 3 j | 5/5 | 732 | 629 bps | 23.3 bps | **73.7 bps** |
| 3 j | 3 j | 10/10 | 660 | 353 bps | 13.7 bps | **43.5 bps** |
| 3 j | 3 j | 15/15 | 428 | 217 bps | 10.5 bps | **33.2 bps** |
| 3 j | 5 j | 5/5 | 440 | 530 bps | 25.3 bps | **80.0 bps** |
| 3 j | 5 j | 10/10 | 398 | 313 bps | 15.7 bps | **49.7 bps** |
| 3 j | 5 j | 15/15 | 257 | 259 bps | 16.1 bps | **51.1 bps** |

_Le nombre d'épisodes décroît quand le panier grandit : un panier de 15/15 exige
35 symboles cotés, ce que les premières années de l'historique ne fournissent pas
toujours. C'est un arbitrage réel entre diversification et longueur d'échantillon._

_« Edge min. détectable » = l'excess moyen par épisode qu'il faut pour avoir
80 % de chances de franchir le seuil pré-enregistré `t > 2.33`, soit
`(t* + 0,84)·σ/√n`. Ce n'est PAS `1,96·σ/√n` : tester cinq hypothèses coûte de
la puissance, et ce coût est déjà compté ici._

## Ce que ça impose au design

**Le pas de rééquilibrage doit être ≥ 2 jours.** À un jour, le decluster fond
l'historique entier en un épisode et aucune hypothèse n'est décidable — quel
que soit son edge.

Sous cette contrainte, la configuration la plus résolvante est **horizon 1 j,
pas 2 j, panier 15/15** : 643 épisodes, σ = 129 bps, et un edge de
**16.1 bps par épisode** devient détectable au seuil des cinq hypothèses.

À comparer à la cible opérationnelle de **+15 bps nets**, soit +29 bps d'excess
avant coût : cette configuration a la résolution nécessaire, et de loin.

Le panier le plus large réduit σ par diversification ; le pas le plus court
compatible avec le decluster maximise n. Les deux vont dans le même sens, ce
qui est rare et qu'il faut prendre.

## Une démonstration involontaire, et elle vaut d'être lue

Les 27 configurations ci-dessus tirent des paniers **au hasard**. Leur moyenne
devrait donc être nulle. La plus extrême affiche pourtant +43.4 bps avec une
erreur-type de 15.7, soit **t = 2.77** (horizon 3 j, pas 5 j, panier 10/10).

Ce t franchit le seuil d'une hypothèse unique (1,64) et même le seuil des cinq
hypothèses (2.33). Sur du bruit pur, sans aucun signal.

C'est la démonstration la plus courte de pourquoi le seuil doit être dérivé du
nombre d'essais : il a suffi d'en regarder 27 pour en sortir un qui a l'air
d'une découverte. Le round 4 en a regardé ~700.

## Ce que ce budget ne dit pas

**La colonne `moyenne` n'est pas un résultat.** C'est le rendement d'un panier
aléatoire : du bruit, dont la seule fonction ici est la démonstration ci-dessus.

**Il ne dit pas qu'il y a un edge.** σ est la dispersion d'un panier ALÉATOIRE :
elle borne ce qu'on pourra VOIR, pas ce qu'on trouvera. Un vrai signal aura une
dispersion différente — plus faible s'il sélectionne des noms corrélés, plus
forte s'il se concentre sur les extrêmes.

**Il suppose des épisodes indépendants.** Avec un pas strictement supérieur à la
fenêtre, les fenêtres de détention ne se recouvrent pas — mais la corrélation
transversale résiduelle entre épisodes voisins n'est pas nulle pour autant. Le
n effectif est donc une borne HAUTE.

