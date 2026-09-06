# Edge discovery scoreboard

P0 absolu (2026-08-28) : 5-10 edges réellement indépendants, chacun edge faible/modéré ×
beaucoup d'occurrences × faible corrélation. Pas de nouveau modèle complexe au milieu du
pipeline :

```
HYPOTHESIS -> DEV_DISCOVERY -> INDEPENDENT_CONFIRMATION -> EXECUTION_ECONOMICS -> PAPER_LIVE -> PORTFOLIO_ADMISSION
```

Règle de gel (voir `docs/PHASE4F_TRUTH_ACCEPTANCE.md` pour l'esprit) : un IC/edge annoncé
"confirmé" doit porter un manifeste de provenance (hash de config + SHA-256 des données + seed).
Sans ça, il reste `UNFROZEN` — discovery, pas confirmé.

**Le critère n°1 est l'edge net, pas l'IC.** Chaque ligne à `EXECUTION_ECONOMICS` ou mieux doit
afficher : gross edge/event, net edge/event, events/day, round-trip cost, latency half-life,
capacity, corrélation aux sleeves existants. L'IC est diagnostique, pas la métrique finale.

Kill vite : ~1-2 jours de discovery max par hypothèse simple quand la donnée existe déjà.
`CLOSED_NO_EDGE` rapide = succès du process, pas un échec.

## Familles (16 mécanismes économiques, catalogue canonique unique)

**`alpha_foundry_v5/labs/catalog.py` est la nomenclature canonique unique pour A1-A16 —
plus aucune paraphrase divergente ailleurs.** Ce tableau reprend `name`/`hypothesis_template`/
`payer` du code directement (2026-08-29, correction P0-5 : les libellés précédents de ce
scoreboard divergeaient du catalogue réel — corrigé, ne pas réintroduire de synonymes).

| # | name (catalogue) | hypothesis_template | payer | statut |
|---|---|---|---|---|
| A1 | Cross-venue price discovery | Venue innovations lead peer repricing. | slower venue inventory | pas commencé |
| A2 | Venue dislocation convergence | Transient venue dislocations converge to a robust anchor. | urgent local flow | `DEV_DISCOVERY` ✅ — voir détail ci-dessous |
| A3 | Queue depletion hazard | Conditional add/cancel/execution intensity predicts first queue depletion. | adverse selected passive liquidity | pas commencé |
| A4 | Liquidity resilience | Refill asymmetry after a sweep separates continuation from rejection. | shock extrapolators | pas commencé |
| A5 | Toxic flow and absorption | Signed flow versus impact identifies informed toxicity or hidden absorption. | late market-order followers | pas commencé |
| A6 | Liquidity shock propagation | Depth/spread shocks on leaders propagate with measurable impulse response. | slow cross-venue repricing | pas commencé |
| A7 | Liquidation cascade | Forced liquidations become nonlinear relative to available depth and OI. | leveraged forced flow | pas commencé |
| A8 | Leverage topology | Joint price/OI/funding/mark-index premium state distinguishes new leverage from deleveraging. | crowded leverage | pas commencé (piste "résiduel hedgé, pas racheter le rebond" — voir A7/A8) |
| A9 | Funding and executable perp-spot basis convergence | Extreme executable perp-vs-spot basis/funding deviations converge under arbitrage capital. | perp carry payers | pas commencé — déjà proche de ce qui a été épuisé (carry simple), ne pas reprendre sans angle neuf |
| A10 | Funding settlement event | Funding boundaries create predictable inventory adjustment around settlement. | funding-sensitive inventory | pas commencé |
| A11 | Informed wallet flow | Persistent public-wallet markout identifies informed flow before broad repricing. | less-informed counterparties | pas commencé (HL wallets = cas d'usage naturel) |
| A12 | Cross-asset causal propagation | Leader innovations predict follower residual returns. | slow cross-asset repricing | pas commencé |
| A13 | Residual relative value | Factor-neutral residual divergence mean-reverts independent of market beta. | temporary inventory imbalance | pas commencé |
| A14 | Options surface shock | IV/skew/term shocks predict hedging pressure and realized-vol repricing. | convexity rehedging | pas commencé |
| A15 | On-chain exchange flow | Exchange/stablecoin inventory changes shift future supply-demand. | slower settlement-layer responders | pas commencé |
| A16 | Execution alpha | Queue state and flow predict fill probability and post-fill adverse selection. | immediacy demanders | pas commencé |
| — | Phase 5.2 — OKX queue imbalance → LOO fair value | `okx__queue_imbalance_l5` @ 30s prédit Binance+Bybit+HL | — | `CLOSED_NO_EDGE` ❌ à EXECUTION_ECONOMICS (2026-08-28). Hors catalogue V5 (héritage `market_physics_v3/phase5_mechanism.py`, pas de LabSpec A-numéroté propre — ne pas lui inventer un numéro). |

Note sur les libellés informels utilisés avant cette correction ("dynamic leader/follower",
"aggressive-flow propagation", "spot/perp lead-lag", etc.) : ce sont des reformulations, pas
des labs distincts. Les plus proches équivalents catalogue sont A1 (leadership), A5/A6 (flux
et chocs de liquidité cross-venue), A9 (pas A7/A8, un lead-lag spot/perp général n'a pas
d'équivalent exact — nécessiterait un nouveau LabSpec s'il est retenu), A11 (wallets), A12
(cross-asset), A14 (options). Ne plus utiliser ces reformulations — citer le numéro catalogue.

## A2 — détail

`DEV_DISCOVERY` ✅ (8/8 horizons, budget famille 8/8 consommé, p=0.004975 = plancher à 200
permutations). IC 0.195 @ 2s (0.127 @ 100ms → 0.104 @ 30s). Figé sur `dev_6h`
(`/home/qbee/futur-alpha-foundry-v5`, branche `research/alpha-foundry-v5`, désormais mergée
dans `main`). Preuve scellée commitée (`reports/alpha_foundry_v5/`).

Bloqué sur deux choses concrètes avant `INDEPENDENT_CONFIRMATION` :
1. Le code du stage `INDEPENDENT_CONFIRMATION` n'existe pas encore dans
   `alpha_foundry_v5/research_engine.py` (contrats/gates/registry déjà codés, `run_confirmation`
   manquant — même trou que trouvé pour Phase 5.2 avant qu'on lui construise son
   `EXECUTION_ECONOMICS`).
2. Pas encore ≥24h de données jamais vues, sealed. La seule fenêtre postérieure à `dev_6h`
   disponible (12h, `research/market-physics-data-v3`) est maintenant libre d'usage (verdict
   Phase 5.2 scellé le 2026-08-17) mais insuffisante seule — il faut soit l'étendre, soit
   collecter une fenêtre neuve dédiée ≥24h.

Ne pas retoucher le modèle A2 en attendant — data snooping.

## Phase 5.2 — post-mortem (edge trouvé oublié, testé, tué proprement)

`okx__queue_imbalance_l5` @ 30s, target LOO fair value Binance+Bybit+HL, avait un verdict
`CONFIRMED_INFORMATION_CANDIDATE` scellé le 2026-08-17 sur `research/market-physics-data-v3`
(branche mergée dans `main` le 2026-08-28), jamais poussé plus loin. Chiffré le 2026-08-28,
méthodologie corrigée le 2026-08-29 (seuils gelés depuis DEV_PILOT, PnL sur prix réels
bid/ask à poids d'entrée figés, capacité = jambe limitante, fill_rate calculé) : gross edge
+0,38bps/trade, coûts (frais taker uniquement, le spread est déjà dans le PnL réel) → **net
-4,44bps, profit factor 0,032**. Verdict inchangé malgré la correction — fermé, pas à
retenter avec des coûts plus favorables. Détail :
`reports/market_physics_v3/phase5_2_execution_economics/VERDICT.md`.

## Comment ajouter une piste

1. Une ligne par **famille économique**, pas par variante testée (ex : A2 = 1 ligne malgré
   8 horizons — ce sont 8 expressions temporelles du même mécanisme, pas 8 edges).
2. Statut avancé seulement avec preuve : nombre de folds OOS positifs, p/q-value, et si le
   test a frappé le plancher de résolution (`1/(n_permutations+1)`) — le signaler explicitement,
   ça ne veut pas dire "plus significatif que ça".
3. `EXECUTION_ECONOMICS` doit chiffrer fees + spread + slippage + latence avant `PAPER_LIVE` —
   le plus gros IC brut n'est pas nécessairement le meilleur net alpha (cf. Phase 5.2 : IC réel
   mais net très négatif).
