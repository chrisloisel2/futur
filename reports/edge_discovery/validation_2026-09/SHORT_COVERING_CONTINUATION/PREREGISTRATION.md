# PREREGISTRATION — SHORT_COVERING_CONTINUATION

**Worker :** V1, Alpha Validation Factory wave 2. **Écrit :** 2026-09-03, AVANT tout calcul de
rendement (seuls les schémas de `binance_vision_metrics`, la liste d'univers figée et le
`freeze_spec.json` de l'alpha live ont été inspectés).

**Réclamation testée** (`alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md`, rang 2 ;
evidence NON ouverte) : prix ↑ + OI ↓ (décile de queue du rendement 1 h et de la variation d'OI),
horizon `fwd_4h`, **excess vs baseline +9,2 bps plein / +19,0 bps OOS**, t 5,5 / 4,7,
n = 23 422 événements sur une population de 2 055 173 barres.

**Alpha live concerné :** `SHORT_COVERING_CONTINUATION_V1`, `SIGNAL_SHADOW`,
`scientific_status: RECONSTRUCTED` (script d'origine perdu, classification reconstruite).
Le `freeze_spec.json` a été lu en LECTURE SEULE — autorisé par le briefing §2 pour savoir ce qui
TOURNE — mais la réimplémentation part de la définition économique, pas du code
`src/institutional/engines/short_covering_continuation/`.

## 1. PRIMARY_SPEC — figée

| Item | Règle |
|---|---|
| Univers | Les 50 symboles figés de `configs/portfolio_v1_1_parallel_50.yaml` (même univers que l'alpha live, pour que la comparaison ait un sens). |
| Barres | Barres **horaires** UTC, construites indépendamment : prix = dernier close 5 m de l'heure (`data_v2/normalized/perp_ohlcv`), OI = dernier `sum_open_interest` de l'heure (`derivatives_backfill/binance_vision_metrics`). Jointure sur l'heure UTC ; heure sans prix OU sans OI = écartée, jamais imputée. |
| Features | `px_ret_1h(t) = close_t / close_{t−1h} − 1` ; `oi_delta_1h(t) = oi_t / oi_{t−1h} − 1`. Strictement causales. |
| Centiles | Rang centile **causal** de chaque feature dans la fenêtre glissante des **720 heures (30 j) précédentes**, barre courante **exclue** de sa propre population de référence : `pctile(t) = #{x_s < x_t, s ∈ [t−720h, t−1h]} / 720`. Fenêtre pleine exigée (720 antécédents), sinon la barre est écartée et comptée. |
| **Bras A (état SHORT_COVERING)** | `px_ret_1h_pctile ≥ 0,90` **ET** `oi_delta_1h_pctile ≤ 0,10` — conjonction, exactement la construction réclamée (prix dans la queue haute, OI dans la queue basse). |
| **Bras B (baseline)** | **Toutes les autres barres éligibles**, même univers, même période. C'est la baseline de la réclamation ; le verdict porte sur l'EXCESS A − B, jamais sur « A > 0 ». |
| Trade | LONG, horizon `fwd_4h` = `close_{t+4h} / close_t − 1`. Sortie au dernier close disponible dans `(t, t+4h]`. |
| Coût | `net14 = brut − 14`, stress `net28 = brut − 28`. |
| Période | 2022-01-01 → fin des données, UTC. |

## 2. Déclustering

- **L1** = même symbole, chaîne < 4 h (l'horizon : deux signaux du même nom dans la même fenêtre
  de détention ne sont pas deux observations).
- **L2** = jour calendaire UTC, tous symboles.
- **L3 (unité d'inférence)** = **épisode cross-symbole chaîné, gap < 4 h**. Un short-squeeze est
  un mouvement corrélé : plusieurs noms basculent dans l'état dans la même heure.
- Inférence : t cluster-robuste sur L3, block bootstrap par épisode. **Double lecture
  obligatoire** — moyenne par épisode ET moyenne par événement avec SE cluster-robuste.

## 3. Perturbations préenregistrées (≤ 8)

| # | Perturbation | But |
|---|---|---|
| P1 | Décile → quintile (0,80 / 0,20) | sensibilité au découpage de queue |
| P2 | Fenêtre de référence 360 h au lieu de 720 h | sensibilité à la mémoire du centile |
| P3 | Score combinateur `min(px_pctile, 1 − oi_pctile) ≥ 0,90` (le score de la spec live) | la classification live porte-t-elle l'edge ? |
| P4 | Horizon `fwd_1h` et `fwd_8h` | sensibilité d'horizon |
| P5 | Hors 2021-2022 (régime de départ) | concentration de régime |
| P6 | Coût +50 % (21 bps) | fragilité au coût |
| P7 | Bras A restreint aux barres SANS cascade de liquidation concomitante (± 4 h) | l'edge est-il un doublon du fade de cascade ? |
| P8 | OI en notionnel (`sum_open_interest_value`) au lieu du nombre de contrats | définition de l'OI |

## 4. Contrôles obligatoires

- **Chevauchement** avec `LIQ_CASCADE_REPEAT_V1` (ledger, lecture seule) et avec la population
  cascade `LONG_CASCADE` : part des barres du bras A à ± 5 min d'un événement de cascade.
- **Accord avec le ledger live** `SHORT_COVERING_CONTINUATION_V1/decisions.parquet` (lecture
  seule) : taux d'accord décision par décision entre ma classification et la sienne sur la
  fenêtre commune — c'est le test de la spec RECONSTRUITE.
- **ETA** : c'est l'alpha à la plus haute fréquence du projet, donc celui au meilleur ETA
  potentiel. Taux d'épisodes L3 sur 2 ans / 6 mois, `n_required` par block bootstrap
  (α = 5 % unilatéral, puissance 80 %, edge haircuté 50 %), `minimum_calendar_days = 60`.

## 5. Critères de succès

1. `excess_net14 = A − B > 0` avec `t_L3 ≥ 1,645` **dans les deux conventions de pondération**
   (épisode et événement cluster-robuste) — la leçon `LIQ_CASCADE_FAR_FROM_LOW` de cette vague.
2. `excess_net28 > 0`, sinon `COST_FRAGILE` au mieux.
3. ≥ 4/5 années positives sur l'excess.
4. Le bras A seul (`net14` brut vs zéro) > 0 — un produit doit aussi battre zéro.
5. Chevauchement avec `LIQ_CASCADE_REPEAT_V1` ≤ 50 %.

`t_L3 < 1,0` → `REJECTED` ; `1,0 ≤ t_L3 < 1,645` → `NEEDS_MORE_RESEARCH`.
`recommended_next_step` attendu ∈ {`UPGRADE_LIVE_STATUS`, `DOWNGRADE_LIVE_STATUS`}.
Aucun paramètre n'est modifié après avoir vu un résultat.
