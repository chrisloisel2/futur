# Moteur MULTI-HORIZON — optimisation structurelle par horizon (OOS)

Fine-tuning régularisation par horizon sur VALIDATION purgée (jamais test). Chaque moteur à ses horizons ; comparaison à son horizon de trade.


## LIQ_CASCADE (trade fwd_4h, exit fwd_8h)
| config | n | PF | mean bps | net (unités) |
|---|---:|---:|---:|---:|
| BASELINE | 6284 | 1.227 | +24.9 | +156288 (×1.00) |
| TERM_HOLD | 6283 | 1.359 | +44.0 | +276358 (×1.77) |
| CONSENSUS | 2218 | 1.443 | +56.6 | +125479 (×0.80) |
| COMBINED | 2218 | 1.646 | +95.3 | +211338 (×1.35) |
| COMBINED_costx2 | 2218 | 1.529 | +81.3 | +180286 (×1.15) |

_COMBINED PF par année : {2023: 2.18, 2024: 1.55, 2025: 1.92, 2026: 1.31}_

## PREMIUM_DISLOCATION (trade fwd_4h, exit fwd_8h)
| config | n | PF | mean bps | net (unités) |
|---|---:|---:|---:|---:|
| BASELINE | 4263 | 1.456 | +54.1 | +230698 (×1.00) |
| TERM_HOLD | 4263 | 1.493 | +68.1 | +290400 (×1.26) |
| CONSENSUS | 1486 | 2.732 | +189.4 | +281394 (×1.22) |
| COMBINED | 1486 | 2.761 | +228.7 | +339831 (×1.47) |
| COMBINED_costx2 | 1486 | 2.586 | +214.7 | +319027 (×1.38) |

_COMBINED PF par année : {2023: 1.89, 2024: 2.72, 2025: 3.96, 2026: 1.22}_

## CROWDING_REVERSAL (trade fwd_8h, exit fwd_24h)
| config | n | PF | mean bps | net (unités) |
|---|---:|---:|---:|---:|
| BASELINE | 298 | 5.332 | +367.3 | +109446 (×1.00) |
| TERM_HOLD | 298 | 4.413 | +400.3 | +119282 (×1.09) |
| CONSENSUS | 172 | 7.978 | +541.5 | +93133 (×0.85) |
| COMBINED | 172 | 5.65 | +577.5 | +99324 (×0.91) |
| COMBINED_costx2 | 172 | 5.404 | +563.5 | +96916 (×0.89) |

_COMBINED PF par année : {2024: 3.71, 2025: 13.73}_