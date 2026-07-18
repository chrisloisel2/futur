# PROTOCOLE PRÉ-ENREGISTRÉ — OPTIONS_POSITIONING_4H

**Statut : pré-enregistré le 2026-07-18, AVANT toute exécution.**
Décision utilisateur : le v0 journalier est mort (dabc9f9 + fdfa862, NO_EDGE) et ne sera
pas ressuscité par optimisation. **Une seule** tentative 4h est autorisée, sur les mêmes
données, parce que le mécanisme supposé est intraday. Si ce protocole échoue,
OPTIONS_POSITIONING est classé **définitivement NO_EDGE** — aucune variante ultérieure.

## Hypothèse mécanique (fixée avant le run)

Les flux d'options agressifs (achat de protection, chasse au gamma) impactent le sous-jacent
en heures via le hedging des dealers ; l'agrégation journalière noie ce signal. Si l'info
existe, elle doit apparaître à 4h avec les MÊMES features qu'en v0, sans nouvelle ingénierie.

## Données (immuables, déjà backfillées)

- Trades options Deribit BTC 2023-01 → 2026-06 (`data/options_backfill/deribit/trades/BTC/`).
- Prix BTC : `data/enriched/BTCUSDT_1h_enriched.parquet` (close).

## Features par bucket 4h UTC (00/04/08/12/16/20) — identiques au v0, agrégées 4h

1. `d_skew_4h` : skew_25ish(B) − skew_25ish(B−1) ; skew = médiane IV puts OTM (K/S 0.80-0.95)
   − médiane IV calls OTM (1.05-1.20). NaN si une des deux jambes est vide (comptés, pas imputés).
2. `d_atm_iv_4h` : idem sur IV médiane des trades ~ATM (0.95-1.05).
3. `net_call_flow_4h` : Σ amount signé (buy=+1, sell=−1) des calls du bucket.
4. `net_put_flow_4h` : idem puts.

Normalisation : z-score roulant 540 buckets (~90 j), min 180, jamais centré sur le futur.

## Tests (24, tous listés à l'avance)

4 signaux × horizons {4h, 8h, 24h} × délais {0, +1 bucket}. Entrée = close BTC à la fin
du bucket (B+délai) ; retour forward simple. Métriques : IC Spearman + p, spread Q5−Q1.

## Critère PASS/FAIL (fixé avant le run, aucune autre lecture admise)

**PASS** ssi au moins une cellule (signal, délai, horizon) satisfait SIMULTANÉMENT :
- p < 0.002 (≈ Bonferroni 24 tests) ;
- |IC| ≥ 0.04 ;
- même signe d'IC sur les deux moitiés temporelles (2023-01→2024-09 et 2024-10→2026-06),
  chaque moitié avec n ≥ 500 buckets valides.

**FAIL** sinon → verdict définitif NO_EDGE, versionné. Aucun re-run avec d'autres fenêtres,
bandes de moneyness, seuils ou horizons. Les résultats sont publiés quels qu'ils soient.

## Exécution

`scripts/test_options_positioning_4h_preregistered.py` — un seul run, résultats dans
`reports/OPTIONS_POSITIONING_4H_VERDICT.md` + JSON. Le hash du commit de CE fichier
précède le commit des résultats (vérifiable dans git).
