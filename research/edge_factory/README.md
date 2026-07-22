# Edge Factory — onze pistes indépendantes

Programme de recherche multi-edges, **sans modification du shadow actuel**
(`scripts/paper_*_signal.py`, flags dans `config/strategy_flags.py` — gelés).

Objectif : fermer un gap de ~8,1 points mensuels. Budget de recherche
moyen : **~1,35 %/mois marginal par famille validée** (cible de recherche,
pas une performance supposée). Le but n'est **pas** 11 stratégies qui
semblent bonnes : c'est **3 à 5 moteurs réellement décorrélés**, chacun
positif après coûts ×2, délai d'une barre, OOS, DSR/PBO, et avec
contribution marginale positive au portefeuille actuel. Les pistes sont
des candidats, pas des promesses.

## Les onze pistes

| # | Edge | Signal | Horizon | Rôle | Statut données |
|---|------|--------|---------|------|----------------|
| 1 | [ctrend/](ctrend/) — Cross-sectional trend | Classement 30–80 cryptos liquides par tendance prix-volume multi-horizon ; long top-K, cash si régime défavorable ; grille 6 h/24 h/hebdo | 6 h–7 j | Alpha | ✅ klines fapi + univers archivé quotidiennement |
| 2 | [liquidation_exhaustion/](liquidation_exhaustion/) — Rebond post-cascade | Cascade vendeuse + chute OI + funding normalisé + profondeur reconstruite + OFI/CVD retourné → long de récupération | 1–8 h | Alpha | ⚠️ liquidationSnapshot Binance Vision à télécharger ; OI/funding archivés |
| 3 | [top_traders/](top_traders/) — Divergence top traders | Ratios comptes vs positions des top 20 % (marge), croisés retail/OI/funding/CVD | 15 min–24 h | Filtre/score | ✅ **archivage live démarré le 2026-07-17** (`bin/archive-derivs`) |
| 4 | [onchain_flows/](onchain_flows/) — Flux on-chain vers CEX | USDT→CEX (risk-on, le mieux documenté), ETH→CEX (baissier), BTC→CEX (vol), cohortes whales | 1–6 h | Alpha + régime | ⚠️ scrapers whale_data existants à auditer |
| 5 | [hyperliquid_metaorders/](hyperliquid_metaorders/) — Metaorders publics | TWAP/vaults/gros wallets Hyperliquid : continuation pendant, reversion après, lead-lag vers Binance | 1 min–6 h | Alpha | ❌ collecteur à construire |
| 6 | [l2_execution/](l2_execution/) — Edge d'exécution L2 | Maker/taker, retard, annulation selon liquidité, queue imbalance, OFI, phase quart-d'heure | s–15 min | Overlay | ❌ overlay uniquement, sur edges déjà positifs |
| 7 | [options_flow/](options_flow/) — Flux options directionnels | Δ skew 25Δ, pression d'achat OTM signée, blocs Deribit, concentration strikes, term structure IV | 4 h–7 j | Alpha + régime | ✅ `deribit_options_summary` archivé 4 h ; chaîne complète + blocs à ajouter |
| 8 | [basis_dispersion/](basis_dispersion/) — Basis & dispersion inter-exchange | Basis spot-perp, funding extrême, prime Binance–Bybit–Hyperliquid, carry delta-neutre conditionnel | min–jours | Carry + filtre stress | ✅ funding Binance/Bybit archivés ; basis reconstructible ; Hyperliquid à ajouter |
| 9 | [protocol_fundamentals/](protocol_fundamentals/) — Rotation fondamentale | Accélération fees/revenus, P/S on-chain, TVL nette, unlocks, volume DEX — orthogonal marché/taille/momentum | 1–4 sem | Alpha lent | ❌ collecteur DefiLlama quotidien à construire |
| 10 | [lst_pegs/](lst_pegs/) — Pegs LST & convergence | Décote stETH/cbETH/wbETH hedgée perp, file de rachat, micro-depegs stables | h–sem | Défensif | ❌ prix pools + file Lido à collecter |
| 11 | [stablecoin_regime/](stablecoin_regime/) — Régime stablecoin | Accélération mint/burn USDT+USDC, réserves CEX, depeg monitor, dominance stable, imbalance DEX | 1 h–7 j | Meta-signal (sizing) | ⚠️ DefiLlama stablecoins à collecter ; `coingecko_global` archivé |

## Verdicts — campagne de falsification 2026-07-17/18

Tests **pré-enregistrés** exécutés sur la machine de recherche
(`qbee@100.127.59.114`, repo `~/futur`, verdicts commités là-bas).
Protocole commun : hypothèse et seuils déclarés avant tout calcul,
verdict binaire sur le primaire, 0 retuning, coûts ×1/×2, exécution
barre suivante, stabilité inter-moitiés, drop des 10 plus gros événements.

| Piste | Verdict | Détail | Commit |
|---|---|---|---|
| 1 ctrend | ❌ **REJECTED** | v1 univers point-in-time (786 délistés inclus) : CAGR −2,1 % — le v0 positif était du biais de survivance | `859ebad` |
| 2 liquidation_exhaustion | ❌ **NO_EDGE** | Liquidations réelles cm 16 mois : setup complet −0,28 %/evt, la confirmation CVD retourne le signe ; confirmé côté basis (premium bas → ça continue de baisser) | `9088b2c` |
| 3 top_traders | ❌ **NO_EDGE** | Découverte : ratios historiques dans Vision metrics depuis 2021-12 (pas besoin d'attendre l'archive). Panel 49 actifs, 1,4 M obs : effet fort pré-2024 (t = 14,8) **mort depuis** (t = −0,75) | `7ef8d83` |
| 4 onchain_flows | ⚠️ **NOT_TESTABLE** | Mongo whale vide, aucun flux CEX historique, pas de backfill gratuit possible. Décision : collecteur live (rééval 60-90 j) ou drop | `53d6f70` |
| 7 options_flow | ❌ **NO_EDGE** | 3,3 ans de trades Deribit BTC, flux OTM signé + 6 secondaires : t = 0,28, tout est mort au grain quotidien | `fdfa862` |
| 8 basis_dispersion | ❌ **NO_EDGE** (directionnel) | Funding extrême en niveau, panel 9 actifs × 5,4 ans : la continuation domine (bucket z>2 : +0,50 %). Restent de la famille : STRESS_GATE = VALIDATED_SIGNAL (parqué, non câblé) | `a589e54` |
| 9 protocol_fundamentals | ❌ **NO_EDGE** | Accél. fees/revenus DefiLlama, 19 protocoles × 228 semaines : spread −0,11 %/sem, IC négatif — malgré le biais de survivance favorable | `eef8646` |

**Pistes encore vivantes** : 5 hyperliquid_metaorders (collecteur à
construire — seul candidat alpha restant non testé), 10 lst_pegs et
11 stablecoin_regime (non testées, défensif/meta), 6 l2_execution
(overlay, conditionné à un moteur positif). Le socle validé reste
**Portfolio V1.1 : +4,8 %/an, DD 1,9 % sur 3,6 ans** (machine distante).

## Ordre d'exécution (après falsification)

1. ✅ Shadow gelé (aucune modification).
2. ✅ Campagne de falsification terminée — 6 verdicts rendus (table ci-dessus).
3. **Hyperliquid metaorders** : construire le collecteur (twapHistory,
   vaults, gros wallets) — l'historique se constitue en le collectant.
4. **On-chain flows** : trancher collecteur live vs drop.
5. lst_pegs / stablecoin_regime : défensif — seulement si un moteur alpha
   émerge, pour lisser.
6. Overlay L2 : en dernier, sur edge positif uniquement.
7. Ne **rouvrir** une piste fermée que sur thèse nouvelle et précise
   (ex. options intraday tick, top-traders si changement de régime
   documenté) — jamais en re-balayage des mêmes hypothèses.

## Gates de validation (communs à toutes les familles)

Un edge n'entre en shadow que si **tous** les critères passent :

- **DSR ≥ 95 %** (Deflated Sharpe Ratio, corrigé du nombre d'essais) ;
- **PBO ≤ 10 %** (Probability of Backtest Overfitting, CSCV) ;
- **coûts ×2 positifs** (doubler fees + slippage, PnL net > 0) ;
- **robustesse** : survit à une barre de délai d'exécution, et à la
  suppression des 10 plus gros événements (cascades, trades) ;
- **indépendance** : `|corrélation PnL| ≤ 0,35` avec chaque famille déjà
  retenue ; les variantes fortement corrélées comptent comme une seule famille ;
- **contribution marginale nette** positive au portefeuille combiné.

## Conventions

- Chaque piste = un sous-dossier avec `README.md` (spec du signal, données,
  résultats datés) + scripts versionnés `*_v{n}.py`.
- Données brutes dans le lake `data/raw/` via `data_pipeline.storage`
  (parquet partitionné année/mois) — jamais de CSV ad hoc.
- Résultats d'expériences : JSON daté dans `<piste>/results/`.
- Aucune piste n'écrit dans `strategies/`, `production/` ou `config/`
  tant que les gates ne sont pas passés.
