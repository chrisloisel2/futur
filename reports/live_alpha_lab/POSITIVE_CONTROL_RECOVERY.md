# Contrôle positif — ce que la chaîne restitue d'un edge connu

_Généré par `scripts/audit_positive_control_recovery.py`. Ne pas éditer à la main._

Le placebo mesure ce que la chaîne **ajoute** à un signal sans edge. Celui-ci
mesure ce qu'elle **retire** à un signal qui en a un. Sans lui, « aucun alpha n'a
d'edge net du marché » (item A1, 548 décisions) reste indécidable entre deux
mondes : il n'y a pas d'edge, ou il y en a un et l'appareil le détruit.

La réponse tient en deux lignes, et la seconde n'était pas la question posée.

> **L'appareil est fidèle.** Un edge injecté ressort intact, au bit près.
> **L'appareil est aveugle.** À son nombre d'épisodes, il ne pouvait pas voir
> l'edge cherché — sur quatre alphas sur cinq.

## 1. Ce qui a été injecté

| grandeur | valeur |
|---|---|
| décisions construites | **1408** |
| fenêtre | 2026-06-29 → 2026-09-06 |
| horizon | `fwd_4h`, fenêtres non recouvrantes |
| ancrage | `dec` — latence nulle par construction |
| look-ahead | **oui, délibéré** : c'est l'instrument |
| capital | **aucun** — `eligibility.BLOCK_POSITIVE_CONTROL` |

## 2. Fidélité — la chaîne rend-elle ce qu'on lui donne ?

L'oracle sélectionne avec `MarkSeriesCache` et `universe_return_bps` — les mêmes
objets que le labelliseur. Par décision, les deux doivent donc coïncider au bit
près, et tout écart viendrait des deux seules couches intermédiaires : le
decluster en épisodes et le filtre des refus.

| contrôle | attendu | observé |
|---|---|---|
| écart max par décision | 0 | **0.000000 bps** |
| écart au niveau épisode | 0 | **0.000000, 0.000000, 0.000000, 0.000000 bps** |
| décisions refusées (`NO_PRICE`/`STALE_MARK`) | — | **0 sur 1408** |
| fonction de transfert | pente 1, ordonnée 0 | **pente 1.026, ordonnée -4.954 bps** (r² 0.9775) |

| Δ visé | injecté (mesuré) | récupéré | déplacement vs placebo | IC 95 % | épisodes |
|---|---|---|---|---|---|
| 0 bps | 0.00 | -5.60 | **+0.00** | [-18.9, +7.9] | 326 |
| 10 bps | 8.74 | 1.12 | **+6.72** | [-9.5, +12.2] | 325 |
| 29 bps | 26.19 | 27.64 | **+33.24** | [+13.6, +43.6] | 318 |
| 60 bps | 57.20 | 51.56 | **+57.16** | [+42.0, +61.3] | 321 |

Le déplacement suit l'injection. Il n'est pas exact niveau par niveau parce que
les niveaux tournent sur des barres DISJOINTES : chaque moyenne porte ±10 à 15 bps
de bruit de marché qui ne s'annule pas entre niveaux. La fidélité, elle, est
exacte, parce qu'elle est appariée sur les mêmes lignes.

**Conclusion partielle : la chaîne de mesure ne détruit aucun signal.** Ni le
decluster, ni le filtre des refus, ni la soustraction du coût, ni le bootstrap.
L'hypothèse « l'appareil divise l'edge par quatre » est écartée.

## 3. Résolution — la chaîne pouvait-elle VOIR l'edge cherché ?

C'est la question que le contrôle a soulevée sans qu'on la pose. Un appareil peut
être parfaitement fidèle et incapable de distinguer +15 bps de zéro, s'il n'a pas
assez d'épisodes. La dispersion mesurée est de **σ ≈ 112 bps par épisode** — le
marché crypto à 4 h. Il faut donc :

- **29 bps d'excess** (soit 15 bps nets) : **116 épisodes indépendants** pour 80 % de chances de le voir.
- **60 bps d'excess** (soit 46 bps nets) : **27 épisodes indépendants** pour 80 % de chances de le voir.

### Ce dont chaque alpha disposait réellement

| alpha | épisodes | excess mesuré | IC 95 % | plus petit edge visible | verdict |
|---|---|---|---|---|---|
| `LIQ_CASCADE_REPEAT_V1` | 16 | -12.2 bps | [-92.2, +67.8] | 114 bps | 🟠 **indécidable** |
| `LIQ_CASCADE_REPEAT_SYSTEMIC_V1` | 6 | -9.4 bps | [-130.5, +111.7] | 173 bps | 🟠 **indécidable** |
| `LIQ_CASCADE_FAR_FROM_LOW_V1` | 40 | +6.1 bps | [-39.5, +51.7] | 65 bps | 🟠 **indécidable** |
| `BTC_LEAD_ALT_CASCADE_V1` | 1 | — | — | — | ⚪ rien à lire |
| `SHORT_COVERING_CONTINUATION_V1` | 99 | +1.9 bps | [-16.7, +20.5] | 27 bps | ✅ vrai négatif |
| `PLACEBO_RANDOM_V1` | 26 | -28.1 bps | [-72.8, +16.6] | 64 bps | ⚙️ contrôle — pas un candidat |
| `POSITIVE_CONTROL_ORACLE_V1` | 860 | +19.8 bps | [+12.1, +27.6] | 11 bps | ⚙️ contrôle — pas un candidat |

_« Vrai négatif » = l'intervalle exclut la cible de 29 bps d'excess (+15 bps nets). « Indécidable » = il ne l'exclut pas._

## 4. Ce que ça change à la lecture de l'item A1

« Aucun alpha n'a d'edge net du marché » se scinde en deux affirmations très
différentes, et une seule est établie :

- **1 alpha(s) exclu(en)t réellement la cible** : `SHORT_COVERING_CONTINUATION_V1`. Là, le zéro est un zéro.
- **3 alpha(s) ne l'excluent pas** : `LIQ_CASCADE_REPEAT_V1`, `LIQ_CASCADE_REPEAT_SYSTEMIC_V1`, `LIQ_CASCADE_FAR_FROM_LOW_V1`. Leur intervalle contient
  confortablement +29 bps. Ils n'ont pas montré l'absence d'edge, ils ont montré
  qu'ils n'avaient pas de quoi trancher.

Le goulot n'est donc pas la mesure, c'est le **nombre d'épisodes indépendants** —
exactement la contrainte déjà identifiée au round 4 de la chasse sous sa forme
duale (`confirmable en N ans ⟺ Sharpe ≥ 5,60/√N`).

## Ce que ce contrôle ne dit pas

**Il ne teste pas la source de prix.** Les deux bouts lisent la même archive de
marks, donc une erreur commune aux deux resterait invisible. Comparer deux
archives indépendantes est une question distincte, et elle doit se faire SANS
sélection, sinon la malédiction du vainqueur produit une fausse atténuation.

**Il ne teste pas la couche de découverte.** Features, backtest de validation et
déflation sont en amont du labelliseur et ne sont pas traversés ici.

**Il ne mesure pas le coût de la latence.** `decided_at = event_time` par
construction : la latence est mesurée séparément (`decision_lag_h`, audit du
2026-09-05), et confondre les deux les rendrait toutes deux illisibles.

