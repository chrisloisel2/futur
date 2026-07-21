# Phase 3 — protocole de stabilité (préenregistré avant tout calcul)

Écrit après le gel de la Phase 2 (`cb4b5c7`, `REPRODUCED_CAUSAL_ASSOCIATION`
/ `REPRODUCED_CAUSALLY_ORDERED_PREDICTIVE_ASSOCIATION`), **avant** tout
calcul de stabilité, leave-one-out, ou régression incrémentale.

## Paramètres gelés (Phase 3 ne peut en modifier aucun)

```yaml
frozen_from_phase2:
  forward_horizon: 24h
  primary_quantile: 0.95
  warmup_min_periods: 180
  expanding_window: 270
  matching: mutual_one_to_one, tolerance_ms=1000
  primary_variable: absolute_raw_settled_funding_rate_difference
  threshold_group: [symbol, observed_interval_hours]
  target_price_series: binance_mark_price_5m
  bootstrap: {type: moving_calendar_block, block_days: 7, resamples: 10000,
             resample_all_assets_jointly: true, seed: 20260721}
  stress_labels: frozen_from_primary_result   # is_stress de PRIMARY_RESULT, jamais recalculé
```

Interdit : horizon 12h/48h, quantile 90%/97.5%, nouvelle transformation
capable de remplacer le primaire.

## Test 1 — Stabilité temporelle (split fixé avant calcul)

```yaml
period_1: ["2022-11-03T00:00:00Z", "2024-09-08T00:00:00Z")   # exclusif
period_2: ["2024-09-08T00:00:00Z", "2026-07-14T00:00:00Z"]
gate: delta_period_1 > 0 AND delta_period_2 > 0
significance_required: false   # puissance réduite attendue, IC rapportés quand même
```

## Test 2 — Leave-one-asset-out

Retrait successif de BTC, ETH, SOL, BNB sur le panel pooled.

```yaml
gate: delta > 0 dans les 4 retraits
report: effet individuel par actif (pas seulement pooled-minus-un)
no_significance_required_per_asset: true
```

## Test 3 — Leave-one-calendar-year-out

Retrait successif de 2022, 2023, 2024, 2025, 2026 (années civiles,
2022/2026 partielles incluses).

```yaml
gate: delta > 0 dans les 5 retraits
significance_required: false
purpose: tester si FTX (2022) ou la période récente gouverne le résultat
```

## Test 4 — Leave-one-stress-episode-out

Regroupement des événements stress (tous actifs) en épisodes calendaires :
même épisode tant que l'écart entre événements successifs ≤24h, nouvel
épisode sinon.

```yaml
gate: aucun retrait d'un seul épisode ne fait passer delta <= 0
report: [n_episodes, median_events_per_episode, min_leave_one_episode_out_delta,
        episode_with_largest_effect_reduction]
```

## Test 5 — Sensibilité panel exact-ms (secondaire, ne peut pas remplacer le primaire)

Sous-panel des paires appariées par égalité stricte en ms (déjà calculé en
Phase 2 comme `n_exact_matches`), sans modification.

```yaml
gate: delta_exact_ms > 0
significance_required: false   # perd ~37% de l'échantillon, puissance réduite attendue
```

## Test 6 — Valeur incrémentale (le plus important)

Le spread de funding peut simplement réagir à un stress de prix/volatilité
déjà en cours. Spécification unique préenregistrée :

```yaml
regression:
  outcome: future_loss_24h
  regressors:
    - asset_fixed_effects
    - calendar_year_fixed_effects
    - interval_fixed_effects
    - {name: beta_stress, var: stress_flag}
    - {name: beta_drawdown, var: trailing_24h_drawdown}
    - {name: beta_rv, var: trailing_24h_realized_volatility}
  controls_causality: >
    trailing_24h_drawdown et trailing_24h_realized_volatility calculés
    UNIQUEMENT sur les barres mark-price entièrement disponibles avant
    decision_timestamp (jamais la fenêtre forward elle-même)
  inference: {bootstrap: moving_calendar_block, block_days: 7, resamples: 10000,
             resample_all_assets_jointly: true, seed: 20260721}
  gate: "beta_stress > 0 AND bootstrap_ci95_lower(beta_stress) > 0"
```

Si ce gate échoue seul (les autres passent) : l'association brute reste
vraie mais n'est pas démontrée incrémentale à un simple gate volatilité/
drawdown.

## Verdicts (mutuellement exclusifs, dans cet ordre de priorité)

```text
un gate de stabilité (test 1-4) échoue
  -> REPRODUCED_BUT_UNSTABLE_ASSOCIATION / NOT_RUNNER_QUALIFIED
  -> clôture de la voie runner, aucun sauvetage par seuil/horizon alternatif

seul le gate incrémental (test 6) échoue
  -> REPRODUCED_NON_INCREMENTAL_RISK_ASSOCIATION
  -> conservé comme documentation, pas de nouvelle feature de production

tous les gates passent (1-6, y compris test 6)
  -> VALIDATED_RISK_FEATURE_CANDIDATE / PORTFOLIO_VALUE_NOT_YET_PROVEN
  -> Phase 4 autorisée
```

Aucun cycle de sauvetage après échec. Budget : un seul passage complet des
6 tests.
