# EDGE_DATA_CATALOG — quelles données peuvent contenir un edge brut de 10 à 100 bps

*2026-09-10, branche `p2-edge-data-acquisition`. Après P0 (0 sleeve validée) et P1/P1.1 (mur de coût
9,5-11,3 bps aller-retour au VIP0 ; cross-exchange fermé ; maker-only institutionnel = market making).
Règle : une donnée qui ne peut pas produire **3× le coût** est rejetée avant tout modèle.*

## Top 3, classés par probabilité de contenir un edge

| rang | dataset | propriété qui le rend candidat | état sur ce disque | brut visé |
|---|---|---|---|---|
| **1** | **Official event tape** — listings / delistings / listings perp / launchpool / statuts | *contrainte forcée* (inclusion obligatoire, retail à levier à l'ouverture) + *arrive avant une partie du marché* (publication ≠ ouverture) | **7 449 enregistrements**, 5 sources officielles, historique Binance/OKX/Bybit ; `trading_start_ts` extrait pour 889 ; aucune jointure prix encore | ≥ 30 bps |
| **2** | **Forced-flow tape** — liquidations en direct + BBO/mark/index/funding *d'avant* | *ordre forcé* : ni le timing ni le côté ne sont choisis ; la famille cascade est morte de latence (45-48 h), pas de mécanisme | collecteur validé (dry-run : 28 liquidations / 45 s, enrichissement ≥ 0 ms avant l'événement), service à démarrer ; Binance throttle 1/s/symbole = borne basse | ≥ 10 bps |
| **3** | **Account actual execution** — frais, fills, part maker, rejets, latence du compte | *account-specific* : ce n'est pas une source d'alpha, c'est ce qui décide si un alpha est monétisable | lecteurs read-only écrits et inertes sans clé ; aucune clé lue | — (coût réel à battre) |

Ensuite seulement : **L2 / file d'attente sur instruments à tick large** (P1 : le seul cas rouvrable est maker-only ;
inutile sans `expected maker value = spread gagné − adverse selection − fills manqués − frais`) et la
**stablecoin stress tape** (gros mouvements possibles, validation longue ; le protocole STABLECOIN_REGIME v0
a déjà tourné une fois ici).

## Hard rejection list — ce qu'on n'acquiert plus

| donnée | pourquoi c'est mort ici (mesuré) |
|---|---|
| OHLCV 1h/5m, features de bougies | 138 essais effectifs, rien au-dessus de la médiane du bruit (2,81) ; `close_location_20` inverse son signe de 50 à 696 symboles |
| funding public seul | mesuré en niveau, en écart cross-venue et en structure par terme : rien ; Bitfinex refusé pour le même payeur |
| long/short ratio Binance seul, top-trader ratio | famille positionnement : 88 essais, t 2,399 contre 2,955 ; indécidable, scellé pour 2028-12-06, pas une donnée neuve |
| BBO BTC/ETH seul | P0 : microstructure 0,78 bps brut contre 12,55 de coût (×16) ; cross-exchange 1,97 contre 24 (×12) |
| basis perp/spot | 16 signaux, meilleur t 2,49, corrélé 0,69 au positionnement ; CME segmentation t_net 1,58 < 1,96 |
| ML sans donnée nouvelle | interdit par `LEGACY_FREEZE` ; un modèle ne trouve pas ce que la donnée ne contient pas |

## Ce que chaque enregistrement doit porter (règle P2)

`source` · horodatage **exchange** (ou `null` explicite) · horodatage **local** · `raw_hash`. Sans ces quatre
champs, la donnée n'existe pas. Tests : `test_event_tape.py`, `test_forced_flow_tape.py`.
