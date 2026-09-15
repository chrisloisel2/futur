# Live Alpha Lab — scoreboard

Généré : 2026-09-15T04:39:38.914848+00:00

⚠ `operational_status=SIGNAL_SHADOW` signifie UNIQUEMENT que le signal tourne réellement.
Ça ne dit RIEN sur la validité de l'alpha — voir `scientific_status`. Seule la colonne
`forward_decisions` (event_time > freeze_timestamp) compte comme preuve jamais-vue ;
`replay_decisions` est du backfill historique, pas une preuve forward.

⚠ `last_trigger_h_ago` vs `last_attempt_h_ago` : le premier dit quand l'alpha a DÉCLENCHÉ,
le second quand son producteur a TENTÉ. Un alpha rare peut n'avoir rien déclenché depuis
des jours sans anomalie ; un producteur qui n'a pas tenté depuis des jours est une panne.
Sans les deux, les deux cas sont indiscernables — et `run_state.json::last_run` n'est écrit
QUE quand un producteur a produit quelque chose (les 10 runners ont un `return 0` anticipé
sur « rien de nouveau » qui précède l'écriture), donc il ne répond pas à la question.

⚠ `lag_med_h` / `périmées` mesurent l'EXÉCUTABILITÉ, pas la validité : un alpha dont le
lab découvre les événements après l'expiration de son propre horizon accumule des décisions
forward correctes mais ne pourra JAMAIS engager de capital.

**Lire la colonne (24h), pas le cumul, pour juger l'état COURANT.** Le cumul inclut les
périodes où le lab tournait à la main et rattrapait plusieurs jours d'événements d'un coup :
ces décisions sont nées périmées et le restent à jamais dans le total. Exemple mesuré le
2026-09-05 : SHORT_COVERING_CONTINUATION_V1 affichait 160/360 périmées en cumul alors que
ses exécutions du jour tournaient à ~10 minutes de latence.

| alpha_id | family | scientific_status | operational_status | freeze_timestamp | replay | forward | independent_episodes | confidence | forward_age_h | last_trigger_h_ago | last_attempt_h_ago | actual_freq/day | lag_med_h (cumul) | périmées (cumul) | lag_med_h (24h) | périmées (24h) | risk_bucket |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CROSS_SECTIONAL_MOMENTUM_PIT_V1 | cross_sectional | DISCOVERY | DATA_BLOCKED | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | None | CROSS_SECTIONAL_FAMILY |
| LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1 | liquidation | DISCOVERY | CODE_MISSING | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | None | LIQUIDATION_FAMILY |
| MICROSTRUCTURE_OFI_CLUSTER_V1 | microstructure | DISCOVERY | CODE_MISSING | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | None | MICROSTRUCTURE_FAMILY |
| OPTIONS_BLOCK_FLOW_TO_RV_V1 | options_vol_overlay | DISCOVERY | MERGED_INTO_VOL_FORECAST_LAYER_V1 | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | None | VOLATILITY_FAMILY |
| OPTIONS_FAR_OTM_PUT_SHARE_V1 | options_vol_overlay | DISCOVERY | MERGED_INTO_VOL_FORECAST_LAYER_V1 | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | None | VOLATILITY_FAMILY |
| OPTIONS_RV_IV_SPREAD_V1 | options_vol_overlay | DISCOVERY | MERGED_INTO_VOL_FORECAST_LAYER_V1 | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | None | VOLATILITY_FAMILY |
| AMIHUD_ILLIQUIDITY_PREMIUM_V1 | cross_sectional | FROZEN | SIGNAL_SHADOW | 2026-09-02T11:20:10+00:00 | 24530 | **166** | 166 | STRONG | 305.3 | 148.7 | 2.9 OK | 13.049 | 25.9 | 0/166 | None | None | CROSS_SECTIONAL_FAMILY |
| BTC_LEAD_ALT_CASCADE_V1 | liquidation | FROZEN | SIGNAL_SHADOW | 2026-09-03T16:20:00+00:00 | 2494 | **31** | 31 | DEVELOPING | 276.3 | 255.9 | 0.1 OK | 2.693 | 18.5 | 31/31 | None | None | LIQUIDATION_FAMILY |
| FUNDING_BASIS_DISAGREEMENT_V2 | relative_value | FROZEN | SIGNAL_SHADOW | 2026-08-31T18:08:39+00:00 | 1 | **0** | 0 | TOO_EARLY | 346.5 | None | 0.7 OK | None | None | None | None | None | RELATIVE_VALUE_FAMILY |
| LIQ_CASCADE_REPEAT_SYSTEMIC_V1 | liquidation | FROZEN | SIGNAL_SHADOW | 2026-09-03T08:18:34+00:00 | 3654 | **36** | 24 | DEVELOPING | 284.4 | 25.1 | 0.1 OK | 3.038 | 0.3 | 11/36 | None | None | LIQUIDATION_FAMILY |
| LIQ_CASCADE_REPEAT_V1 | liquidation | FROZEN | SIGNAL_SHADOW | 2026-08-31T00:00:00+00:00 | 5667 | **76** | 43 | DEVELOPING | 364.7 | 3.6 | 0.1 OK | 5.001 | 0.4 | 35/76 | 0.3 | 0/3 | LIQUIDATION_FAMILY |
| VOL_FORECAST_LAYER_V1 | options_vol_overlay | FROZEN | SIGNAL_SHADOW | 2026-08-31T22:05:00+00:00 | 1325 | **15** | 15 | EARLY | 342.6 | 4.7 | 0.7 OK | 1.051 | 1.8 | 3/15 | 1.7 | 0/1 | VOLATILITY_FAMILY |
| LIQ_CASCADE_FAR_FROM_LOW_V1 | liquidation | INVALIDATED_PENDING_RESPEC | SIGNAL_SHADOW | 2026-08-31T18:08:39+00:00 | 6805 | **128** | 74 | MEANINGFUL | 346.5 | 1.5 | 0.1 OK | 8.866 | 0.3 | 52/128 | 0.3 | 0/3 | LIQUIDATION_FAMILY |
| PLACEBO_RANDOM_V1 | control | PLACEBO | SIGNAL_SHADOW | 2026-09-06T09:55:26+00:00 | 4 | **3272** | 50 | MEANINGFUL | 210.7 | 0.2 | 0.1 OK | 372.701 | 0.1 | 0/3272 | 0.1 | 0/376 | CONTROL_FAMILY |
| POSITIVE_CONTROL_ORACLE_V1 | control | POSITIVE_CONTROL | OFFLINE_INSTRUMENT | None | 0 | **1408** | 860 | STRONG | None | None | None | None | 0.0 | 0/1408 | None | None | CONTROL_FAMILY |
| CROSS_SECTIONAL_MOMENTUM_LIVE_V1 | cross_sectional | RECONSTRUCTED | SIGNAL_SHADOW | 2026-08-31T18:30:44+00:00 | 2581 | **20** | 20 | DEVELOPING | 346.1 | 28.7 | 3.0 OK | 1.387 | 27.6 | 0/20 | 25.7 | 0/10 | CROSS_SECTIONAL_FAMILY |
| CROSS_SECTIONAL_MOMENTUM_LIVE_V2 | cross_sectional | RECONSTRUCTED | SIGNAL_SHADOW | 2026-09-01T10:24:15+00:00 | 11217 | **96** | 96 | MEANINGFUL | 330.3 | 28.7 | 3.0 OK | 6.975 | 29.6 | 0/96 | 25.8 | 0/43 | CROSS_SECTIONAL_FAMILY |
| SHORT_COVERING_CONTINUATION_V1 | liquidation | RECONSTRUCTED | SIGNAL_SHADOW | 2026-08-31T18:08:39+00:00 | 189 | **914** | 234 | STRONG | 346.5 | 0.7 | 0.1 OK | 63.307 | 0.2 | 160/914 | 0.0 | 0/62 | LIQUIDATION_FAMILY |
| WHALE_LSR_SCREEN_V1 | positioning | RECONSTRUCTED | SIGNAL_SHADOW | 2026-08-31T18:08:39+00:00 | 3091 | **754** | 16 | EARLY | 346.5 | 80.6 | 0.1 OK | 52.225 | 3.5 | 52/754 | None | None | POSITIONING_WALLET_FAMILY |
| FUNDING_BASIS_DISAGREEMENT_V1 | relative_value | REJECTED | DATA_BLOCKED | None | 1 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | None | RELATIVE_VALUE_FAMILY |

**Total forward_decisions toutes familles : 6916**.

⚠ **PF / net_bps / maxDD / edge_retention ne sont PAS encore calculés** pour les alphas
de position (nécessite un label de résultat forward par décision, comme le backfill
`actual_realized_rv` de VOL_FORECAST_LAYER_V1 mais pour chaque alpha directionnel —
pas encore construit, prochaine étape logique une fois plus de forward accumulé).

⚠ **EXÉCUTABILITÉ — constat du 2026-09-05.** La famille cascade de liquidation
(`LIQ_CASCADE_REPEAT_V1`, `LIQ_CASCADE_REPEAT_SYSTEMIC_V1`, `LIQ_CASCADE_FAR_FROM_LOW_V1`,
`BTC_LEAD_ALT_CASCADE_V1`) lit `data/derivatives_backfill/binance_vision_metrics/`, un
backfill d'archives quotidiennes Binance Vision structurellement en retard de 1 à 2 jours.
Mesuré : **100% de ses décisions forward arrivent 45-48h après l'événement, pour un
horizon de 4h** — elles sont périmées à l'arrivée et ne peuvent pas recevoir de capital.
Ce n'est pas un creux de marché, c'est une impossibilité d'architecture. Ces alphas
accumulent une preuve de SIGNAL valable, pas une preuve de STRATÉGIE exécutable.
Détail et options de correction : `reports/live_alpha_lab/DECISION_LATENCY_AUDIT_2026-09-05.md`.

**0 signal pendant quelques heures n'est PAS un problème** — les cascades de liquidation,
le funding-basis (~15-18/an/actif) et le screen positioning sont des mécanismes rares par
construction. `actual_freq_per_day` est là pour comparer objectivement à
`expected_capacity` (texte libre du registre) le moment venu, pas pour juger après
quelques heures.

---

## Candidats non implémentés depuis plus de 30 jours

Aucun. (4 candidat(s) ne tournent pas mais sont sous le seuil : LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1, FUNDING_BASIS_DISAGREEMENT_V1, CROSS_SECTIONAL_MOMENTUM_PIT_V1, MICROSTRUCTURE_OFI_CLUSTER_V1.)

---

## RÉSULTATS FORWARD — ce que les décisions ont réellement rapporté

Source : `reports/live_alpha_lab/<ALPHA>/outcomes.parquet`, ledger de labels
**scellés** (append-only, jamais réécrits) écrit par `scripts/label_forward_outcomes.py`
à chaque cycle, à l'échéance de l'horizon de chaque décision.

⚠ **`net_excess` est le seul chiffre qui mesure un edge.** Les cinq alphas
labellisables sont long-only et l'univers frozen-50 a pris **+10,9 %** sur la fenêtre
forward : le rendement BRUT de n'importe quelle position longue y est positif, signal
ou pas. `net_excess` retranche le rendement de l'univers sur exactement la même
fenêtre. L'écart entre `net_gross` et `net_excess` EST le bêta.

⚠ **Ancrages.** `dec` = à partir de `decided_at`, ce que le lab pouvait réellement
capturer. `evt` = à partir de `event_time`, ce que le backtest de validation a mesuré.
Seul `evt` est comparable à `expected_net_bps`, donc seul `evt` alimente
`edge_retention`. L'écart entre les deux est le coût de la latence.

⚠ **Coûts.** `net@14` = coût exact du simulateur (aller-retour) ; `net@28` = borne haute. Le slippage
est une CONSTANTE de 2 bps alors que ces alphas tradent pendant les cascades,
c'est-à-dire au moment où les spreads s'écartent le plus. Un résultat qui ne survit
pas à la borne haute est une hypothèse, pas un résultat.

⚠ **Seuil d'échantillon déclaré : 20 épisodes indépendants.**
En dessous, AUCUN chiffre n'est imprimé — ni moyenne, ni intervalle, ni hit rate.
Un IC calculé sur un seul épisode a l'air précis parce qu'il n'a pas de largeur.
Aucune métrique annualisée n'est produite ici, à aucun `n`.

| alpha_id | n_lab | n_épisodes | scellés/tardifs | anc. | net_gross@14 | net_excess@14 | net_excess@28 | PF | hit | IC95 excess@14 | edge_retention |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC_LEAD_ALT_CASCADE_V1 | 31 | 1 | 0/31 | — | INSUFFICIENT_SAMPLE (n_ep=1 < 20) | | | | | | |
| LIQ_CASCADE_FAR_FROM_LOW_V1 | 124 | 71 | 65/61 | `dec` | -0.5 | -15.5 | -29.5 | 0.771 | 0.352 | [-54.5, +21.4] | — |
|  | 117 | 69 | 65/61 | `evt` | -59.9 | -61.3 | -75.3 | 0.419 | 0.333 | [-100.8, -19.8] | -13.359 (ABSOLUTE) |
| LIQ_CASCADE_REPEAT_SYSTEMIC_V1 | 35 | 23 | 26/10 | `dec` | +33.1 | +11.0 | -3.0 | 1.184 | 0.304 | [-62.7, +101.9] | — |
|  | 34 | 23 | 26/10 | `evt` | -34.3 | -29.9 | -43.9 | 0.666 | 0.348 | [-108.9, +69.4] | -1.551 (ABSOLUTE) |
| LIQ_CASCADE_REPEAT_V1 | 72 | 40 | 41/33 | `dec` | -4.8 | -8.0 | -22.0 | 0.879 | 0.4 | [-61.5, +56.2] | — |
|  | 69 | 39 | 41/33 | `evt` | -50.9 | -21.1 | -35.1 | 0.723 | 0.308 | [-77.1, +49.8] | -1.879 (ABSOLUTE) |
| ⚙️ PLACEBO_RANDOM_V1 _(contrôle négatif)_ | 2755 | 75 | 2755/400 | `dec` | -10.2 | -8.2 | -22.2 | 0.577 | 0.267 | [-17.7, +2.7] | — |
|  | 2755 | 75 | 2755/400 | `evt` | -10.6 | -8.7 | -22.7 | 0.559 | 0.24 | [-17.9, +2.4] | base non déclarée |
| ⚙️ POSITIVE_CONTROL_ORACLE_V1 _(contrôle positif — LOOK-AHEAD)_ | 1408 | 860 | 0/1408 | `dec` | +12.4 | +5.8 | -8.2 | 1.191 | 0.492 | [-1.7, +13.6] | — |
|  | 1408 | 860 | 0/1408 | `evt` | +12.4 | +5.8 | -8.2 | 1.191 | 0.492 | [-1.7, +13.6] | base non déclarée |
| SHORT_COVERING_CONTINUATION_V1 | 881 | 235 | 471/439 | `dec` | -11.6 | -8.0 | -22.0 | 0.766 | 0.387 | [-19.7, +4.8] | — |
|  | 881 | 235 | 471/439 | `evt` | -9.9 | -6.1 | -20.1 | 0.821 | 0.413 | [-17.7, +6.4] | -0.659 (EXCESS_VS_BASELINE) |

### Hors périmètre du label, avec motif

- **AMIHUD_ILLIQUIDITY_PREMIUM_V1** — NO_FORWARD_DECISIONS — 0 décision FORWARD_LIVE (tout REPLAY).
- **CROSS_SECTIONAL_MOMENTUM_LIVE_V1** — NO_FORWARD_DECISIONS — 0 décision FORWARD_LIVE (tout REPLAY).
- **CROSS_SECTIONAL_MOMENTUM_LIVE_V2** — NO_FORWARD_DECISIONS — 0 décision FORWARD_LIVE (tout REPLAY).
- **CROSS_SECTIONAL_MOMENTUM_PIT_V1** — pas de ledger de décisions — operational_status=DATA_BLOCKED
- **FUNDING_BASIS_DISAGREEMENT_V1** — NO_FORWARD_DECISIONS — scientific_status=REJECTED, jamais lancé sous cette identité (jambe perp stale). Rien à labelliser.
- **FUNDING_BASIS_DISAGREEMENT_V2** — SIGNAL_SHADOW_PUR — le registre déclare explicitement « AUCUN exit simulé, AUCUN fill » ; k30d n'est qu'une fenêtre de decluster, pas un horizon de détention. Labelliser reviendrait à inventer une stratégie que la spec refuse de définir.
- **LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1** — pas de ledger de décisions — operational_status=CODE_MISSING
- **MICROSTRUCTURE_OFI_CLUSTER_V1** — pas de ledger de décisions — operational_status=CODE_MISSING
- **OPTIONS_BLOCK_FLOW_TO_RV_V1** — pas de ledger de décisions — operational_status=MERGED_INTO_VOL_FORECAST_LAYER_V1
- **OPTIONS_FAR_OTM_PUT_SHARE_V1** — pas de ledger de décisions — operational_status=MERGED_INTO_VOL_FORECAST_LAYER_V1
- **OPTIONS_RV_IV_SPREAD_V1** — pas de ledger de décisions — operational_status=MERGED_INTO_VOL_FORECAST_LAYER_V1
- **VOL_FORECAST_LAYER_V1** — HAS_OWN_LABEL_MECHANISM — alpha de volatilité, pas de direction de prix. Son label de résultat existe déjà et lui est propre : `actual_realized_rv` (src/institutional/engines/vol_forecast_layer/backfill.py). Le dupliquer ici produirait deux vérités concurrentes pour la même décision.
- **WHALE_LSR_SCREEN_V1** — NOT_DIRECTIONAL_SCREEN — aucune colonne `direction` : c'est un écran de positionnement consommé comme GATE par le portefeuille, pas une position. Ses 304 décisions forward ne sont pas des trades et ne peuvent pas porter un rendement directionnel.

### Ce que ce tableau ne dit pas

- **Il ne dit rien d'un Sharpe.** Cinq jours, un seul régime, un marché qui monte de
  près de 11 % : la question « quel edge par décision » (n = épisodes, mesurable) et
  la question « quel Sharpe » (n = 5 jours, non mesurable) n'ont pas la même taille
  d'échantillon, et la seconde ne se déduit pas de la première.
- **Le contrôle est `PLACEBO_RANDOM_V1`, et il se lit AVANT les autres lignes.**
  Signal aléatoire (4 symboles du frozen-50 par cycle, LONG, fwd_4h), labellisé par
  exactement la même chaîne : même source de prix, mêmes ancrages, même decluster,
  même référence de marché, même modèle de coût. Il n'a aucun edge par construction,
  donc **tout ce qu'il affiche est un biais de la mesure**. Si son `net_excess` est
  significativement non nul, tous les chiffres de ce tableau sont à relire ; s'il est
  nul avec un intervalle serré, ils sont lisibles tels quels. C'est le seul instrument
  qui mesure l'infrastructure elle-même — aucune statistique sur les alphas réels ne
  peut le remplacer. Il ne reçoit jamais de capital (`eligibility.BLOCK_PLACEBO`).
  Son compteur forward démarre au 2026-09-06 : sous 20 épisodes il affiche
  `INSUFFICIENT_SAMPLE` comme n'importe quel autre, et il n'est pas encore lisible.
  **Son nombre d'épisodes plafonne à ~49, et attendre n'en créera pas d'autres.**
  `episodes.decluster` chaîne par lien simple : il tire ~8 fois par symbole et par
  jour, donc tous ses tirages d'un même symbole fusionnent en UN épisode. Le plafond
  est le nombre de symboles de l'univers. Ce qui continue de s'améliorer avec le
  temps est la DENSITÉ de chaque épisode, donc la largeur de l'intervalle : simulé
  sur son propre design, son null vaut [−6,9 ; +8,8] bps à trois jours de collecte et
  [−5,5 ; +6,0] à cinq. C'est à cet intervalle-là qu'il faut le comparer, pas à zéro.
  Voir `APPARATUS_NULL.md`.
- **`POSITIVE_CONTROL_ORACLE_V1` est l'autre borne, et sa ligne N'EST PAS un résultat.**
  Il choisit ses décisions EN CONNAISSANT leur rendement (look-ahead délibéré) pour
  mesurer ce que la chaîne RETIRE à un edge qui existe — là où le placebo mesure ce
  qu'elle AJOUTE à un edge qui n'existe pas. Verdict mesuré : la chaîne restitue
  l'edge injecté au bit près (écart nul par décision, pente 1,03). Elle ne détruit
  donc aucun signal. Ce qui manque aux alphas réels n'est pas la fidélité de la
  mesure, c'est le nombre d'épisodes : à σ ≈ 112 bps par épisode, voir +15 bps nets
  demande ~116 épisodes indépendants, et quatre des cinq alphas en ont moins de 40.
  Voir `POSITIVE_CONTROL_RECOVERY.md`. Il ne reçoit jamais de capital
  (`eligibility.BLOCK_POSITIVE_CONTROL`).
- **Les labels `LATE_BACKFILL` ne sont pas des labels scellés à l'échéance.** Le prix
  relevé est honnête (les partitions de `derivatives_raw` ne sont pas réécrites), mais
  rien ne garantit que la règle de labellisation ait été fixée avant d'avoir vu la
  donnée. Seule la colonne `scellés` porte cette garantie, et elle ne peut que croître
  à partir du 2026-09-06.
- **`edge_retention` contre une référence RECONSTRUCTED ne confirme rien.** Le registre
  le dit déjà pour SHORT_COVERING et WHALE_LSR : leur `expected_net_bps` vient d'un
  seuil ajusté sur ces mêmes données, c'est un contexte historique, pas une cible.
