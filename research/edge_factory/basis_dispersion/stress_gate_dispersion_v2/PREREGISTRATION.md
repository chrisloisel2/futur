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

## Décisions primaires fixées avant tout résultat

- **Horizon forward primaire** : 24 h. Choisi pour cohérence avec la
  décision opérationnelle de l'overlay (le carry_basis_v12 rebalance sur
  cycle funding 8 h ; 24 h = 3 cycles, assez long pour couvrir un
  dénouement de stress, assez court pour rester actionnable par l'overlay),
  pas pour reproduire le chiffre historique (qui utilisait aussi 24 h,
  mais ce n'est pas la raison du choix ici).
- **Définition primaire du drawdown forward** : creux minimum du NAV
  synthétique (spot mid) sur la fenêtre [t+1h, t+25h] relatif au prix en
  t, exécution barre suivante (pas de signal-au-close-utilisé-au-close).
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

## Budget

Un seul cycle complet. Pas de cycle de sauvetage après échec.
