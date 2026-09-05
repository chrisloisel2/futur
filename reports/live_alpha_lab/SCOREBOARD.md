# Live Alpha Lab — scoreboard

Généré : 2026-09-05T19:52:07.607950+00:00

⚠ `operational_status=SIGNAL_SHADOW` signifie UNIQUEMENT que le signal tourne réellement.
Ça ne dit RIEN sur la validité de l'alpha — voir `scientific_status`. Seule la colonne
`forward_decisions` (event_time > freeze_timestamp) compte comme preuve jamais-vue ;
`replay_decisions` est du backfill historique, pas une preuve forward.

⚠ `lag_med_h` / `périmées` mesurent l'EXÉCUTABILITÉ, pas la validité : un alpha dont le
lab découvre les événements après l'expiration de son propre horizon accumule des décisions
forward correctes mais ne pourra JAMAIS engager de capital.

**Lire la colonne (24h), pas le cumul, pour juger l'état COURANT.** Le cumul inclut les
périodes où le lab tournait à la main et rattrapait plusieurs jours d'événements d'un coup :
ces décisions sont nées périmées et le restent à jamais dans le total. Exemple mesuré le
2026-09-05 : SHORT_COVERING_CONTINUATION_V1 affichait 160/360 périmées en cumul alors que
ses exécutions du jour tournaient à ~10 minutes de latence.

| alpha_id | family | scientific_status | operational_status | freeze_timestamp | replay | forward | independent_episodes | confidence | forward_age_h | last_trigger_h_ago | actual_freq/day | lag_med_h (cumul) | périmées (cumul) | lag_med_h (24h) | périmées (24h) | risk_bucket |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CROSS_SECTIONAL_MOMENTUM_PIT_V1 | cross_sectional | DISCOVERY | DATA_BLOCKED | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | CROSS_SECTIONAL_FAMILY |
| LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1 | liquidation | DISCOVERY | CODE_MISSING | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | LIQUIDATION_FAMILY |
| MICROSTRUCTURE_OFI_CLUSTER_V1 | microstructure | DISCOVERY | CODE_MISSING | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | MICROSTRUCTURE_FAMILY |
| OPTIONS_BLOCK_FLOW_TO_RV_V1 | options_vol_overlay | DISCOVERY | MERGED_INTO_VOL_FORECAST_LAYER_V1 | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | VOLATILITY_FAMILY |
| OPTIONS_FAR_OTM_PUT_SHARE_V1 | options_vol_overlay | DISCOVERY | MERGED_INTO_VOL_FORECAST_LAYER_V1 | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | VOLATILITY_FAMILY |
| OPTIONS_RV_IV_SPREAD_V1 | options_vol_overlay | DISCOVERY | MERGED_INTO_VOL_FORECAST_LAYER_V1 | None | 0 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | VOLATILITY_FAMILY |
| AMIHUD_ILLIQUIDITY_PREMIUM_V1 | cross_sectional | FROZEN | SIGNAL_SHADOW | 2026-09-02T11:20:10+00:00 | 24530 | **0** | 0 | TOO_EARLY | 80.5 | None | None | None | None | None | None | CROSS_SECTIONAL_FAMILY |
| BTC_LEAD_ALT_CASCADE_V1 | liquidation | FROZEN | SIGNAL_SHADOW | 2026-09-03T16:20:00+00:00 | 2494 | **31** | 31 | DEVELOPING | 51.5 | 31.1 | 14.447 | 18.5 | 31/31 | 18.5 | 31/31 | LIQUIDATION_FAMILY |
| FUNDING_BASIS_DISAGREEMENT_V2 | relative_value | FROZEN | SIGNAL_SHADOW | 2026-08-31T18:08:39+00:00 | 1 | **0** | 0 | TOO_EARLY | 121.7 | None | None | None | None | None | None | RELATIVE_VALUE_FAMILY |
| LIQ_CASCADE_REPEAT_SYSTEMIC_V1 | liquidation | FROZEN | SIGNAL_SHADOW | 2026-09-03T08:18:34+00:00 | 3654 | **9** | 5 | EARLY | 59.6 | 5.9 | 3.624 | 18.4 | 6/9 | 18.4 | 6/9 | LIQUIDATION_FAMILY |
| LIQ_CASCADE_REPEAT_V1 | liquidation | FROZEN | SIGNAL_SHADOW | 2026-08-31T00:00:00+00:00 | 5667 | **31** | 15 | EARLY | 139.9 | 5.9 | 5.318 | 35.4 | 27/31 | 18.3 | 15/19 | LIQUIDATION_FAMILY |
| VOL_FORECAST_LAYER_V1 | options_vol_overlay | FROZEN | SIGNAL_SHADOW | 2026-08-31T22:05:00+00:00 | 1325 | **5** | 5 | EARLY | 117.8 | 19.9 | 1.019 | 8.3 | 2/5 | 1.4 | 0/1 | VOLATILITY_FAMILY |
| LIQ_CASCADE_FAR_FROM_LOW_V1 | liquidation | INVALIDATED_PENDING_RESPEC | SIGNAL_SHADOW | 2026-08-31T18:08:39+00:00 | 6805 | **54** | 31 | DEVELOPING | 121.7 | 1.1 | 10.649 | 37.5 | 42/54 | 24.7 | 20/32 | LIQUIDATION_FAMILY |
| CROSS_SECTIONAL_MOMENTUM_LIVE_V1 | cross_sectional | RECONSTRUCTED | SIGNAL_SHADOW | 2026-08-31T18:30:44+00:00 | 2581 | **0** | 0 | TOO_EARLY | 121.4 | None | None | None | None | None | None | CROSS_SECTIONAL_FAMILY |
| CROSS_SECTIONAL_MOMENTUM_LIVE_V2 | cross_sectional | RECONSTRUCTED | SIGNAL_SHADOW | 2026-09-01T10:24:15+00:00 | 11217 | **0** | 0 | TOO_EARLY | 105.5 | None | None | None | None | None | None | CROSS_SECTIONAL_FAMILY |
| SHORT_COVERING_CONTINUATION_V1 | liquidation | RECONSTRUCTED | SIGNAL_SHADOW | 2026-08-31T18:08:39+00:00 | 189 | **388** | 91 | MEANINGFUL | 121.7 | 0.9 | 76.516 | 2.0 | 160/388 | 0.2 | 0/57 | LIQUIDATION_FAMILY |
| WHALE_LSR_SCREEN_V1 | positioning | RECONSTRUCTED | SIGNAL_SHADOW | 2026-08-31T18:08:39+00:00 | 3091 | **304** | 8 | EARLY | 121.7 | 4.5 | 59.951 | 6.1 | 52/304 | 2.5 | 0/46 | POSITIONING_WALLET_FAMILY |
| FUNDING_BASIS_DISAGREEMENT_V1 | relative_value | REJECTED | DATA_BLOCKED | None | 1 | **0** | 0 | TOO_EARLY | None | None | None | None | None | None | None | RELATIVE_VALUE_FAMILY |

**Total forward_decisions toutes familles : 822**.

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
