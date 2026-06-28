# Binance×Bybit Funding Edge Report

- symbols: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
- overlap: 2022-11-03 → 2026-06-28  (15,895 obs 8h, 4 actifs)
- funding_spread (bybit−binance) médian: 0.00 bps  p99 abs: 3.37 bps

## H3 — Carry Gate V2 (priorité)

| set | n | net_carry_24h moyen | flip_rate_24h |
|---|---:|---:|---:|
| tous | 15883 | 0.89 bps | 36.9% |
| **gated (pos_both & disp<90pct)** | 8638 | **2.36 bps** | 25.5% |

→ Carry gate AMÉLIORE le net carry / flips. **CARRY_GATE_V2 = VALIDATED**

## H2 — Risk-off Gate (dispersion → drawdown futur)

| set | n | future_maxDD_24h moyen | future_ret_24h moyen |
|---|---:|---:|---:|
| tous | 15895 | -1.82% | +0.12% |
| **top5% abs_spread** | 813 | **-2.05%** | +0.23% |

→ Dispersion élevée PRÉCÈDE des DD plus profonds. **CROSS_EXCHANGE_STRESS_GATE = VALIDATED**

## H1 — Directionnel (spread_zscore → forward_return_24h)

| decile spread_z | fwd_ret_24h moyen |
|---|---:|
| D1(bas) | +0.070% |
| D2 | +0.206% |
| D3 | +0.163% |
| D4 | +0.069% |
| D5(haut) | +0.134% |

→ monotonicité par decile : False. **pas d edge directionnel brut**

## Robustesse par année (net carry gated − base)
| année | Δ net_carry_24h (bps) |
|---|---:|
| 2022 | +3.96 |
| 2023 | +1.82 |
| 2024 | +1.24 |
| 2025 | +0.78 |
| 2026 | +0.75 |

## Décision (priorité carry > risk > directionnel)
- CARRY_GATE_V2 : VALIDATED
- CROSS_EXCHANGE_STRESS_GATE : VALIDATED
- Directionnel : voir monotonicité ci-dessus (attendu : faible)