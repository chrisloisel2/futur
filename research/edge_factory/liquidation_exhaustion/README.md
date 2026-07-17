# Piste 2 — Liquidation exhaustion (rebond post-capitulation)

Ne rouvre pas le short. Exploite le rebond après capitulation :

```text
cascade vendeuse extrême
+ chute brutale OI
+ funding remis à zéro
+ absorption des ventes
+ profondeur bid reconstruite
+ OFI/CVD retourné
= entrée long de récupération
```

Horizon : 1–8 h. Tester séparément BTC, ETH, SOL et alts liquides.

## Données

- **Liquidations historiques** : `data.binance.vision/data/futures/um/daily/liquidationSnapshot/{symbol}/`
  (fichiers quotidiens, ordres forceOrder) — à télécharger et intégrer au lake.
- **OI + funding** : archivés en 5 m par `bin/archive-derivs`
  (`data/raw/binance_futures_positioning/`) ; historique long via
  `binance_futures_funding` du registre de sources.
- **CVD/OFI** : reconstructibles depuis aggTrades Binance Vision
  (taker_buy_volume déjà dans le dataset 1 m BTC).
- **Profondeur bid** : pas d'historique L2 public long — proxy : Amihud +
  range/volume au début, L2 live plus tard (piste 6).

## Protocole de rejet

L'edge est rejeté si le PnL disparaît avec :

- une barre de délai entre signal et entrée ;
- coûts ×2 ;
- suppression des dix plus grosses cascades de l'échantillon.
