# Piste 11 — Régime stablecoin / liquidité agrégée (meta-signal)

**Pas un moteur d'alpha autonome** : un cadran de régime qui module
l'exposition brute du portefeuille (risk-on / risk-off) avant les
changements de liquidité. Se juge en amélioration du Sharpe combiné,
comme l'overlay L2 (piste 6).

Horizon : 1 h – 7 j.

## Ce que dit la recherche

- *Stablecoins as Dry Powder: A Copula-Based Risk Analysis*
  (https://arxiv.org/abs/2603.23480, Imperial) : le volume stablecoin et
  sa volatilité haussière **causent** (in-sample, horizons quotidien →
  mensuel) la volatilité du marché crypto — la « poudre sèche » est
  mesurable et précède l'activité.
- *Stability Anchors and Risk Amplifiers: Tail Spillovers Across
  Stablecoin Designs* (https://arxiv.org/abs/2602.18820) : en queue de
  distribution, les stables fiat-backed (USDT/USDC) sont des **ancres**
  (spillovers nets ~0, flight-to-quality), les designs algo/crypto-
  collatéralisés deviennent **amplificateurs**. Le comportement de queue
  n'est pas le comportement moyen — modéliser les régimes séparément.
- Philadelphia Fed, *Flight to Safety: Stablecoin's Role as a Safe-Haven*
  (https://www.philadelphiafed.org/the-economy/banking-and-financial-markets/flight-to-safety-evaluating-stablecoins-role-as-a-safe-haven-asset-in-defi-markets) :
  en stress, l'USDT sert de **bouée de liquidité surtout pour les détenteurs
  d'ETH** (retail) ; rôle plus faible côté BTC — les flux stables sont
  asymétriques par actif.
- Ahmed & Aldasoro, *Stablecoins and safe asset prices* (BIS/Cleveland Fed,
  https://www.clevelandfed.org/-/media/project/clevelandfedtenant/clevelandfedsite/events/financial-stability-conferences/2025/ahmed_paper.pdf) :
  les flux stablecoin sont assez gros pour bouger les rendements T-bills —
  l'agrégat est devenu une variable macro à part entière.
- Complément direct : la piste 4 (arxiv 2411.06327) montre que **USDT →
  CEX prédit positivement les retours BTC/ETH en 1–6 h** — la piste 11 est
  la version *agrégée/lente* du même mécanisme (mint/burn, supply), la
  piste 4 la version *flux/rapide*. Les garder décorrélées ou les fusionner.

## Sous-signaux à tester

1. **Accélération mint/burn** : Δ7j vs Δ30j de la supply USDT+USDC
   agrégée (toutes chaînes) → cadran risk-on/off principal.
2. **Réserves stables sur exchanges** : niveau + pente (pouvoir d'achat
   posé sur le carnet).
3. **Moniteur de depeg** : USDT/USDC vs 1,00 en intraday — micro-décotes
   persistantes = stress de financement → risk-off immédiat.
4. **Dominance stable** : market cap stables / market cap total (déjà
   partiellement dérivable de `coingecko_global`) — hausse rapide =
   flight-to-cryptosafety en cours.
5. **Imbalance DEX stable↔volatile** : sens net des swaps sur les gros
   pools (Uniswap/Curve) comme flux d'agression risk-on/off.

## Données (gratuites)

- **DefiLlama Stablecoins API** (`/stablecoins`, historique de supply par
  chaîne et par coin) — la meilleure source gratuite pour mint/burn.
- `coingecko_global` (✅ archivé 1 h) : market caps pour la dominance.
- Depeg : klines spot USDT/USDC (Binance Vision, ✅ couvert).
- Flux CEX stables : recoupe la piste 4 (étiquetage d'adresses d'exchanges) —
  mutualiser le collecteur, pas le dupliquer.

## Protocole d'évaluation (spécifique meta-signal)

- Se teste comme **modulateur d'exposition** du portefeuille existant :
  amélioration du Sharpe/MAR combiné vs portefeuille non modulé, coûts de
  re-sizing inclus.
- Doit rester utile quand la piste 4 est active (contribution marginale
  au-delà des flux CEX rapides), sinon fusionner les deux familles.
- Attention aux ruptures structurelles : la croissance séculaire de la
  supply stable (régulation, adoption) n'est pas un signal — travailler
  en accélérations détrendées, jamais en niveaux.
