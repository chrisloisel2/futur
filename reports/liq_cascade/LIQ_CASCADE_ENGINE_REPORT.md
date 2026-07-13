# LIQ_CASCADE — moteur événementiel cascades OI 5-min (2026-07-06)

## Ce qui a été construit (en une session)

1. **Découverte data** : Binance Vision `futures/um/daily/metrics/` = OI + ratios
   top-traders + taker long/short **par 5 minutes, 2020-09 → J-2, TOUS les actifs**.
   Comble le gap déclaré dans `SCALE_ASSESSMENT.md` ("OI BTC-only, alts sans OI").
   Backfill : `scripts/backfill_binance_metrics_vision.py` → **49/50 actifs,
   ~20M barres, 943 MB** (RNDR retiré de Binance, migration RENDER). Idempotent,
   manifest par symbole.
2. **Moteur** `src/institutional/engines/liq_cascade/` :
   - `detector.py` : cascade = chute d'OI 30-min z ≤ −3 (stat glissante 7j passé
     only, warm-up 3j) + |Δprix| ≥ 0,4 % → LONG_CASCADE / SHORT_SQUEEZE. Prix
     implicite = OI_value/OI (aucun feed prix requis).
   - `dataset.py` : 17 features causales (intensité, positionnement z-scoré,
     contexte, cross-asset market-wide) + labels forward 1h/4h/8h, entrée à la
     barre SUIVANTE. **Causalité prouvée par test** (choc futur injecté → aucune
     feature ne bouge, les labels bougent). 5/5 tests.
   - `train_liq_cascade_engine.py` : walk-forward annuel, baseline rule sans ML,
     LightGBM P(fwd_4h > coût), seuil déclaré 0,55, coûts 14 bps + stress 28 bps.
3. **Pont proxy↔réel** : `validate_cascade_proxy_vs_real.py` compare les cascades
   OI aux vraies liquidations collectées (Bybit/OKX). Recouvrement actuel 4,3 h
   → INDICATIF seulement ; se re-mesure chaque jour de collecte.

## Résultats walk-forward FINAL (49 actifs, 38 141 events, 2021-2026)

Règles figées AVANT le run : seuil 0,55 ; fold valide ⟺ train ≥ 2 000 events ;
CANDIDATE ⟺ ≥ 3 folds PF ≥ 1,35 et aucun fold valide destructeur.

| Fold | train | ML n | PF | WR | mean net | ROI sizé 10 % | PF coûts ×2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2022 (EXCLU : train 597) | 597 | 2002 | 0,73 | 48 % | −38 bps | −54 % | 0,70 |
| 2023 | 6 722 | 764 | **1,85** | 64 % | +50 bps | **+46 %** | 1,75 |
| 2024 | 14 590 | 3 058 | **1,37** | 56 % | +41 bps | **+242 %** | 1,39 |
| 2025 | 24 465 | 1 324 | 1,10 | 49 % | +15 bps | +18 % | 1,15 |
| 2026 (S1) | 33 232 | 568 | 1,29 | 54 % | +25 bps | +15 % | 2,58 |

**VERDICT au gate déclaré : NO_EDGE (2/4 folds ≥ 1,35 ; il en fallait 3).**

Mais l'honnêteté vaut dans les deux sens — ce qui est également vrai :
- **4/4 folds valides POSITIFS nets de coûts** (PF 1,10-1,85), zéro année
  destructrice — premier moteur offensif du repo avec ce profil (le pullback
  promu SHADOW en juin faisait PF 0,72 sur 2026).
- **Robuste aux coûts ×2** (tous les folds tiennent ou s'améliorent — noter que
  le label s'adapte au coût, donc le modèle re-sélectionne ; en live on entraîne
  avec son coût réel : légitime, mais ce n'est pas un stress pur).
- **Gradient de conviction monotone** : au seuil 0,65 (échelle RAPPORTÉE, pas
  sélectionnée), 2023-2026 sortent PF 3,79 / 1,78 / 2,26 / 11,7 — chaque année
  fortement positive. Choisir 0,65 maintenant serait du tuning sur test ; ce
  gradient est une HYPOTHÈSE à valider en shadow/forward.
- Learning curve nette : l'AUC/PF suit la taille du train — le moteur
  s'améliorera mécaniquement avec l'accumulation (collecteur live + Vision J-2).

## Réserves d'honnêteté

1. **roi_sized ignore la concurrence des positions** (~8-9 events/jour, hold 4h,
   cascades corrélées market-wide) — le chiffre portefeuille sera NETTEMENT plus
   bas. Étape 2 obligatoire (doctrine) : intégration multileg avec caps.
2. Prix implicite OI_value/OI ≈ mark 5-min : l'exécution réelle (klines/slippage
   de cascade) reste à valider ; le slippage en pleine cascade peut dépasser 2 bps.
3. Proxy vs vraies liquidations non encore validé (4 h de recouvrement).
4. 2022 exclu par la règle min-train : le moteur N'EST PAS validé en bear
   profond — le RegimeGate existant devra le couvrir.

## Prochaines étapes (dans l'ordre doctrine)

1. **Intégration portefeuille** : adaptateur LIQ_CASCADE → multileg backtester
   (caps positions, sizing conviction), run 2023-2026 vs V1.2 → mesure du gain
   NET portefeuille (ROI/DD). C'est le juge de paix.
2. Enrichir features avec le funding multi-exchange déjà backfillé (contexte
   crowding à l'event) — re-run WF, mêmes règles.
3. Validation forward : seuil 0,65 en SHADOW sur le feed live (déclenchement =
   vraies liquidations Bybit/OKX, confirmation = OI 5-min) ; re-verdict à 30j.
4. Backfill Vision quotidien en cron (J-2) pour que le dataset reste frais.

Verdict : `LIQ_CASCADE_V1: NO_EDGE_AU_GATE — mais 4/4 folds positifs, gradient
de conviction net, magnitudes de la bonne classe. Itération 2 justifiée.`

---

# V2 — deep dive + optimisation (2026-07-06, même session)

## Deep dive structurel (`DEEP_DIVE.md`) — la physique de l'edge

Gradients MONOTONES sur toutes les dimensions, convergeant vers une thèse unique :
**l'argent est dans les capitulations extrêmes, larges et violentes** :
- profondeur : z≤−8 → PF 2,18 (+91 bps, n=640) vs masse z∈[−4,5;−3] → PF 0,92 ;
- ampleur : >5 events market-wide/30 min → PF 1,31 vs isolé 0,86 ;
- vol 24h extrême → PF 1,42 ; chute 30 min <−3 % → PF 1,37 ;
- funding aux DEUX extrêmes → PF 1,20-1,34, zone neutre négative.
(Descriptif plein-historique : sert à comprendre, PAS à filtrer — le tri reste au modèle WF.)

## V2 : méthodes (toutes internes au train, gate INCHANGÉ)

+12 features causales (funding as-of+z, structure OI 2h/24h/pctile30j, deltas
positionnement, contexte BTC, séquencement, dow) · val purgée chrono 15 % +
embargo 8 h + early stopping · sample weights |ret−coût| winsorisés p95 ·
bagging 5 graines · sélection par QUANTILE des scores val (top 20 % déclaré
primaire). L'isotonique, essayée d'abord, était instable sur val fine
(sélection n=3 → n=3295 selon fold) → remplacée, run archivé `*_V2_ISOTONIC.json`.

## Résultats V2 (event-level, coût 14 bps ; stress 28 bps entre parenthèses)

| Fold | n | PF | mean | WR | PF ×2 | PORTF max3 (FIFO) |
|---|---:|---:|---:|---:|---:|---:|
| 2022 (EXCLU train<2000) | 1834 | 0,76 | −37 bps | 48 % | (0,70) | −27,0 % |
| 2023 | 517 | **2,23** | +71 bps | 62 % | (1,87) | **+11,6 % PF 1,58** |
| 2024 | 2538 | **1,41** | +49 bps | 56 % | (1,34) | −4,1 % |
| 2025 | 1220 | 1,12 | +18 bps | 51 % | (1,07) | −9,3 % |
| 2026 S1 | 1053 | **1,42** | +35 bps | 54 % | (1,25) | −4,3 % |

**VERDICT AU GATE PRÉ-DÉCLARÉ : `CANDIDATE`** (3/4 folds PF≥1,35, 0 destructeur).
Premier moteur offensif du repo à passer un gate de folds.

## La réserve centrale : la conversion portefeuille N'est PAS résolue

La sim à concurrence bornée (max 3, FIFO, hold 4 h) est négative 3 années sur 4
alors que l'event-level est PF≥1,12 partout : **les slots FIFO se remplissent au
début des vagues et sautent les events profonds tardifs — or le deep dive montre
que l'argent est précisément dans les vagues profondes.** La conversion exige une
allocation par conviction intra-vague (top-k par score par barre, slots dédiés
aux z extrêmes) — à construire et tester dans le multileg (niveau 2 doctrine),
PAS en réglant la sim jusqu'au vert.

## Multiplicité & anti-overfit — dit clairement

3 variantes exécutées (v1, v2-iso, v2b). Le gate était figé avant tout run 50
actifs et n'a pas bougé, mais le risque de sélection de variante existe.
Confirmations exigées avant toute promotion au-delà de SHADOW :
1. **Intégration multileg** avec allocation par conviction, 2023-2026, gain net
   vs V1.2 (ROI/DD) — le juge de paix niveau 2.
2. **SHADOW forward ≥30 j** sur le feed live (déclencheur = vraies liquidations
   Bybit/OKX collectées 24/7 ; confirmation OI 5-min via Vision J-2 quotidien).
3. Ré-exécution du WF à chaque mois de données fraîches (le train grandit).

Statut : `LIQ_CASCADE_V2: CANDIDATE (event-level) — portfolio-conversion TBD.`
