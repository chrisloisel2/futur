# Préenregistrement — momentum_tokenized_macro_v1

Statut : **IDEA / déprioritisé**. Écrit pour acter la séparation d'univers
décidée le 2026-07-21 (voir `../cross_sectional_momentum_v1/DATA_INVENTORY.yaml`,
`../cross_sectional_momentum_v1/PREREGISTRATION_CRYPTO_V1_ADDENDUM.md`) —
pas pour lancer un travail immédiat. Aucune donnée supplémentaire n'a été
collectée pour cette piste ; le funding déjà archivé pour ces symboles est
un sous-produit de l'extension d'univers de `cross_sectional_momentum_v1`,
pas un effort dédié.

## Idée

Les perps tokenisés Binance sur actions, ETF et commodités (NVDAUSDT,
MSTRUSDT, INTCUSDT, MRVLUSDT, SKHYNIXUSDT, SNDKUSDT, QQQUSDT, SOXLUSDT,
EWYUSDT, XAUUSDT, XAGUSDT, PAXGUSDT, BZUSDT/CLUSDT et les autres identifiés
dans l'inventaire momentum) partagent des facteurs de risque *macro*, pas
crypto : bêta Nasdaq/semi-conducteurs pour les tech ; bêta or/argent pour
les commodités ; horaires de marché sous-jacent (marché actions fermé le
week-end/soir) potentiellement mal reflétés par un perp qui, lui, cote 24/7.

Hypothèse de recherche (non testée) : un momentum cross-sectionnel sur cet
univers macro-tokenisé nécessiterait sa propre neutralisation (bêta
Nasdaq/or/actions, pas bêta BTC) et un traitement explicite du décalage
horaire marché sous-jacent vs cotation continue — un mécanisme différent
de `MOMENTUM_CRYPTO_V1`, pas une variante cosmétique.

## Pourquoi non prioritaire

```text
univers plus petit (~15-20 symboles vs 30 pour le crypto)
historique plus court pour plusieurs noms (produits récents, 2025-2026)
neutralisation factorielle à concevoir (Nasdaq/or/actions, pas BTC)
mécanisme d'horaires de marché sous-jacent non modélisé nulle part ici
```

## Prochaine étape (non lancée)

Aucune. Reprendre seulement si `MOMENTUM_CRYPTO_V1` est validé et qu'un
budget de recherche est explicitement alloué à ce sleeve — pas en
parallèle du sprint momentum crypto.
