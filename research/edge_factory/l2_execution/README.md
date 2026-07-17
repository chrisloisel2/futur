# Piste 6 — Microstructure L2 comme edge d'exécution (overlay uniquement)

**Pas une stratégie autonome.** Le L2 pré-événement prédit fortement les
transitions de liquidité ; l'order flow n'ajoute de la valeur qu'en
complément et surtout selon l'actif (https://arxiv.org/abs/2607.09230).
L'effet quart-d'heure est prédictif mais son gain brut moyen est inférieur
aux frais taker (https://arxiv.org/abs/2607.09426) : à utiliser pour
améliorer les entrées des edges déjà positifs.

Horizon : secondes–15 min.

## Décisions couvertes

- maker vs taker selon l'état de liquidité ;
- retarder l'entrée (queue imbalance, OFI défavorables) ;
- annuler (transition de liquidité imminente) ;
- phase du quart-d'heure comme timing d'entrée.

## Prérequis

N'activer cette piste que lorsqu'au moins un edge des pistes 1–5 est passé
en shadow avec PnL positif : l'overlay se mesure en **amélioration de
l'implementation shortfall** de cet edge, pas en PnL propre.
