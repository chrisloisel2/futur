# Edge Factory — six pistes indépendantes

Programme de recherche multi-edges, **sans modification du shadow actuel**
(`scripts/paper_*_signal.py`, flags dans `config/strategy_flags.py` — gelés).

Objectif : fermer un gap de ~8,1 points mensuels avec six familles d'edges.
Budget de recherche moyen : **~1,35 %/mois marginal par famille** (cible de
recherche, pas une performance supposée).

## Les six pistes

| # | Edge | Signal | Horizon | Statut données |
|---|------|--------|---------|----------------|
| 1 | [ctrend/](ctrend/) — Cross-sectional trend | Classement 30–80 cryptos liquides par tendance prix-volume multi-horizon ; long top-K, cash si régime défavorable | 1–7 j | ✅ klines fapi + univers archivé quotidiennement |
| 2 | [liquidation_exhaustion/](liquidation_exhaustion/) — Rebond post-cascade | Cascade vendeuse + chute OI + funding normalisé + profondeur reconstruite + OFI/CVD retourné → long de récupération | 1–8 h | ⚠️ liquidationSnapshot Binance Vision à télécharger ; OI/funding archivés |
| 3 | [top_traders/](top_traders/) — Divergence top traders | Ratios comptes vs positions des top 20 % (marge), croisés retail/OI/funding/CVD | 15 min–24 h | ✅ **archivage live démarré le 2026-07-17** (`bin/archive-derivs`) |
| 4 | [onchain_flows/](onchain_flows/) — Flux on-chain | ETH/USDT vers exchanges, accumulation whales, divergences prix-flux | 1–6 h | ⚠️ scrapers whale_data existants à auditer |
| 5 | [hyperliquid_metaorders/](hyperliquid_metaorders/) — Metaorders publics | TWAP/vaults/gros wallets Hyperliquid : continuation pendant, reversion après, lead-lag vers Binance | 1 min–6 h | ❌ collecteur à construire |
| 6 | [l2_execution/](l2_execution/) — Edge d'exécution L2 | Maker/taker, retard, annulation selon liquidité, queue imbalance, OFI, phase quart-d'heure | s–15 min | ❌ overlay uniquement, sur edges déjà positifs |

## Ordre d'exécution

1. ✅ Shadow gelé (aucune modification).
2. 🔄 CTREND et liquidation testés avec les données existantes/téléchargeables.
3. ✅ Archivage top-trader démarré immédiatement (rétention API ~30 j —
   chaque jour non archivé est perdu). Voir `data/raw/binance_futures_positioning/`.
4. On-chain et Hyperliquid sur branche séparée.
5. Overlay L2 uniquement sur les edges déjà positifs.
6. Combinaison sous gates (ci-dessous).

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
