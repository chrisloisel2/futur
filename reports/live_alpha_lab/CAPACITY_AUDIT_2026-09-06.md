# Capacité — Live Alpha Lab, 2026-09-06

Question posée (item B3) : le simulateur remplit sauf rejet explicite. Quel est le
notionnel par trade au-delà duquel l'edge se dégrade ? « À 200 K$ ce n'est probablement pas
contraignant » devait devenir une mesure, pas une supposition.

**Réponse courte : ça dépend entièrement de la politique choisie, et l'écart entre les
politiques défendables est d'un facteur 60. Cet écart n'est pas un détail de méthode — c'est
la mesure de ce qu'on ne sait pas.**

---

## 0. Le plafond actuel est inerte, et on ne le savait pas

`orders.liquidity_cap_quantity()` plafonne à `open_interest × 0,002`. L'open interest est un
**stock de positions ouvertes**, pas une profondeur de carnet.

Mesuré sur le forward (`P1_CONTROL`, 1 634 ordres) : **le plafond a mordu 16 fois, soit
1,0 %**, pour 13,5 k$ de notionnel refusé sur 2,12 M$ de turnover (0,6 %).

Personne ne pouvait le dire avant, parce que rien ne le comptait. `PortfolioState` porte
désormais `capped_order_count` et `capped_notional_usd`, sur le modèle de
`suppressed_turnover_usd` (« on mesure ce qu'on refuse »). Ces compteurs **ne changent aucun
fill** — ils rendent visible un comportement déjà présent.

---

## 1. Le volume nécessaire à un plafond de capacité n'existait pas

L'action demandée était « plafonner la taille par une fraction du volume observé sur la
barre ». La seule série de volume du dépôt, `data/enriched/*_1h_enriched.parquet`, s'est
**arrêtée fin juin 2026 pour 40 des 50 symboles** :

| | |
|---|---|
| symboles avec une barre depuis le 2026-09-01 | **10 / 50** |
| décisions labellisées couvertes | **106 / 548** |
| dernière barre pour ARUSDT (30 décisions) | 2026-06-29 |
| dernière barre pour ARBUSDT (27), BCHUSDT (24), TRXUSDT (19), OPUSDT (17)… | 2026-06-28/29 |

Aucun des huit symboles les plus tradés par le lab n'a de volume frais. Les flux
`metrics_5m` (Vision et live) ne portent que l'open interest et des ratios, jamais de volume.

`scripts/probe_spread_cross_section.py` capture désormais le **volume 24 h par symbole** (un
second appel REST, `ticker/24hr`) en plus du carnet. Sans ça, la capacité n'était mesurable
pour aucun des symboles qui comptent.

---

## 2. Trois politiques, un facteur 60 entre elles

Appliquées aux 1 698 ordres réellement exécutés (2,12 M$ de turnover) :

| politique | définition | ordres plafonnés | notionnel refusé |
|---|---|---|---|
| `OPEN_INTEREST` | 0,2 % de l'OI — **la règle actuelle** | 0,2 % | 13,5 k$ (0,6 %) |
| `ADV_FRACTION` | 1 % du volume 24 h, prorata de l'horizon | 1,1 % | 37,2 k$ (1,8 %) |
| `TOP_OF_BOOK` | notionnel affiché au meilleur limite | **20,0 %** | **1,33 M$ (62,7 %)** |

Aucune des trois n'est « la vraie capacité » :

- `TOP_OF_BOOK` est une **borne basse**. Elle ne regarde que le niveau 1 : un ordre valant
  3× le meilleur limite ne paie pas forcément beaucoup plus, les niveaux suivants sont
  souvent proches. Elle surestime donc la contrainte.
- `ADV_FRACTION` est une **convention** de participation, pas une mesure d'impact.
- `OPEN_INTEREST` mesure autre chose que ce qu'on lui demande.

La vraie réponse exige un carnet L2 complet. `data/microstructure_reduced` ne le capture que
pour BTC/ETH/SOL — **37 des 548 décisions labellisées, soit 6,8 %**. Tant que ça ne change
pas, la capacité réelle reste bornée entre 1,8 % et 62,7 % de turnover refusé, et cet
intervalle est la conclusion honnête.

---

## 3. Capacité par alpha, en notionnel par trade

Lue sur les symboles que chaque alpha touche **réellement**, pondérés par sa fréquence de
décision — un alpha qui ne trade que des symboles minces n'hérite pas de la liquidité de BTC
parce que BTC est dans l'univers déclaré.

| alpha | décisions | `TOP_OF_BOOK` p10 | médiane | `ADV_FRACTION` p10 | médiane |
|---|---|---|---|---|---|
| SHORT_COVERING_CONTINUATION_V1 | 410 | 297 $ | 1 167 $ | 15 594 $ | 69 144 $ |
| LIQ_CASCADE_FAR_FROM_LOW_V1 | 64 | 297 $ | 838 $ | 34 089 $ | 111 832 $ |
| LIQ_CASCADE_REPEAT_V1 | 33 | 280 $ | 648 $ | 36 260 $ | 111 832 $ |
| BTC_LEAD_ALT_CASCADE_V1 | 31 | 324 $ | 1 132 $ | 15 594 $ | 128 194 $ |
| LIQ_CASCADE_REPEAT_SYSTEMIC_V1 | 10 | 600 $ | 1 000 $ | 36 260 $ | 43 125 $ |

`p10` est le chiffre qui compte : le notionnel au-delà duquel 90 % des décisions de cet
alpha sortent déjà du domaine observé.

**À comparer aux ordres réellement passés :** médiane **21 $**, p90 3 893 $, p99 14 589 $,
max 30 994 $.

---

## Verdict

**La bonne nouvelle est réelle, mais elle est plus étroite que l'intuition.**

Sous la convention ADV, le lab est loin de sa capacité : l'ordre médian (21 $) et même le
p90 (3 893 $) passent très en dessous du plafond p10 (15,6 k$). Seuls les plus gros ordres
s'en approchent — d'où 1,1 % de plafonnage. À 200 K$ par portefeuille, la capacité n'est
effectivement **pas** la contrainte qui mord.

Mais deux réserves, et elles comptent :

1. **Ce n'est vrai qu'aux tailles actuelles, qui sont minuscules.** L'ordre médian vaut
   21 dollars. Le lab n'engage quasiment pas son capital (voir les portes de capital
   fail-closed) : dire « la capacité n'est pas contraignante » à 21 $ par trade n'apprend
   presque rien sur ce qui se passerait à taille normale. En multipliant les positions par
   10 — ce qui reste un portefeuille de 200 K$ correctement investi — le p90 des ordres
   (39 k$) dépasserait le plafond ADV p10 de SHORT_COVERING (15,6 k$).
2. **20 % des ordres dépassent déjà la taille affichée au meilleur limite.** Pour ceux-là,
   le fill au mid moins 2 bps n'est adossé à aucune observation. Ce n'est pas la preuve
   qu'ils coûtent plus cher ; c'est la preuve qu'on n'en sait rien.

## Ce qui n'est pas fait, délibérément

Le plafond du simulateur n'est **pas** remplacé. Changer la règle de fill en cours de route
mélangerait deux régimes d'exécution dans une même courbe d'équité — ce que
`data_segment_boundaries` existe pour empêcher dans ce projet. Le remplacement par
`ADV_FRACTION` (la politique la plus défendable des trois) est une décision séparée, qui
devra déclarer sa frontière de segment.

## Ce qui reste ouvert

1. **L2 hors BTC/ETH/SOL.** C'est la seule chose qui resserrerait l'intervalle [1,8 % ;
   62,7 %]. Tout le reste est convention.
2. **La capacité à taille réelle.** Elle ne sera mesurable que lorsque le lab engagera
   vraiment son capital ; aujourd'hui la mesure porte sur des ordres de 21 $.
3. Le décompte du turnover par classe (item B2) ne couvre que 265 k$ des 2,12 M$ cumulés :
   `cumulative_turnover_by_class` date du commit `3f48476` (2026-09-05) alors que le cumul
   court depuis le 2026-09-01. Ce n'est pas une erreur de comptabilité — les deux compteurs
   s'incrémentent ensemble — mais la décomposition ne pourra répondre à « quelle part du
   turnover est mécanique » qu'à partir de cette date.
