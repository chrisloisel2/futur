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

## Familles (10 mécanismes économiques, pas 10 modèles)

| # | edge | mécanisme | statut |
|---|---|---|---|
| A2 | Venue dislocation convergence | un exchange prend du retard puis converge | `DEV_DISCOVERY` ✅ — voir détail ci-dessous |
| A3 | Dynamic leader/follower | quel venue découvre le prix maintenant, trader les followers | pas commencé |
| A4 | Aggressive-flow propagation | flux agressif venue leader → mouvement retardé ailleurs | pas commencé |
| A5 | Book depletion propagation | disparition de profondeur → mouvement cross-venue | pas commencé |
| A6 | Hyperliquid informed-wallet flow | wallets HL précédant Binance | pas commencé |
| A7 | Spot ↔ perp lead/lag | mouvement non simultané spot/perp | pas commencé |
| A8 | Liquidation residual reversal | cascade + OI drop, rendement résiduel hedgé (pas juste "racheter le rebond" — ça a déjà échoué) | pas commencé |
| A9 | Funding-settlement microstructure | inefficience d'exécution autour du settlement, pas le carry lui-même | pas commencé |
| A10 | Cross-sectional shock propagation | BTC/leader bouge → alts réagissent avec retard, 500ms-30s | pas commencé |
| A11 | Options → perp information | skew/IV/flow options avant déplacement du perp | pas commencé |
| — | Phase 5.2 — OKX queue imbalance → LOO fair value | `okx__queue_imbalance_l5` @ 30s prédit Binance+Bybit+HL | `CLOSED_NO_EDGE` ❌ à EXECUTION_ECONOMICS (2026-08-28) |

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
(branche mergée dans `main` le 2026-08-28), jamais poussé plus loin. Chiffré le 2026-08-28 :
gross edge +0.64bps/trade, coût aller-retour réel (frais taker pondérés 3 venues + spread réel)
~9.9bps → **net -9.25bps, profit factor 0.006**. Écart structurel (~15x), pas un artefact de
modélisation — fermé, pas à retenter avec des coûts plus favorables. Détail :
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
