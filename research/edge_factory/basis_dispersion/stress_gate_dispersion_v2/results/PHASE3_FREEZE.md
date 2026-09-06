# Phase 3 — clôture (gel du verdict non-incrémental)

**Statut final : `REPRODUCED_NON_INCREMENTAL_RISK_ASSOCIATION` — `NOT_RUNNER_QUALIFIED` — `NO_PRODUCTION_FEATURE` — `NO_LIVE_IMPACT`.**

`STRESS_GATE` détecte correctement un état de stress, mais il détecte
essentiellement un stress que le marché a déjà révélé dans les prix
(drawdown et volatilité réalisée des 24h précédentes). Créer une feature
de production sur cette base reproduirait ce que des contrôles de
volatilité/drawdown déjà existants savent mesurer.

## Réserve explicite sur 2022 / FTX

> Le test leave-one-year-out 2022 est non informatif : aucune observation
> 2022 n'atteint `threshold_available` à cause du warm-up (180 observations
> minimum par groupe consomment quasiment tout 2022). La stabilité
> observée ne constitue donc pas une preuve hors épisode FTX.

Ceci ne remet pas en cause le rejet incrémental : le test 6 (valeur
incrémentale, contrôles trailing drawdown + volatilité réalisée) échoue
indépendamment de ce point, sur l'ensemble de l'échantillon 2023-2026.

## Ce que la recherche a réellement établi

- L'association historique existe réellement (Phase 2, `+0,294 %`,
  IC95 `[+0,0079 %, +0,598 %]`, HAC p=0,031).
- Causalement ordonnée au niveau des timestamps (aucune donnée future dans
  le seuil ni la classification).
- Stable entre les deux moitiés de l'échantillon (test 1).
- Ne dépend d'aucun actif unique (test 2).
- Ne dépend d'aucune année unique, avec la réserve 2022 ci-dessus (test 3).
- Ne dépend d'aucun épisode unique (test 4, 264 épisodes, pire retrait
  encore `+0,222 %`).
- Survit au panel exact-ms (test 5).
- N'est **pas** incrémentale après contrôle du drawdown/volatilité déjà
  visibles (test 6 : `beta_stress` chute de `+0,294 %` à `+0,130 %`,
  p=0,30, IC95 borne basse `−0,087 %`).

Falsification réussie : une statistique réelle, stable et reproductible
peut malgré tout ne pas constituer un nouvel edge exploitable.

## Artefacts figés (aucune modification a posteriori)

- `results/PRIMARY_RESULT.json` / `.md` / `unblinding_receipt.yaml` (Phase 2, `c34cc9d`/`cb4b5c7`)
- `results/PHASE3_RESULT.json` (Phase 3, `e863335`)
- `results/PHASE2_FREEZE.yaml`, ce fichier (`PHASE3_FREEZE.md`)
- Correctif de performance du bootstrap : `7a5e2c5` — optimisation
  mathématiquement équivalente, preuve bit-à-bit déjà obtenue (mêmes
  bornes de CI exactement, avant/après), conservé séparément, jamais
  mélangé avec un changement de méthode.

## Registre

Entrée de fermeture ajoutée dans `configs/alpha20.yaml ->
experiment_registry.closed_no_edge` (nom : `stress_gate_dispersion_v2_reproduction`,
verdict `NO_INCREMENTAL_EDGE` — distinct de `NO_EDGE` au sens absolu).
Réactivation interdite sans thèse nouvelle explicite ; en particulier
changer uniquement le quantile, l'horizon, ou le warm-up, ajouter du
levier, retirer les contrôles, ou restreindre à l'actif/la période la
plus favorable ne constituent PAS une thèse nouvelle.

## Prochaine piste

Aucun nouveau travail sur `STRESS_GATE`, sauf récupération forensique
documentaire du code historique disparu (`research/forensics/stress_gate_c78874b/`).
Priorité R&D : `funding_relative_value_cross_venue_v1` — un moteur de
rendement, pas un nouvel overlay de risque.
