# Piste 3 — Top-trader divergence

Binance publie séparément, pour les comptes appartenant aux 20 % ayant la
marge la plus élevée :

- `topLongShortAccountRatio` — ratio par **nombre de comptes** ;
- `topLongShortPositionRatio` — ratio par **taille de positions** ;
- `globalLongShortAccountRatio` — ratio retail global ;
- `takerlongshortRatio` — flux taker buy/sell ;
- `openInterestHist` — OI en contrats et en valeur.

**Rétention API : ~30 jours.** L'archivage continu a démarré le 2026-07-17 via
`bin/archive-derivs` (module `data_pipeline/derivatives_positioning.py`),
top 40 symboles par volume, granularité 5 m, format large dans
`data/raw/binance_futures_positioning/futures_um/{symbol}/5m/`.

## Quatre sous-signaux à tester

1. **Lead** : les top traders deviennent longs avant le retail
   (Δ `ls_ratio_top_accounts` devance Δ `ls_ratio_global`).
2. **Conviction** : `ls_ratio_top_positions` monte sans hausse de
   `ls_ratio_top_accounts` → moins de comptes mais plus gros ; taille
   moyenne en hausse.
3. **Distribution** : top traders réduisent pendant que le retail continue
   d'acheter (divergence top ↓ / global ↑) → signal de retournement baissier.
4. **Contrarian extrême** : consensus extrême top+retail simultané, croisé
   avec funding/OI extrêmes → fade.

Un seul sous-signal validé compte comme edge. Les variantes fortement
corrélées restent une même famille.

## Contraintes

- Horizons cibles : 15 min à 24 h.
- Ne pas backtester au-delà de la fenêtre archivée (pas d'historique long
  disponible — c'est précisément pourquoi l'archivage devait démarrer tôt).
  Accumuler ≥ 60–90 jours avant première évaluation sérieuse.
- Croiser avec CVD (déjà dans les features 1 m) et liquidations (piste 2).
