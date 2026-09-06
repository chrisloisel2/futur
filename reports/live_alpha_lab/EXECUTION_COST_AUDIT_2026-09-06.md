# Audit du coût d'exécution — Live Alpha Lab, 2026-09-06

Question posée (item B1) : le simulateur applique `FIXED_SLIPPAGE_BPS = 2,0` par jambe à
tous les symboles et dans tous les régimes, alors que les alphas qui portent le PnL tradent
pendant les cascades de liquidation, sur des alts. L'hypothèse de départ était que le coût
réel explose précisément là, et que les +21 bps nets par aller-retour pourraient tomber à
+5 bps.

**Cet audit ne confirme pas cette hypothèse, et en trouve une autre, plus sérieuse.**

---

## 1. Le spread ne s'écarte pas pendant les cascades — mesuré, pas supposé

Source : `data/microstructure_reduced/raw/bbo/venue=binance` (bande BBO réelle, `bookTicker`).
Méthode : 12 heures contenant ≥ 3 événements de cascade contre 12 heures sans aucun
événement, tirées au sort sur la fenêtre forward, ~250–300 k points de spread par cellule.

| symbole | régime | médiane | p99 | p99,9 | max |
|---|---|---|---|---|---|
| BTCUSDT | cascade | 0,0128 | 0,1296 | 0,9744 | 7,21 |
| BTCUSDT | calme | 0,0130 | 0,1108 | 0,7636 | 7,86 |
| ETHUSDT | cascade | 0,0413 | 0,1610 | 1,2778 | 15,99 |
| ETHUSDT | calme | 0,0415 | 0,2065 | 1,5637 | 23,86 |
| SOLUSDT | cascade | 0,9880 | 1,0036 | 2,0023 | 12,87 |
| SOLUSDT | calme | 0,9845 | 1,0114 | 2,0077 | 10,86 |

Multiplicateur cascade/calme : **0,99× à 1,28× selon le symbole et le percentile.**
Aucun écartement.

**Réserve, et elle est sérieuse.** Une « heure de cascade » est une heure contenant un
événement sur des **alts**. C'est un proxy faible du stress sur le carnet de BTC : une
cascade sur ARUSDT ne stresse pas le book de BTCUSDT. Ce résultat **borne les majors, il ne
dit rien des alts** — et les alts sont 511 des 548 décisions labellisées. La bande BBO
n'existe que pour BTC/ETH/SOL, soit 37 décisions sur 548 (6,8 %).

---

## 2. Le vrai problème n'est pas temporel, il est transversal

Coupe transversale du frozen-50 (REST `bookTicker`, marché calme, spread aller-retour) :

| | bps |
|---|---|
| BTCUSDT | 0,013 |
| ETHUSDT | 0,040 |
| BNBUSDT | 0,132 |
| **médiane alts** | **1,711** |
| ARUSDT | 6,612 |
| ATOMUSDT | 6,229 |
| IMXUSDT | 7,432 |

Soit un demi-spread de **0,86 bps par jambe pour l'alt médian** : l'hypothèse de 2 bps est
**conservatrice** au centre de la distribution — 2,3× le coût observé.

Mais la queue la dépasse déjà, en marché calme : ARUSDT 3,31 bps par jambe, IMXUSDT 3,72.
Et **ARUSDT est le symbole le plus tradé du lab** (30 des 548 décisions labellisées).

Une constante unique est donc simultanément **trop pessimiste pour la majorité des symboles
et trop optimiste là où le lab engage le plus de capital**. Ces deux erreurs ne se
compensent pas : elles déplacent le capital vers les symboles dont le coût est sous-estimé.

---

## 3. Ce que l'audit n'attendait pas : ce n'est pas un problème de spread, c'est un problème de profondeur

Profondeur au **meilleur limite** (moyenne bid/ask), frozen-50 :

| percentile | notionnel USD |
|---|---|
| p10 | 404 |
| p50 | **1 048** |
| p90 | 39 031 |

Les huit symboles les plus tradés par le lab :

| symbole | décisions labellisées | profondeur best-limit | spread A/R |
|---|---|---|---|
| ARUSDT | 30 | **53 $** | 3,30 |
| ARBUSDT | 27 | 890 $ | 0,51 |
| BCHUSDT | 24 | 655 $ | 0,39 |
| TRXUSDT | 19 | 5 240 $ | 0,30 |
| OPUSDT | 17 | 463 $ | 0,90 |
| FILUSDT | 17 | 938 $ | 1,25 |
| ALGOUSDT | 17 | 1 055 $ | 1,05 |
| APTUSDT | 15 | 724 $ | 1,61 |

Le spread affiché n'est disponible que pour quelques centaines de dollars. Confronté aux
ordres réellement exécutés (`P1_CONTROL/intent_ledger.parquet`, 1 698 ordres ; noter que
`executed_delta` est un **notionnel en dollars**, cf. `portfolio.py:736`, pas une quantité) :

| | |
|---|---|
| notionnel médian par ordre | 21 $ |
| p90 | 3 893 $ |
| p99 | 14 589 $ |
| max | 30 994 $ |
| **ordres dépassant la profondeur best-limit** | **18,7 %** |
| ordres la dépassant de plus de 10× | 4,4 % |

L'ordre typique est minuscule et ne peut pas bouger le carnet. Mais **près d'un ordre sur
cinq est plus gros que la taille affichée au meilleur limite**, et pour ceux-là le fill au
mid moins 2 bps n'est adossé à aucune observation : ils traversent plusieurs niveaux.

**Réserve.** Le meilleur limite n'est pas la profondeur totale du carnet. Un ordre valant
3× le best-limit ne paie pas nécessairement beaucoup plus — les niveaux suivants sont
souvent proches. Chiffrer le vrai surcoût demande un carnet L2 complet, que le collecteur
ne capture que pour BTC/ETH/SOL. Ce qu'on peut affirmer sans L2 : pour 18,7 % des ordres,
le coût modélisé ne repose sur rien.

Le plafond de liquidité existant (`orders.py::liquidity_cap_quantity`) utilise
`open_interest × 0,002` comme proxy. L'OI est un **stock** de positions ouvertes, pas une
profondeur de carnet : il est de plusieurs ordres de grandeur supérieur et ne mord donc
jamais sur ces tailles. Le fichier le dit lui-même — « un proxy honnête, pas une simulation
de microstructure réelle ». Il l'est ; il ne répond simplement pas à cette question-là.

---

## Ce qui a été construit

- **`scripts/probe_spread_cross_section.py`** — une requête REST par cycle (15 min), une
  ligne par symbole du frozen-50, append-only vers `data/spread_probe/`. C'est ce qui
  transforme la coupe instantanée ci-dessus en **distribution par symbole**. Câblée dans
  `run_live_alpha_lab_cycle.py` (étape 0). Coût : ~5 000 lignes/jour.
  Limite assumée : cadencée à 15 min, elle sous-échantillonne par construction les quelques
  secondes d'une cascade. Elle mesure le niveau par symbole, pas le pic instantané.
- **`src/institutional/live_alpha_lab/slippage.py`** — coût par symbole sous quatre
  scénarios **nommés** : `SIMULATOR` (2 bps/jambe), `MEASURED_MEDIAN`, `MEASURED_P90`
  (refusé sous 20 sondes — un p90 sur trois points est un maximum déguisé), `STRESS_BOUND`
  (10 bps/jambe, la borne de l'audit, conservée comme borne et non comme estimation).
  Un symbole non sondé renvoie `None`, jamais un repli silencieux sur la constante.

`FIXED_SLIPPAGE_BPS` n'est **pas** modifié. Changer le coût du simulateur en cours de route
créerait une discontinuité dans la courbe d'équité live et mélangerait deux régimes de
comptabilité dans une même série — ce que `data_segment_boundaries` existe pour empêcher
dans ce projet. Le chiffre mesuré se lit à côté ; le remplacement est une décision séparée,
qui devra déclarer sa frontière de segment.

## Ce qui reste ouvert

1. **Le coût réel des alts en cascade est toujours inconnu.** La seule façon de le mesurer
   est d'étendre la bande BBO au-delà de BTC/ETH/SOL, ou d'accepter d'extrapoler depuis les
   majors — extrapolation qu'aucune donnée ne soutient aujourd'hui.
2. **La capacité par alpha (item B3) est la question qui compte vraiment.** 18,7 % des
   ordres dépassent déjà le best-limit à des tailles médianes de 21 $. Le plafond de
   liquidité actuel ne mord pas parce qu'il est adossé à l'OI, pas au carnet.
3. Une fois ~20 sondes accumulées par symbole (quelques heures), `MEASURED_P90` devient
   disponible et les résultats scellés pourront être re-tarifés par symbole sans rien
   réécrire — le ledger `outcomes.parquet` ne scelle que le **brut**, précisément pour ça.
