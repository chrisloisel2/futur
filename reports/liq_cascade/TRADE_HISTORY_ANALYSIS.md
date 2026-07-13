# Analyse du journal de transactions — stack 3 moteurs (OOS, net de frais)

**11333 trades · win rate 53% · profit factor 1.277 · espérance +36.0 bps/trade**
gain moyen +311 bps · perte moyenne -277 bps (ratio 1.12)

## ⚠ Concentration des pertes — TA question

| pires trades | n | coût | % des gains bruts effacé | uplift si retirés |
|---|---:|---:|---:|---:|
| 0.5% | 56 | -12.980 | **7%** | ×1.32 |
| 1.0% | 113 | -21.830 | **12%** | ×1.54 |
| 2.0% | 226 | -34.164 | **18%** | ×1.84 |
| 5.0% | 566 | -59.382 | **32%** | ×2.46 |
| 10.0% | 1133 | -86.227 | **46%** | ×3.12 |

**Les 15 pires trades individuels :**

| date | actif | moteur | net (bps) | score |
|---|---|---|---:|---:|
| 2025-10-10 | TIA | PREMIUM_DISLOCATION | -3510 | 0.549 |
| 2022-11-08 | SOL | LIQ_CASCADE | -3069 | 0.564 |
| 2025-10-10 | ICP | PREMIUM_DISLOCATION | -2899 | 0.521 |
| 2025-10-10 | VET | LIQ_CASCADE | -2896 | 0.579 |
| 2025-10-10 | ICP | LIQ_CASCADE | -2800 | 0.579 |
| 2025-10-10 | TIA | LIQ_CASCADE | -2782 | 0.581 |
| 2025-10-10 | ATOM | PREMIUM_DISLOCATION | -2780 | 0.526 |
| 2025-10-10 | AR | PREMIUM_DISLOCATION | -2730 | 0.532 |
| 2025-10-10 | SAND | PREMIUM_DISLOCATION | -2696 | 0.523 |
| 2025-10-10 | MANA | PREMIUM_DISLOCATION | -2644 | 0.522 |
| 2025-10-10 | ORDI | LIQ_CASCADE | -2626 | 0.586 |
| 2025-10-10 | AR | LIQ_CASCADE | -2612 | 0.51 |
| 2025-10-10 | ARB | PREMIUM_DISLOCATION | -2612 | 0.538 |
| 2025-02-02 | WIF | LIQ_CASCADE | -2558 | 0.58 |
| 2022-05-11 | SAND | LIQ_CASCADE | -2535 | 0.567 |

## Par moteur
| moteur | n | net (bps cumul) | espérance | PF | WR |
|---|---:|---:|---:|---:|---:|
| CROWDING_REVERSAL | 290 | 115231 | +397.3 | 3.73 | 0.65 |
| PREMIUM_DISLOCATION | 3885 | 140380 | +36.1 | 1.26 | 0.52 |
| LIQ_CASCADE | 7158 | 151785 | +21.2 | 1.17 | 0.53 |

## Actifs qui DÉTRUISENT (net cumul le plus négatif)
| actif | n | net (bps) | esp | PF |
|---|---:|---:|---:|---:|
| RUNE | 325 | -14579 | -44.9 | 0.72 |
| LDO | 218 | -8943 | -41.0 | 0.77 |
| ARB | 196 | -7575 | -38.6 | 0.81 |
| AVAX | 268 | -6123 | -22.8 | 0.86 |
| TRX | 139 | -3814 | -27.4 | 0.74 |
| OP | 259 | -3065 | -11.8 | 0.92 |
| ATOM | 219 | -2055 | -9.4 | 0.94 |
| BTC | 205 | -1823 | -8.9 | 0.89 |
| SAND | 315 | -1105 | -3.5 | 0.98 |
| LTC | 207 | 875 | +4.2 | 1.04 |
| INJ | 235 | 2181 | +9.3 | 1.06 |
| ETH | 260 | 2361 | +9.1 | 1.09 |

## Actifs qui MARCHENT (net cumul le plus positif)
| actif | n | net (bps) | esp | PF |
|---|---:|---:|---:|---:|
| ORDI | 259 | 25298 | +97.7 | 1.77 |
| FET | 203 | 24749 | +121.9 | 2.14 |
| PYTH | 165 | 22484 | +136.3 | 1.88 |
| AR | 321 | 19706 | +61.4 | 1.44 |
| TIA | 208 | 19032 | +91.5 | 1.59 |
| SUI | 203 | 18592 | +91.6 | 1.84 |
| STX | 188 | 18415 | +98.0 | 1.88 |
| GRT | 281 | 18168 | +64.7 | 1.56 |
| IMX | 300 | 17276 | +57.6 | 1.59 |
| ENA | 160 | 16358 | +102.2 | 1.91 |
| FIL | 351 | 15844 | +45.1 | 1.32 |
| HBAR | 198 | 15552 | +78.5 | 1.78 |

## Par bande de conviction (score du modèle)
| bande | n | net (bps) | esp | PF | WR |
|---|---:|---:|---:|---:|---:|
| Q2 | 2833 | 14127 | +5.0 | 1.04 | 0.52 |
| Q1_bas | 2834 | 107468 | +37.9 | 1.39 | 0.55 |
| Q3 | 2833 | 116714 | +41.2 | 1.3 | 0.52 |
| Q4_haut | 2833 | 169088 | +59.7 | 1.36 | 0.53 |

## Par année
| année | n | net (bps) | esp | PF |
|---|---:|---:|---:|---:|
| 2022 | 2852 | -35223 | -12.4 | 0.91 |
| 2023 | 569 | 36004 | +63.3 | 2.01 |
| 2026 | 1838 | 41271 | +22.5 | 1.23 |
| 2025 | 2207 | 119825 | +54.3 | 1.33 |
| 2024 | 3867 | 245519 | +63.5 | 1.51 |