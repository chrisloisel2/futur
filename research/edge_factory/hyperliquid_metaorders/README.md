# Piste 5 — Metaorders publics Hyperliquid (branche séparée)

Les TWAP publics et positions on-chain d'Hyperliquid exposent des intentions
et trajectoires d'exécution. Une étude récente reconstruit des millions de
metaorders et mesure des comportements différents entre ordres cachés et
TWAP visibles (https://arxiv.org/abs/2606.15715).

Horizon : 1 min–6 h.

## Sous-signaux

1. **Continuation** pendant l'exécution d'un TWAP/metaorder visible.
2. **Accélération** quand plusieurs wallets convergent dans le même sens.
3. **Reversion** après achèvement de l'ordre.
4. **Lead-lag** Hyperliquid → Binance/Bybit.

## Collecteur à construire

- API info Hyperliquid : `twapHistory`, états des vaults, positions des
  gros wallets (tout est public on-chain).
- Archiver en continu (même logique que `bin/archive-derivs`) : ces états
  sont des snapshots — l'historique se constitue en le collectant.
