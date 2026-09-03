# VALIDATION_AND_FORWARD_SCOREBOARD

Généré : 2026-09-03T08:33:00.405388+00:00

| alpha_id | family | discovery_net_bps | validation_net_bps | N_validation_independent | validated_for_forward | freeze_timestamp | historical_event_rate | recent_event_rate | N_required | minimum_calendar_days | ETA_P50 | ETA_conservative | forward_age_days | forward_N_independent | forward_net_bps | edge_retention | scientific_status | operational_status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AMIHUD_ILLIQUIDITY_PREMIUM_V1 | cross_sectional | 99.3 | 105.7 | 332 | True | 2026-09-02T11:20:10+00:00 | 1.007/week (last 2y) | 1.033/week (last 6m) | 900 | 182 | 6198 days (~17.0 years) | 6257 days (~17.1 years) | — | — | — | INSUFFICIENT_EVIDENCE | FROZEN | SIGNAL_SHADOW |
| LIQ_REPEAT_VOL_GATE | liquidation | — | — | — | False | 2026-08-31T00:00:00+00:00 | — | — | — | — | — | — | 3.3 | — | — | INSUFFICIENT_EVIDENCE | NEEDS_MORE_RESEARCH | SIGNAL_SHADOW |
| LIQ_REPEAT_DENSITY | liquidation | 39.5 | 22.1 | 1165 | True | 2026-08-31T00:00:00+00:00 | — | ~0.775 épisode/jour (stable) | 2654 | 60 | ~3423 jours (~9.4 ans) | ~3423 jours (~9.4 ans, dominé par variance inter-épisode pas rareté) | 3.3 | — | — | INSUFFICIENT_EVIDENCE | VALIDATED_FOR_FORWARD | SIGNAL_SHADOW |
| LIQ_REPEAT_SKEW_OVERLAY | liquidation | — | — | 579 | True | 2026-08-31T00:00:00+00:00 | — | — | 3069 | 60 | ~11.4 ans (effet plein) | ~46 ans (effet haircut 50%%) | 3.3 | — | — | INSUFFICIENT_EVIDENCE | VALIDATED_FOR_FORWARD | SIGNAL_SHADOW |
| CROSS_SECTIONAL_MOMENTUM_CVD | cross_sectional | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | REJECTED | CODE_MISSING |
| BTC_ETH_CURVE_STEEPNESS | relative_value | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | REJECTED | CODE_MISSING |
| POSITIONING_TAKER_FLOW | positioning | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | REJECTED | CODE_MISSING |
| GLOBAL_ACCOUNT_LSR_FADE | positioning | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | REJECTED | CODE_MISSING |
| OI_CVD_MEMORY_OVERLAP | liquidation | — | — | — | False | 2026-08-31T00:00:00+00:00 | — | — | — | — | — | — | 3.3 | — | — | INSUFFICIENT_EVIDENCE | REJECTED | SIGNAL_SHADOW |
| LIQ_CASCADE_FAR_FROM_LOW | liquidation | — | — | — | False | 2026-08-31T18:08:39+00:00 | — | — | — | — | — | — | 2.3 | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | SIGNAL_SHADOW |
| BTC_LEAD_ALT_CASCADE | liquidation | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| XSEC_MOMENTUM_HORIZON_EXTENSION | cross_sectional | — | — | — | False | 2026-09-01T10:24:15+00:00 | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | SIGNAL_SHADOW |
| XSEC_RESIDUAL_MOMENTUM_14D | cross_sectional | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| XSEC_RELATIVE_LEVERAGE_14D | cross_sectional | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| CROSS_ASSET_OI_BUILDUP_FADE | relative_value | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| SECTOR_RELATIVE_STRENGTH_REVERSAL | relative_value | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| SECTOR_ROTATION | relative_value | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| BASIS_RICHENING_FADE | relative_value | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| BASIS_FUNDING_AGREEMENT_FADE | relative_value | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| XSMOM_REGIME_META | cross_sectional | — | — | — | False | 2026-09-01T10:24:15+00:00 | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | SIGNAL_SHADOW |
| FUNDING_CARRY_X_DISPERSION | relative_value | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| OPTIONS_IV_SHOCK_MEMORY | options_vol_overlay | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| SPILLOVER_X_DVOL_STRESS | options_vol_overlay | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | CODE_MISSING |
| SHORT_COVERING_CONTINUATION | liquidation | — | — | — | False | 2026-08-31T18:08:39+00:00 | — | — | — | — | — | — | 2.6 | — | — | INSUFFICIENT_EVIDENCE | VALIDATING | SIGNAL_SHADOW |
| FUNDING_BASIS_DISAGREEMENT | relative_value | — | — | — | False | 2026-08-31T18:08:39+00:00 | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | ALREADY_LIVE | SIGNAL_SHADOW |
| OPTIONS_RV_IV_SPREAD | options_vol_overlay | — | — | — | False | 2026-08-31T22:05:00+00:00 | — | — | — | — | — | — | 2.4 | — | — | INSUFFICIENT_EVIDENCE | ALREADY_LIVE | SIGNAL_SHADOW |
| OPTIONS_FAR_OTM_PUT_SHARE | options_vol_overlay | — | — | — | False | 2026-08-31T22:05:00+00:00 | — | — | — | — | — | — | 2.4 | — | — | INSUFFICIENT_EVIDENCE | ALREADY_LIVE | SIGNAL_SHADOW |
| OPTIONS_BLOCK_FLOW | options_vol_overlay | — | — | — | False | 2026-08-31T22:05:00+00:00 | — | — | — | — | — | — | 2.4 | — | — | INSUFFICIENT_EVIDENCE | ALREADY_LIVE | SIGNAL_SHADOW |
| WHALE_LSR | positioning | — | — | — | False | 2026-08-31T18:08:39+00:00 | — | — | — | — | — | — | 2.4 | — | — | INSUFFICIENT_EVIDENCE | ALREADY_LIVE | SIGNAL_SHADOW |
| LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION | liquidation | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | BLOCKED | CODE_MISSING |
| MICROSTRUCTURE_ALL_ROUND3 | microstructure | — | — | — | False | — | — | — | — | — | — | — | — | — | — | INSUFFICIENT_EVIDENCE | DATA_ACCUMULATION | CODE_MISSING |
