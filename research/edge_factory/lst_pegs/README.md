# Piste 10 — Pegs LST & convergence d'actifs liés

Mean-reversion sur paires **économiquement liées** : stETH/ETH, cbETH/ETH,
wbETH/ETH, stables mineurs/USDT. Position toujours **hedgée** (long jambe
décotée / short perp de l'actif de référence). Piste défensive : PnL
attendu modeste mais décorrélé — elle lisse le portefeuille.

Horizon : heures – semaines.

## Ce que dit la recherche

- Scharnowski, *The Economics of Liquid Staking Derivatives: Basis
  Determinants and Price Discovery* (Journal of Futures Markets 2025,
  https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22556) : le basis
  LST est non nul et varie avec **incitations de staking, risque de
  concentration, limites à l'arbitrage et facteurs comportementaux** —
  c'est une structure modélisable, pas du bruit.
- *Exploring the Market Dynamics of Liquid Staking Derivatives*
  (https://arxiv.org/abs/2402.17748) : la plupart des déviations au-delà
  d'un petit seuil **se corrigent en quelques heures** ; l'arbitrage
  historique documenté est réel mais *petit* (≈343 ETH de profit cumulé
  sur 400 transactions) — l'edge est dans le **timing des dislocations**,
  pas dans l'arb permanent.
- Précédent extrême : stETH à **0,93 ETH** (Terra/3AC, mai-juin 2022),
  avant les retraits Shapella. Depuis les retraits, la file de rachat
  borne la durée de dislocation → le trade de convergence a un horizon
  quantifiable = longueur de la file + marge.
- Corollaire : une décote LST qui **s'élargit** est aussi un indicateur de
  stress systémique (déleveraging forcé de loopers) → alimente le filtre
  risk-off commun avec les pistes 8 et 11.

## Sous-signaux à tester

1. **Convergence stETH/ETH** : z-score de la décote (pool Curve + CEX) ;
   entrée si décote > seuil ET file de rachat courte ET pas de stress
   funding — sortie à la re-convergence ou à l'échéance de la file.
2. **cbETH / wbETH** : mêmes mécanique, liquidité moindre, décotes plus
   fréquentes — vérifier le coût réel avant d'élargir.
3. **Décote comme signal de stress** (pas de position) : élargissement
   simultané des décotes LST = déleveraging → réduire le risque global.
4. **Stables mineurs vs USDT** : micro-depegs mean-reverting (FDUSD,
   TUSD…) — uniquement si la profondeur le permet, sinon abandonner.

## Données (gratuites)

- Prix pools Curve/Uniswap : lecture RPC on-chain ou API DefiLlama
  (`/coins/prices` historique).
- CEX : cbETH (Coinbase), wbETH (Binance spot, klines Vision), stETH
  (OKX/Bybit spot).
- File de rachat Lido + APY staking : API Lido / beacon chain (gratuit).
- Hedge : perp ETH Binance déjà couvert par le lake.

## Protocole de rejet

- Le PnL doit venir de la **convergence**, pas du carry de staking déguisé
  (séparer les deux composantes dans l'attribution).
- Backtest incluant mai-juin 2022 en scénario adverse : la stratégie doit
  survivre à une décote qui s'élargit de 3 % supplémentaires après entrée
  (sizing/stop en conséquence).
- Coûts réels des deux jambes (le spot LST est cher à trader) ×2 + gates
  du [README parent](../README.md).
