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

## Références microstructure

- Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*
  (https://arxiv.org/abs/1011.6402) : l'**OFI** (order flow imbalance
  cumulant les événements du carnet) a une relation *linéaire* avec les
  variations de prix haute fréquence et domine les mesures d'imbalance de
  trades — c'est la feature de base de l'overlay.
- *Exploring Microstructural Dynamics in Cryptocurrency Limit Order Books*
  (https://arxiv.org/abs/2506.05764) : **les inputs comptent plus que la
  profondeur du modèle** — des features carnet/trades bien construites
  battent l'empilement de couches ; l'importance des features est stable
  cross-assets (BTC → petites caps). Investir dans les features, pas dans
  l'architecture.
- Généralisation de l'OFI multi-niveaux :
  https://arxiv.org/abs/2112.02947.

## Prérequis

N'activer cette piste que lorsqu'au moins un edge des pistes 1–5 est passé
en shadow avec PnL positif : l'overlay se mesure en **amélioration de
l'implementation shortfall** de cet edge, pas en PnL propre.
