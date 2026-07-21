# Préenregistrement — stress_gate_dispersion_v2_reproduction

Écrit avant tout calcul, avant toute donnée chargée. Reconstruction
indépendante de `cross_exchange_stress_gate_h2` (statut
`UNVERIFIED_PROVENANCE`, voir `configs/alpha20.yaml ->
experiment_registry.provenance_blocked` et `research/forensics/
stress_gate_c78874b/`). Ceci n'est PAS une tentative de reproduire
−2,05 % / −1,82 % : c'est un test neuf sur la même prédiction économique.

```yaml
experiment_id: stress_gate_dispersion_v2_reproduction
experiment_type: independent_reconstruction

relationship_to_historical_result:
  historical_report_known: true
  historical_numbers_used_as_target: false
  historical_result_counts_as_validation_evidence: false

mechanism: cross_venue_premium_dispersion_as_forward_liquidity_stress_indicator

intended_use: risk_overlay
not_intended_use: standalone_return_engine

universe:
  assets: [BTC, ETH, SOL, BNB]
  venues: [Binance, Bybit]
  membership: point_in_time

## Amendement 2026-07-21 (bis) — bornes signal vs. prix, avant collecte complète

Une seule borne (`experiment_end_utc`) ne suffit pas : chaque signal
admissible doit disposer de 24h complètes de mark price APRÈS son
`decision_timestamp`. Convention retenue (la plus explicite des deux
proposées) :

```yaml
signal_start_utc: "2022-11-03T00:00:00Z"   # = début réel de l'overlap funding Bybit archivé, pas choisi pour un résultat
signal_end_utc:   "2026-07-14T00:00:00Z"   # borne des SIGNAUX (funding) — inchangée
price_start_utc:  "2022-11-03T00:00:00Z"   # = signal_start_utc (aucune fenêtre forward ne regarde avant le premier signal)
price_end_utc:    "2026-07-15T01:00:00Z"   # = signal_end_utc + 24h + 1h de marge (decision_timestamp peut être
                                            #   jusqu'à ~5 min après signal_timestamp ; marge large et ronde)

last_admissible_signal_rule: >
  le dernier signal_timestamp <= signal_end_utc tel que
  decision_timestamp(signal_timestamp) + 24h <= price_end_utc.
  Valeur numérique résolue seulement une fois les données réelles en main
  (rapport de couverture, commit 5) — pas devinée ici avant collecte.
last_complete_forward_window_end_rule: >
  = decision_timestamp(last_admissible_signal) + 24h, par construction <= price_end_utc.
```

Toute barre 5m manquante entre `price_start_utc` et `price_end_utc` réduit
mécaniquement `last_admissible_signal` — jamais comblée par un forward-fill
pour "sauver" un événement proche de la fin de fenêtre.

## Amendement 2026-07-21 (ter) — périmètre de collecte réduit aux inputs primaires

La cible forward préenregistrée n'utilise que **funding Binance, funding
Bybit, et mark price Binance 5m**. Le mark price Bybit n'est PAS un input
du test primaire (Bybit ne sert qu'à sa propre série de funding, dans le
calcul de la dispersion). En conséquence :

- Collecte réelle limitée à 4 actifs × {funding Binance, funding Bybit,
  mark price Binance 5m} = 12 séries, pas 16.
- Si le mark price Bybit est collecté un jour pour du contrôle qualité
  seulement, il doit être marqué `role: auxiliary_qc`,
  `used_in_primary_feature: false`, `used_in_primary_target: false`,
  `included_in_analysis_input_hash: false` — jamais mélangé silencieusement
  à l'input du test primaire.

## Amendement 2026-07-21 (quater) — invariants de progression de pagination

Ajoutés au collecteur avant le run complet (pas après un résultat — aucune
statistique économique n'existe encore) :

```text
Bybit   : next_end   < previous_end    (progression stricte vers le passé)
Binance : next_start > previous_start  (progression stricte vers le futur)
page N identique (hash) à la page N-1  -> échec, jamais une boucle silencieuse
max_pages explicite par endpoint       -> échec avant toute boucle infinie
```

primary_prediction: high_causal_cross_venue_dispersion_predicts_worse_forward_drawdown

primary_test_count: 1
secondary_tests_are_robustness_only: true

stopping_rule:
  - close_if_primary_sign_is_wrong
  - close_if_primary_effect_is_not_statistically_supported
  - close_if_leave_one_asset_or_leave_one_year_results_are_structurally_unstable
  - close_if_portfolio_marginal_effect_is_non_positive_after_costs
  - no_post_result_threshold_rescue
```

## Amendement 2026-07-21 (avant tout accès aux données historiques)

Ajouté après un simple test de connectivité HTTP (`curl` vers
`fapi.binance.com`/`api.bybit.com`, code 200 — aucune donnée de marché ni
statistique économique observée) et avant toute collecte historique. Ce
n'est pas un sauvetage post-résultat : quatre conventions manquaient dans
la version initiale de ce document et sont fixées maintenant, par
nécessité (le collecteur ne peut pas être écrit sans elles), pas après
avoir vu un résultat.

1. **Variable primaire** : dispersion du **funding rate réglé**
   (`settled funding rate`, la valeur publiée par `fundingRate`/
   `funding/history`), PAS le premium index. Confirme et précise la
   formule déjà écrite plus bas (`abs(funding_perp_binance −
   funding_perp_bybit)`) — pour lever toute ambiguïté avec l'hypothèse
   distincte "dispersion du premium index", qui serait une hypothèse
   différente si jamais testée un jour.
2. **Série pour le drawdown forward** : **mark price Binance** (endpoint
   `markPriceKlines`), pas index price, pas spot, pas Bybit. Choix motivé
   économiquement : le mark price est la référence de marge/liquidation
   — c'est la série sur laquelle un "drawdown de stress" se matérialise
   réellement pour un compte à effet de levier, ce qui est directement
   pertinent pour un overlay de risque. Binance est retenu comme venue
   canonique unique (pas de moyenne/blend Binance-Bybit) pour la même
   raison que carry_basis_v12 trade sur Binance USDM — évite d'introduire
   une deuxième source de désynchronisation inter-venue dans le label
   alors que le SIGNAL, lui, est déjà inter-venues.
3. **Résolution du prix** : barres **5 minutes** (mark price). Assez fin
   pour capter un creux intrajournalier réaliste (cascade de liquidation),
   assez grossier pour rester une collecte tractable (288 barres/24h).
4. **Disponibilité causale** (remplace la formulation précédente, plus
   vague, "exécution barre suivante") :
   ```
   signal_timestamp      = timestamp de règlement du funding (0h/8h/16h UTC)
   decision_timestamp    = première barre mark price 5m dont l'open est
                           strictement postérieur à signal_timestamp
   forward_window_start  = decision_timestamp
   forward_window_end    = decision_timestamp + 24h
   ```
   Empêche explicitement que le low de la barre de règlement serve à la
   fois d'information (dans le calcul du seuil) et de résultat (dans le
   drawdown forward).

## Décisions primaires fixées avant tout résultat

- **Horizon forward primaire** : 24 h. Choisi pour cohérence avec la
  décision opérationnelle de l'overlay (le carry_basis_v12 rebalance sur
  cycle funding 8 h ; 24 h = 3 cycles, assez long pour couvrir un
  dénouement de stress, assez court pour rester actionnable par l'overlay),
  pas pour reproduire le chiffre historique (qui utilisait aussi 24 h,
  mais ce n'est pas la raison du choix ici).
- **Définition primaire du drawdown forward** : creux minimum du **mark
  price Binance, barres 5 min** (voir amendement 2026-07-21) sur
  `[forward_window_start, forward_window_end]` = `[decision_timestamp,
  decision_timestamp + 24h]`, relatif au mark price en `decision_timestamp`.
- **Transformation primaire de la dispersion** : `abs(funding_perp_binance
  − funding_perp_bybit)` au cycle funding 8 h le plus récent strictement
  antérieur à t.
- **Seuil causal (point méthodologique critique imposé)** : PAS de
  percentile calculé sur l'échantillon complet. `threshold_t =
  percentile_95(dispersion sur les 270 observations 8h strictement
  antérieures à t, minimum 180)` — quantile *expanding* avec warm-up,
  même fenêtre que `backtest_funding_extreme.py` (Z_WIN=270, Z_MIN=180)
  pour rester cohérent avec le protocole déjà validé dans ce repo.
  `stress_t = dispersion_t >= threshold_t`.
- **Timestamps de funding** : cycle 8 h aux heures (0, 8, 16) UTC,
  Binance et Bybit alignés sur l'horodatage de publication (pas de
  décalage de settlement supposé sans vérification).
- **Observations manquantes sur une venue** : `fail_closed` — un
  timestamp où une des deux venues n'a pas de funding publié est exclu du
  panel pour ce timestamp (pas de forward-fill, pas d'imputation).
- **Actifs minimum présents** : les 4 (BTC/ETH/SOL/BNB) ; un panel avec
  moins de 4 actifs valides sur une fenêtre est un échec d'intégrité, pas
  un résultat.
- **Déduplication** : un couple (asset, timestamp_8h) dupliqué est une
  erreur d'intégrité (test dédié), jamais moyennée silencieusement.
- **Synchronisation Binance/Bybit** : jointure sur timestamp exact du
  cycle funding (pas de tolérance temporelle) ; désynchronisation = ligne
  exclue (fail_closed), pas d'appariement approximatif.

## Séquence de validation (dans cet ordre, arrêt à la première étape ratée)

1. **Intégrité du panel** — tests `test_no_future_rows_used`,
   `test_causal_quantile_threshold`, `test_no_forward_fill_across_venue_gap`,
   `test_duplicate_timestamp_rejected`, `test_missing_leg_handled_fail_closed`,
   `test_panel_deterministic`, `test_manifest_records_input_hashes` — tous
   sur données synthétiques déterministes (comme le reste de la suite
   `test_alpha20_tournament_selection.py`), avant tout accès à des données
   réelles.
2. **Test événementiel primaire** — sur données réelles si disponibles sur
   la machine d'exécution ; NW-t, IC par block bootstrap, effect size,
   n, répartition par actif/année. Échec si signe inversé, IC ne
   soutenant pas l'effet, dépendance à un seul actif/année, ou
   disparition avec le seuil causal (par opposition au seuil non-causal
   du rapport historique).
3. **Robustesse** (seulement si l'étape 2 passe) — leave-one-asset-out,
   leave-one-year-out, split moitié/moitié, sensibilité raisonnable du
   seuil (95 %) et du warm-up, correction du nombre d'essais.
4. **Ablation portefeuille** (seulement si l'étape 3 passe) — baseline,
   size-only, entry gate, entry+exit ; CAGR net, vol, maxDD, Sharpe,
   Calmar, turnover, coûts marginaux, probabilité de franchir `dd_kill`.

## Interdits explicites pour ce cycle

Ne pas : viser numériquement −2,05 %/−1,82 % ; modifier un seuil après
avoir vu un résultat ; requalifier le résultat historique en
`VALIDATED_SIGNAL` ; créer un runner à n'importe quelle étape ; rendre un
runner `ACTIVE` ; toucher au tournoi ou au comportement live ; multiplier
horizons/quantiles jusqu'à un résultat favorable ; lancer un second cycle
de "sauvetage" après un échec à l'étape 2 ou 3.

## Critère de qualification d'un runner (rappel, ne s'applique qu'en fin de cycle)

- Étape 2 seule passe → `VALIDATED_RISK_FEATURE` / `NOT_RUNNER_QUALIFIED`.
- Étapes 2 ET 4 passent → `OBSERVE_ONLY`, `selection_eligible: false`,
  `feature_flag_default: false`. Toujours pas `ACTIVE`.
- Étape 2 échoue → `CLOSE_NO_EDGE` pour cette reconstruction, priorité R&D
  reportée sur un moteur de rendement orthogonal (funding relative value
  cross-venue, calendar basis, ou paired relative value).

## Amendement 2026-07-21 (quinquies) — settlement_timestamp_alignment_v1

Mini-audit data-only exécuté d'abord (aucune cible économique construite,
aucune relation dispersion→drawdown regardée) : jointure exacte en ms ne
capture que 2553/4047 (63%) même sur BTC/ETH/BNB, identique aux 3 actifs.
Cause confirmée sur données réelles : timestamps Binance portent 0-30ms de
jitter hors grille, timestamps Bybit tombent exactement sur la grille.
Rejet de l'option "garder l'exact" (sélection artificielle par format de
timestamp, pas un signal économique) et de l'option "élargir après avoir
vu le résultat". Tolérance fixée à **1000ms AVANT** le mini-audit, pas
choisie après :

```yaml
preregistration_amendments:
  - amendment_id: settlement_timestamp_alignment_v1
    reason: >
      Binance funding settlement timestamps contain sub-second reporting
      jitter while Bybit timestamps are aligned to the exact UTC grid.
      Exact millisecond equality discards economically simultaneous events.
    observed_before_target_construction: true
    economic_outcomes_inspected: false
    primary_matching_rule:
      type: mutual_nearest_one_to_one
      tolerance_ms: 1000
      asset_must_match: true
      ambiguous_match: reject
      unmatched_event: reject
      forward_fill: forbidden
      merge_asof: forbidden
    canonical_timestamp_role:
      used_for_pairing_only: true
      used_as_information_availability_timestamp: false
    pair_information_available_at:
      formula: max(binance_raw_timestamp, bybit_raw_timestamp)
    decision_timestamp:
      formula: >
        first Binance 5-minute mark-price bar beginning strictly after
        pair_information_available_at
```

Mini-audit, résultat (`scripts/normalize_stress_gate_dispersion_v2.py::run_mini_audit`,
données réelles collectées) :

| Actif | n binance | n bybit | matchs exacts | matchs 1-à-1 ≤1s | ambigus | non appariés (b/y) | p50/p95/p99/max \|offset\| ms |
|---|---:|---:|---:|---:|---:|---|---|
| BTC/ETH/BNB (identique) | 4047 | 4047 | 2553 (63%) | **4047 (100%)** | 0 | 0/0 | 0/12/18/29 |
| SOL | 4122 | 4407 | 2579 | 4119 (99.9% côté binance) | 0 | 3/288 |0/12/18/30 |

Zéro ambiguïté sur les 4 actifs. Marge ~34× entre le jitter max observé
(29-30ms) et la tolérance (1000ms) — la règle correspond à la structure
constatée, pas resserrée ni élargie après inspection. Les 288 événements
Bybit SOL non appariés sont dans la fenêtre où Bybit restait à 2h après le
retour à 8h de Binance (cf. segmentation de cadence déjà documentée) — pas
une anomalie de la règle de jointure elle-même.

## Amendement 2026-07-21 (sexies) — comparabilité des intervalles, variable primaire

La variable primaire reste le **taux réglé brut** (aucune normalisation
d'intervalle silencieuse). Condition ajoutée avant admission au panel
primaire :

```yaml
primary_rate_comparability:
  feature: absolute_raw_settled_funding_rate_difference
  previous_settlement_required_on_both_venues: true
  equal_observed_interval_required: true
  allowed_interval_hours: [2, 4, 8]
  interval_mismatch: reject
  irregular_or_unknown_interval: reject
```

`funding_rate_per_hour` / `funding_rate_8h_equivalent` restent des colonnes
de sensibilité secondaire, jamais un substitut du taux brut en cas d'échec
du test primaire.

## Budget

Un seul cycle complet. Pas de cycle de sauvetage après échec.
