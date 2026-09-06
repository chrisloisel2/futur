# Préenregistrement — liquidation_relative_reversal_v1

Écrit avant tout accès aux données de cascade/liquidation au-delà de ce qui
est déjà connu de l'inventaire momentum (PIT, funding), avant tout calcul
d'edge. Autorisation reçue (2026-07-22, décision humaine) : préenregistrement
et inventaire de données seulement à ce stade — voir `## Statuts
d'autorisation` en bas de ce document.

## Distinction du prior art (obligatoire avant de commencer)

Cette piste **n'est pas** une réouverture de `liquidation_exhaustion`
(piste 2, `NO_EDGE`, commit `9088b2c`). Ce précédent testait un mécanisme
différent : cascade + chute OI + funding remis à zéro + absorption + CVD
retourné → **long directionnel de récupération sur le MÊME actif**, sans
hedge. Résultat : `−0,28 %/évt`, la confirmation CVD retourne le signe,
confirmé indépendamment côté basis (le premium bas prolongeait la baisse,
pas une récupération).

`liquidation_relative_reversal_v1` est structurellement différent : ce
n'est pas un pari directionnel sur le rebond de l'actif liquidé, c'est un
pari de **valeur relative market-neutral** — long l'actif disloqué, short
un hedge bêta BTC/ETH roulant, sur le **spread résiduel** après
neutralisation du mouvement de marché commun. Un actif peut continuer de
baisser en absolu (comme piste 2 l'a trouvé) tout en sur-performant son
hedge bêta — les deux mécanismes ne se contredisent pas et doivent rester
falsifiés séparément.

## Leçons portées depuis les pistes précédentes (pas ignorées, appliquées)

- **Binance Vision `liquidationSnapshot` est cm (coin-margined) UNIQUEMENT**
  — retiré pour usdm, publication cm elle-même arrêtée le 2024-10-14
  (découverte piste 2). Le détecteur primaire de cette piste ne peut donc
  **pas** dépendre des liquidations déclarées comme condition obligatoire
  historique — c'est explicitement la raison de la définition causale en
  4 familles de signaux (§3 ci-dessous), les liquidations déclarées ne
  servant que de confirmation quand disponibles.
- **CVD/OFI reconstructibles depuis aggTrades** (déjà noté piste 2) —
  réutiliser cette reconstruction plutôt que d'en bâtir une nouvelle.
- **Pas d'historique L2 profondeur publique long** (déjà noté piste 2) —
  la profondeur réellement exécutable pendant le stress doit être un
  inventaire honnête (`BLOCKED_...` probable), pas une supposition ; les
  travaux récents montrent que profondeur affichée et liquidité cachée
  divergent précisément pendant les épisodes de stress (Lim, *Hidden
  Liquidity, Displayed Depth, and Execution Risk*).
- **Jamais un timestamp exact-ms comme preuve de simultanéité**
  (`settlement_timestamp_alignment_v1`, réutilisé par toutes les pistes
  cross-source cette session) — s'applique à tout recoupement
  OI/liquidations/aggTrades/mark/index.
- **Univers PIT + InstrumentMaster déjà corrigés pour momentum** — réutiliser
  `build_pit_universe.py` et le membership déjà construit plutôt que d'en
  refaire un ; adapter seulement les filtres (§5).
- **Un résultat négatif OU positif spectaculaire est suspect avant d'être
  cru** (leçon retenue de l'audit momentum, 2026-07-21/22) — s'applique
  dès la Phase 1 événementielle : vérifier direction/alignement/comptabilité
  avant tout verdict.

## Hypothèse primaire

```yaml
experiment_id: liquidation_relative_reversal_v1

hypothesis: >
  après une liquidation forcée importante sur un altcoin, si son perpétuel
  sous-performe anormalement un hedge BTC/ETH, si l'open interest chute, si
  le volume explose, et si la baisse cesse d'accélérer, alors le spread
  résiduel se rétablit partiellement dans les 60 minutes suivantes

direction: >
  long altcoin perpetual ; short rolling-beta hedge composé de BTC et ETH
  perpetuals

primary_horizon_minutes: 60
secondary_horizons_minutes: [30, 120]
secondary_results_cannot_rescue_primary: true

no_leverage_in_primary_test: true
```

## 2. Les jambes exactes

```text
Jambe A — long ALTUSDT perpetual (actif disloqué)
Jambe B — short BTCUSDT perpetual × bêta_BTC estimé avant l'événement
Jambe C — short ETHUSDT perpetual × bêta_ETH estimé avant l'événement
```

Contraintes de construction :

```text
exposition dollar nette ≈ 0
bêta BTC résiduelle ≈ 0
bêta ETH résiduelle ≈ 0
gross exposure fixée
aucune jambe construite avec des données futures à l'événement
```

## 3. Définition causale d'une cascade (détecteur primaire)

Quatre familles de signaux, pas les liquidations déclarées comme condition
obligatoire (raison : couverture historique incomplète, voir leçons
ci-dessus) :

```text
1. rendement résiduel extrême (défini identiquement au §4)
2. baisse d'open interest
3. explosion de volume agressif (taker)
4. divergence perp/spot ou mark/index
```

Déclenchement primaire, barres 5 minutes, toutes conditions réunies :

```text
residual_return_30m <= percentile causal 1%
open_interest_change_30m <= percentile causal 5%
aggressive_volume_30m >= percentile causal 95%
perp_spot_basis_change_30m <= percentile causal 5%
```

Chaque percentile : par actif, historique expanding, `shift(1)`, warm-up
minimal fixé avant tout résultat. Seuils **non partagés en valeur brute**
entre actifs (hétérogénéité documentée entre contrats sur la dynamique des
cascades — Guo, *Cross-coin Heterogeneity in Liquidation Cascade
Dynamics*).

Les liquidations déclarées servent de **confirmation seulement**, quand la
couverture le permet (voir `DATA_INVENTORY.yaml`) — jamais une condition
bloquante du détecteur historique.

### Condition d'entrée (jamais au moment du déclenchement)

```text
deux barres 5m consécutives sans nouveau plus bas résiduel
ET open interest ne baisse plus de >1% supplémentaire
ET basis perp/spot ne se détériore plus
```

## 4. Bêta hedge

```text
régression rolling, rendements horaires, fenêtre 30 jours
variables : BTC et ETH
coefficients décalés de 1 heure (jamais la période événementielle elle-même)

r_alt = alpha + beta_BTC * r_BTC + beta_ETH * r_ETH + erreur

r_residual = r_alt - beta_BTC * r_BTC - beta_ETH * r_ETH
```

Cette définition unique sert à la fois au test événementiel (Phase 1) et
au PnL du moteur (Phase 2) — jamais deux définitions différentes.

## 5. Univers primaire

```text
actifs crypto PIT Binance (réutilise build_pit_universe.py / InstrumentMaster)
actif réellement listé à la date t
historique minimal 90 jours
volume médian 30j à t-1 >= 20 M$/jour
open interest disponible
spot ou index de référence disponible
BTC et ETH exclus des actifs candidats (réservés au hedge)
stablecoins et tokenized assets exclus (déjà filtrés côté momentum)
aucun forward-fill après délisting
```

## 6. Documents requis (ce préenregistrement + les deux suivants)

```text
research/edge_factory/liquidation_relative_reversal_v1/
├── PREREGISTRATION.md   (ce document)
├── DATA_INVENTORY.yaml  (étape suivante — statut réel par actif/période)
└── PROVENANCE.md        (étape suivante — source exacte, méthode de collecte,
                           limites connues, commande de reproduction par type
                           de donnée)
```

`DATA_INVENTORY.yaml` doit couvrir, par actif et période : perp 5m OHLCV,
aggTrades/trades, spot/index 5m, mark price 5m, open interest 5m, funding,
liquidations déclarées, listing/delisting, fees, tick size, lot size.

Statuts autorisés après inventaire :
`DATA_READY`, `DATA_READY_WITH_INFERRED_LIQUIDATIONS`, `COLLECTOR_REQUIRED`,
`BLOCKED_BY_OI_HISTORY`, `BLOCKED_BY_REFERENCE_SPOT`, `NOT_REPRODUCIBLE`.

## 7. Phase 1 — preuve événementielle (sans portefeuille)

Par événement : `entry_timestamp, entry_alt_price, entry_BTC_price,
entry_ETH_price, beta_BTC, beta_ETH, residual_return_{30,60,120}m,
estimated_round_trip_cost, net_residual_return`.

Variable primaire : **net residual return à 60 minutes**.

### Gate primaire (toutes conditions requises)

```text
mean net residual return > 0
bootstrap calendar-block 95% lower bound > 0
Newey-West coefficient > 0, p < 0.05
median net return > 0
cost x2 mean return > 0
```

### Robustesse obligatoire

```text
première moitié > 0 ; deuxième moitié > 0
leave-one-year-out toujours > 0
leave-one-asset-out toujours > 0
leave-one-event-cluster-out toujours > 0
aucun actif > 20% du PnL total
aucun épisode > 20% du PnL total
```

Aucun test de seuil alternatif ne peut sauver un primaire échoué.

## 8. Phase 2 — moteur multi-jambes (conditionnel au succès de la Phase 1)

Exécution : taker sur les 3 jambes (scénario central), latence commune
500 ms + latence stress 2 s, slippage sur profondeur réellement
exécutable (jamais la profondeur affichée seule), échec partiel d'une
jambe, legging risk, funding pendant la détention, fees ×1 et ×2.

Sortie primaire : fixe à 60 minutes (pas de take-profit optimisé). Stop
primaire : sortie si perte résiduelle atteint 1,5× l'amplitude résiduelle
observée avant l'entrée, calculé à l'entrée, ne bouge pas.

## 9. Gates portefeuille (Phase 2)

```text
CAGR net > 10%
Sharpe > 1.5
maxDD < 10%
cost x2 positif
slippage stress positif
legging failure stress survivable
DSR > 0.80
PBO <= 0.10
aucune année complète fortement négative
aucun actif > 20% du PnL
```

Entrée en paper seulement si : event edge PASS, portfolio edge PASS,
`promotion_gate` PASS, config hash verrouillé
(`src/alpha20/deployment_guard.py`), runner `OBSERVE_ONLY`,
`selection_eligible: false`.

## 10. Interdits explicites

```text
inverser le signal si l'effet est négatif
changer 60m en 30m parce que 30m passe
modifier les percentiles après résultats
sélectionner uniquement les meilleurs actifs
retirer une mauvaise année
utiliser seulement les événements connus manuellement
faire du forced averaging pendant la cascade
ajouter du levier pour rendre le CAGR attractif
```

## Statuts d'autorisation (2026-07-22, décision humaine)

```yaml
PHASE_0_PREREGISTRATION: AUTHORIZED
DATA_INVENTORY: AUTHORIZED
COLLECTORS: AUTHORIZED_AFTER_INVENTORY
EVENT_EDGE_RUN: AUTHORIZED_AFTER_DATA_FREEZE
PORTFOLIO_BACKTEST: NOT_YET_AUTHORIZED
PAPER_RUNNER: NOT_AUTHORIZED
LIVE: FORBIDDEN
```

## Ordre des travaux (aucune étape sautée)

```text
1. [CE DOCUMENT] préenregistrer liquidation_relative_reversal_v1
2. inventorier les données historiques réellement accessibles (DATA_INVENTORY.yaml, PROVENANCE.md)
3. collecter les entrées OI/liquidations/prix de référence manquantes (si autorisé après inventaire)
4. implémenter et tester les invariants du détecteur causal de cascade
5. geler le panel d'événements
6. exécuter le test événementiel net (Phase 1)
7. seulement si (6) passe : implémenter le simulateur trois-jambes
8. seulement si (6) passe : exécuter la validation portefeuille (Phase 2)
9. seulement si (8) passe : enregistrer un runner qualifié OBSERVE_ONLY
```

Étapes 2-9 non commencées. Aucun code, aucune donnée, aucun résultat dans
ce document au-delà de ce qui est cité comme prior art déjà connu.
