# MEXC_VOLUME_TRUST_V1 — covariables de wash sur le volume MEXC d'avant Binance

Généré 2026-09-12T17:35:53+00:00 · code `1b4180b5a834` · spec sha256 `fda22b452d81` · descriptif, non validé, sans verdict, sans alpha, sans budget.

## Support

| statut | n |
|---|---|
| MEASURED | 63 |
| NOT_COMPUTABLE | 42 |
| NO_TAPE | 8 |

Raisons de non‑mesure : n_hours 0 < 36 ×17; no hourly tape stored ×8; BAD_TIMESTAMP: excluded by rule (manual proof needed) ×6; n_hours 4 < 36 ×3; n_hours 3 < 36 ×2; n_hours 11 < 36 ×1; n_hours 16 < 36 ×1; n_hours 2 < 36 ×1; n_hours 18 < 36 ×1; n_hours 7 < 36 ×1; n_hours 6 < 36 ×1; n_hours 21 < 36 ×1; n_hours 31 < 36 ×1; n_hours 20 < 36 ×1; n_hours 5 < 36 ×1; n_hours 27 < 36 ×1; n_hours 35 < 36 ×1; n_hours 23 < 36 ×1; n_hours 28 < 36 ×1

## Drapeaux (fraction pré‑déclarée, jamais un filtre)

- `wash_volume_suspect` (décile supérieur du rang d'anomalie, sur 63 MEASURED, soit ⌈63 × 0.10⌉ = 7 rangs, ex æquo au seuil inclus) : **8**
- `programme_like` (plancher ≥ 0.8 et CV ≤ 0.4) : **6**

## Médianes des événements MEASURED

| covariable | médiane |
|---|---|
| volume_floor | 0.4601 |
| volume_cv | 0.9341 |
| volume_range_coupling | 0.6035 |
| log10_volume_per_range | 5.8389 |
| anticipation_ratio | 1.7347 |
| log10_venue_volume_multiple | -0.3575 |
| coupling_gap | -0.0361 |

## Contrôle même actif, second venue, mêmes heures

- événements avec un multiple mesuré : **55** (bybit perp 6, bybit spot 4, gate spot 4, kucoin spot 35, okx perp 2, okx spot 4)
- `log10_venue_volume_multiple` médian : -0.3575 (0 = même volume horaire médian que l'autre place ; 1 = 10×)
- `coupling_gap` médian : -0.0361 (négatif = le volume MEXC est moins couplé à l'amplitude que sur l'autre place)

## Différence de place (F4, MEXC − 24 contrôles autre‑place‑première)

- n : MEXC 63, contrôles 14 (MEASURED 14, NOT_COMPUTABLE 10)
- diff log10 : -0.9678, SE 0.2484, IC 95 % [-1.4546, -0.4809], demi‑largeur ≈ ×3.07
- étiquette : venue+market+fee+era difference; not a per-event control; selection on Binance listing applies to both groups

## Événements MEASURED, du plus anormal au moins anormal

| asset | marché | h | F1 plancher | F2 CV | F3 couplage | F4 log10 vol/amp | rang | suspect | programme | anticipation | 2ᵉ place | log10 multiple | écart couplage |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ZKJ | spot | 69 | 0.7801 | 0.7801 | 0.3501 | 5.9764 | 0.7698 | True | False | 1.6530 | okx spot | -0.4259 | -0.2890 |
| PONS | spot | 70 | 0.5951 | 0.5403 | 0.5895 | 6.9199 | 0.7659 | True | False | 1.8441 | kucoin spot | 0.2449 | 0.1131 |
| GUA | perp | 69 | 0.9648 | 0.1983 | 0.3340 | 5.2603 | 0.7579 | True | True | 1.1650 | gate spot | -0.4915 | -0.2135 |
| OPENAI | perp | 70 | 0.9238 | 0.2934 | 0.5145 | 5.7190 | 0.7579 | True | True | 1.0038 | okx perp | -0.6309 | -0.3323 |
| BSB | perp | 70 | 0.9585 | 0.0320 | 0.2574 | 5.1543 | 0.7540 | True | True | 0.9893 | bybit spot | -0.6920 | -0.3741 |
| BILL | spot | 46 | 0.4955 | 0.9627 | -0.2709 | 6.9231 | 0.7381 | True | False |  | kucoin spot | 0.1891 | -0.7683 |
| MYRO | spot | 68 | 0.5118 | 0.5184 | 0.6035 | 6.6207 | 0.7183 | True | False | 1.4185 | kucoin spot | -0.3501 | 0.0147 |
| ANTHROPIC | perp | 70 | 0.8563 | 0.3643 | 0.5793 | 5.8270 | 0.7183 | True | True | 1.3258 | okx perp | -0.6065 | -0.1717 |
| PUFFER | perp | 48 | 0.5560 | 0.6554 | 0.4982 | 5.9291 | 0.7063 | False | False | 2.2090 | kucoin spot | 0.6005 | -0.1615 |
| GOAT | spot | 69 | 0.4422 | 0.7215 | 0.4475 | 6.3944 | 0.6984 | False | False | 2.4112 | — |  |  |
| MYX | perp | 68 | 0.7482 | 0.7834 | 0.4417 | 5.4881 | 0.6548 | False | False | 2.6429 | — |  |  |
| OL | perp | 69 | 0.5159 | 0.9307 | 0.4931 | 5.9757 | 0.6409 | False | False | 2.1418 | okx spot | -0.3654 | -0.1216 |
| MEGA | perp | 69 | 0.9178 | 0.2855 | 0.6946 | 5.4508 | 0.6111 | False | True | 1.0164 | bybit perp | -0.0754 | 0.0485 |
| B3 | perp | 68 | 0.3633 | 0.8164 | 0.5433 | 7.0814 | 0.6071 | False | False | 1.7008 | bybit spot | -0.2268 | 0.1013 |
| COLLECT | spot | 69 | 0.7451 | 0.4751 | 0.5270 | 4.8745 | 0.6032 | False | False | 0.7276 | — |  |  |
| PRL | perp | 70 | 0.5019 | 1.3727 | 0.4414 | 6.1192 | 0.6032 | False | False | 1.9316 | kucoin spot | -0.4407 | 0.4307 |
| GIGGLE | spot | 70 | 0.4881 | 0.7759 | 0.6491 | 6.1872 | 0.5992 | False | False | 1.5745 | — |  |  |
| B2 | perp | 67 | 0.2956 | 1.0137 | 0.0823 | 6.7429 | 0.5952 | False | False | 1.8826 | kucoin spot | -0.8802 | -0.2994 |
| AKE | perp | 68 | 0.8915 | 0.2930 | 0.6777 | 5.1584 | 0.5873 | False | True | 1.2723 | kucoin spot | -0.0779 | 0.1928 |
| 1000PEPE | spot | 67 | 0.4405 | 0.7695 | 0.7206 | 7.2328 | 0.5833 | False | False | 2.8662 | okx spot | -0.5509 | 0.0504 |
| BAN | spot | 69 | 0.5043 | 0.6858 | 0.8526 | 6.5963 | 0.5714 | False | False | 1.5980 | — |  |  |
| AT | perp | 69 | 0.7468 | 0.5766 | 0.6595 | 5.3341 | 0.5714 | False | False | 1.4772 | gate spot | -0.9803 | 0.5869 |
| MERL | spot | 49 | 0.4209 | 0.9476 | 0.5397 | 6.3454 | 0.5675 | False | False | 1.4222 | kucoin spot | 1.3164 | -0.1082 |
| AGT | perp | 69 | 0.3035 | 0.9677 | 0.5048 | 6.9914 | 0.5595 | False | False | 1.1576 | — |  |  |
| WIF | spot | 64 | 0.4344 | 0.7110 | 0.6922 | 6.1453 | 0.5556 | False | False | 1.5260 | bybit perp | -1.3686 | -0.0085 |
| BULLA | spot | 70 | 0.7259 | 0.5342 | 0.6884 | 5.4480 | 0.5556 | False | False | 1.1328 | — |  |  |
| PONKE | spot | 69 | 0.4922 | 0.8068 | 0.6646 | 5.9757 | 0.5496 | False | False | 1.1483 | kucoin spot | -0.1074 | -0.0437 |
| DEGEN | spot | 69 | 0.4491 | 0.7264 | 0.6030 | 5.8389 | 0.5476 | False | False | 2.0177 | kucoin spot | -0.9840 | -0.0400 |
| SLX | spot | 70 | 0.5148 | 1.7384 | 0.5534 | 6.0154 | 0.5278 | False | False | 1.3197 | gate spot | -0.2059 | 0.0133 |
| MOODENG | spot | 70 | 0.4601 | 0.8123 | 0.7164 | 6.0802 | 0.5119 | False | False | 1.5761 | kucoin spot | -0.3575 | -0.0604 |
| CLANKER | spot | 70 | 0.5107 | 1.6554 | 0.3665 | 5.5368 | 0.5079 | False | False | 1.7347 | kucoin spot | -0.2319 | 0.0287 |
| PHAROS | perp | 70 | 0.3899 | 1.0668 | 0.4653 | 5.8916 | 0.4921 | False | False | 1.3737 | — |  |  |
| 1000000MOG | spot | 69 | 0.4457 | 1.8072 | 0.4975 | 5.9289 | 0.4841 | False | False | 4.1364 | kucoin spot | -0.3243 | -0.1083 |
| ZEST | perp | 70 | 0.7412 | 0.3718 | 0.8695 | 5.2985 | 0.4841 | False | False | 1.0099 | kucoin spot | 0.0133 | 0.3083 |
| 1000CAT | spot | 69 | 0.3964 | 1.0237 | 0.4861 | 5.8028 | 0.4802 | False | False | 1.7916 | kucoin spot | -0.8647 | -0.0410 |
| XPIN | spot | 70 | 0.1802 | 0.9349 | 0.3318 | 5.6196 | 0.4722 | False | False | 0.1598 | kucoin spot | -0.4755 | 0.0704 |
| ARIA | perp | 70 | 0.7490 | 0.9660 | 0.7752 | 5.5975 | 0.4683 | False | False | 0.6923 | kucoin spot | -1.1415 | 0.2848 |
| TAIKO | spot | 69 | 0.0000 | 0.7179 | 0.5325 | 5.7020 | 0.4583 | False | False | 9.9155 | kucoin spot | 0.0042 | -0.3609 |
| ETHW | spot | 65 | 0.3221 | 0.8816 | 0.6188 | 5.9434 | 0.4563 | False | False | 1.9535 | kucoin spot | 0.2845 | -0.2051 |
| ZORA | perp | 70 | 0.2155 | 0.9956 | 0.6774 | 6.8777 | 0.4484 | False | False | 0.4083 | kucoin spot | 0.5844 | -0.1720 |
| USELESS | spot | 69 | 0.4258 | 1.3610 | 0.6545 | 6.0725 | 0.4484 | False | False | 2.3312 | kucoin spot | -0.2867 | 0.0475 |
| FLOCK | perp | 70 | 0.5829 | 4.2292 | 0.5761 | 5.6907 | 0.4484 | False | False | 33.9908 | kucoin spot | 0.0761 | -0.3410 |
| BAS | spot | 70 | 0.5525 | 0.6156 | 0.7628 | 5.0344 | 0.4444 | False | False | 1.1732 | kucoin spot | -1.4192 | 0.7088 |
| GPS | perp | 70 | 0.3571 | 1.8513 | 0.3613 | 5.8799 | 0.4405 | False | False | 0.3014 | bybit spot | -1.2393 | -0.3988 |
| TRIA | perp | 55 | 0.3660 | 1.7663 | 0.3487 | 5.5869 | 0.4365 | False | False | 7.7609 | kucoin spot | -0.8717 | 0.2807 |
| TOSHI | spot | 69 | 0.5264 | 2.5384 | 0.5928 | 5.6337 | 0.4325 | False | False | 5.5969 | kucoin spot | 0.1229 | -0.0361 |
| MARSCOIN | spot | 70 | 0.3999 | 0.7259 | 0.6464 | 5.2790 | 0.4167 | False | False | 1.5500 | gate spot | 0.5594 | 0.0553 |
| KAS | perp | 59 | 0.2154 | 0.9057 | 0.8830 | 8.0053 | 0.4127 | False | False | 2.6995 | kucoin spot | 0.7999 | 0.0320 |
| MORPHO | perp | 69 | 0.3514 | 1.1517 | 0.6919 | 6.3228 | 0.4127 | False | False | 4.8925 | kucoin spot | -0.7517 | -0.0840 |
| BSV | spot | 62 | 0.3306 | 1.1006 | 0.3609 | 5.2911 | 0.3929 | False | False | 2.1876 | bybit perp | -2.2976 | -0.4753 |
| DEEP | perp | 69 | 0.4265 | 4.3336 | 0.7091 | 6.5145 | 0.3849 | False | False | 31.3021 | kucoin spot | 0.5656 | 0.1074 |
| BMT | perp | 69 | 0.3605 | 1.6251 | 0.7904 | 6.2563 | 0.3492 | False | False | 3.7435 | bybit perp | -0.8200 | -0.0728 |
| MOCA | spot | 69 | 0.5559 | 2.9506 | 0.6959 | 5.4729 | 0.3333 | False | False | 68.1572 | kucoin spot | 0.1020 | -0.1018 |
| LAB | spot | 48 | 0.2964 | 0.9341 | 0.8262 | 5.9216 | 0.3214 | False | False | 1.8343 | kucoin spot | -0.1852 | 0.1227 |
| ARC | spot | 69 | 0.2465 | 1.0216 | 0.5151 | 5.1055 | 0.3095 | False | False | 1.2095 | bybit perp | -2.6647 | -0.2244 |
| CROSS | perp | 68 | 0.4713 | 1.3815 | 0.8093 | 5.5271 | 0.3095 | False | False | 7.2233 | kucoin spot | -0.3796 | 0.1555 |
| VELVET | perp | 68 | 0.6904 | 1.9073 | 0.8326 | 5.4557 | 0.3095 | False | False | 5.4504 | kucoin spot | 0.2299 | 0.1824 |
| UNITREE | perp | 71 | 0.3618 | 2.6505 | 0.8213 | 6.2823 | 0.3056 | False | False | 6.3593 | bybit perp | 0.2954 | 0.0769 |
| JELLYJELLY | spot | 71 | 0.4739 | 2.9130 | 0.7634 | 5.4852 | 0.2738 | False | False | 6.6336 | kucoin spot | -0.1437 | -0.0898 |
| AIO | spot | 69 | 0.1439 | 0.9135 | 0.7473 | 4.4263 | 0.2103 | False | False | 0.4823 | kucoin spot | -1.5969 | 0.5282 |
| ELSA | perp | 46 | 0.4202 | 2.1651 | 0.8109 | 5.1741 | 0.1786 | False | False |  | bybit spot | -1.0954 | 0.1183 |
| XCN | spot | 69 | 0.0274 | 1.4560 | 0.8997 | 5.4753 | 0.1429 | False | False | 27.2279 | kucoin spot | -1.0678 | 0.0158 |
| FLUID | spot | 70 | 0.0000 | 4.9723 | 0.8069 | 4.2343 | 0.0456 | False | False | 17.2664 | okx spot | -0.8549 | -0.1919 |

## Événements non mesurés

| asset | statut | raison | h utilisées |
|---|---|---|---|
| ARK | NOT_COMPUTABLE | BAD_TIMESTAMP: excluded by rule (manual proof needed) | 0 |
| TOKEN | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| BOME | NOT_COMPUTABLE | n_hours 11 < 36 | 11 |
| SWELL | NOT_COMPUTABLE | BAD_TIMESTAMP: excluded by rule (manual proof needed) | 0 |
| SPX | NOT_COMPUTABLE | BAD_TIMESTAMP: excluded by rule (manual proof needed) | 0 |
| KMNO | NOT_COMPUTABLE | BAD_TIMESTAMP: excluded by rule (manual proof needed) | 0 |
| SWARMS | NOT_COMPUTABLE | BAD_TIMESTAMP: excluded by rule (manual proof needed) | 0 |
| SONIC | NOT_COMPUTABLE | n_hours 16 < 36 | 16 |
| TRUMP | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| MELANIA | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| VINE | NOT_COMPUTABLE | n_hours 2 < 36 | 2 |
| VVV | NOT_COMPUTABLE | n_hours 3 < 36 | 3 |
| BR | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| WAL | NOT_COMPUTABLE | n_hours 3 < 36 | 3 |
| BABY | NO_TAPE | no hourly tape stored | 0 |
| PROMPT | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| BANK | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| DOLO | NOT_COMPUTABLE | BAD_TIMESTAMP: excluded by rule (manual proof needed) | 0 |
| SXT | NO_TAPE | no hourly tape stored | 0 |
| SOON | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| LA | NOT_COMPUTABLE | n_hours 18 < 36 | 18 |
| NEWT | NO_TAPE | no hourly tape stored | 0 |
| XPL | NO_TAPE | no hourly tape stored | 0 |
| ASTER | NOT_COMPUTABLE | n_hours 7 < 36 | 7 |
| BOB | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| IRYS | NOT_COMPUTABLE | n_hours 6 < 36 | 6 |
| RLS | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| POWER | NOT_COMPUTABLE | n_hours 21 < 36 | 21 |
| NIGHT | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| US | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| RAVE | NOT_COMPUTABLE | n_hours 31 < 36 | 31 |
| LIT | NOT_COMPUTABLE | n_hours 20 < 36 | 20 |
| SPORTFUN | NOT_COMPUTABLE | n_hours 5 < 36 | 5 |
| AIA | NO_TAPE | no hourly tape stored | 0 |
| ACU | NOT_COMPUTABLE | n_hours 4 < 36 | 4 |
| SPACE | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| BIRB | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| KAT | NO_TAPE | no hourly tape stored | 0 |
| COPPER | NOT_COMPUTABLE | n_hours 27 < 36 | 27 |
| EDGE | NO_TAPE | no hourly tape stored | 0 |
| BASED | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| GENIUS | NOT_COMPUTABLE | n_hours 35 < 36 | 35 |
| CHIP | NO_TAPE | no hourly tape stored | 0 |
| OPG | NOT_COMPUTABLE | n_hours 23 < 36 | 23 |
| AIGENSYN | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| CTR | NOT_COMPUTABLE | n_hours 28 < 36 | 28 |
| ARX | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| CAP | NOT_COMPUTABLE | n_hours 4 < 36 | 4 |
| GRVT | NOT_COMPUTABLE | n_hours 0 < 36 | 0 |
| DOS | NOT_COMPUTABLE | n_hours 4 < 36 | 4 |

## Limites déclarées

- detects constant-rate volume programmes only (floor high, CV low, coupling weak); blind to bots that mimic organic flow
- no labelled wash event exists on any venue: sensitivity, specificity and false-positive rate are UNKNOWN
- event-specific inflation below ~10x is undetectable from candles (cross-sectional SD of log10 hourly volume ~0.8)
- volume-per-range is confounded with genuine depth and token size: high = deep OR washed
- the 24 other-venue-first events are a venue+market+fee+era difference, never a per-event control
- the mechanical thresholds (floor 0.8, CV 0.4) were suggested by a reviewer after reading 4 MEXC tapes; the top-decile rule is parameter-free

Politique des features : `FEATURE_POLICY.md`. Spec : `reports/prereg/MEXC_VOLUME_TRUST_V1_SPEC.md`.
