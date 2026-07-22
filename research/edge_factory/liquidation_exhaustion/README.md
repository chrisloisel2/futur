# Piste 2 — Liquidation exhaustion (rebond post-capitulation)

> ## ❌ VERDICT : NO_EDGE (2026-07-17, commit distant `9088b2c`)
>
> Testé en pré-enregistré sur liquidations **réelles** cm (Vision,
> 257 874 événements, fenêtre utile 2023-06 → 2024-10) avec exactement
> cette spec : cascade P99 + chute OI −2 %/4 h + funding z ≤ 0,5 +
> absorption + flip taker → long open t+1, hold 8 h. Résultat : setup
> complet **−0,28 %/evt** (la confirmation CVD retourne le signe) ;
> coûts ×2 et drop-top10 aggravent. Confirmation indépendante côté
> basis (2026-07-17) : premium très bas → la baisse *continue* à 24 h.
> Découvertes data : liquidationSnapshot um retiré de Vision (cm only),
> publication cm stoppée 2024-10-14. L'edge cascade résiduel est dans
> l'*entrée pendant le stress* (moteurs 5 min existants), pas le rebond.

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

## Ce que dit la recherche

- Théorie des valeurs extrêmes sur données BitMEX : **3,51 % des longs et
  1,89 % des shorts liquidés par jour**, levier moyen des liquidés ≈ 60× —
  les liquidations forcées sont un flux matériel et récurrent, pas un
  événement rare.
- Étude d'événement du crash du 10-11 octobre 2025 : boucles réflexives
  levier ↔ liquidité ↔ volatilité ; les canaux microstructurels
  (retrait de profondeur, ADL, épuisement des fonds d'assurance)
  amplifient le mouvement — voir aussi *Risk-Based Auto-Deleveraging*
  (https://arxiv.org/abs/2603.15963) et *Autodeleveraging: Impossibilities
  and Optimization* (https://arxiv.org/abs/2512.01112).
- *Perpetual Futures and Basis Risk* (AEA 2026) : les crashes pilotés par
  liquidations produisent des **spikes de basis négatifs à récupération
  lente** — le basis est donc un marqueur de purge en cours *et* un
  confirmateur de fin de purge (recoupe la piste 8).
- Épisodes récents utilisables comme cas d'étude : oct. 2025 (purge
  historique, ADL multi-venues), 30 janv. 2026 (> 2,5 G$ liquidés en une
  journée), fév. 2026 (OI −20 % en quelques sessions). Le déleveraging
  « ordonné » (fév. 2026) et la capitulation désordonnée (oct. 2025) ne se
  tradent pas pareil — le signal doit les distinguer (vitesse de la purge,
  profondeur du carnet, comportement du basis).

## Protocole de rejet

L'edge est rejeté si le PnL disparaît avec :

- une barre de délai entre signal et entrée ;
- coûts ×2 ;
- suppression des dix plus grosses cascades de l'échantillon.
