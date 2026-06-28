# Enriched pipeline discovery (Phase 23.3-23.4)

Objectif : localiser le VRAI pipeline enriched et les sources raw, sans rien inventer.

## Pipelines candidats

| Script | Source | Réseau | MTF | Rôle |
|---|---|---|---|---|
| `scripts/bootstrap_enriched.py` | Binance API (fetch_binance_1h) | **oui** | oui (`_add_mtf_features`) | création from-zero (originel, online) |
| `scripts/assemble_enriched_from_dataout.py` | `data_out/result/{year}_{SYM}_features.parquet` (1m) | non | **non** (`include_multi_timeframe=False`) | assemblage offline |
| `data_pipeline/enriched_ohlcv_features.py` | — | non | param | **module de features partagé** (les deux l'appellent) |

Les deux pipelines appellent `compute_enriched_ohlcv_features` → même famille de features.
Sortie : `data/enriched/{SYM}_1h_enriched.parquet` (col temporelle `datetime`, 1h UTC).

## Schéma de référence (fichiers valides)

| Fichier | colonnes | mtf_* | statut |
|---|---:|---|---|
| BTCUSDT | 4050 | oui | OK (gap pré-2020 only) |
| ADAUSDT | 4060 | oui | OK (référence ALT) |
| DOGEUSDT / XRPUSDT | 4050 | oui | OK |

→ **Les fichiers valides contiennent les features MTF.** Donc `assemble_enriched_from_dataout.py`
tel quel (MTF=False) ne reproduit PAS le schéma canonique : il faut activer
`include_multi_timeframe=True` + `include_sequence_features=True`. C'est un **wrapper minimal**
(`scripts/rebuild_enriched_from_origin.py`) qui réutilise le chargement offline de l'assembleur
mais avec les flags canoniques — aucune feature inventée.

Référence schéma pour un ALT reconstruit = **ADAUSDT** (alt valide), pas BTC (légères diffs macro).

## Sources raw par actif corrompu (`data_out/result/`)

| Asset | enriched actuel | raw data_out/result | rebuild offline |
|---|---|---|---|
| AVAXUSDT | corrompu | 7 fichiers (2020-2026) ✓ | **oui** |
| BNBUSDT | corrompu | 7 fichiers (2020-2026) ✓ | **oui** |
| LINKUSDT | corrompu | 7 fichiers (2020-2026) ✓ | **oui** |
| **DOTUSDT** | corrompu | **0 fichier (absent partout)** | **NON → retiré de l'univers** |

Raw 1m : ~527k lignes/an, OHLCV + macro/funding (272 cols) → resample 1h → features.

## Décision

1. Rebuild **BNB, AVAX, LINK** offline via wrapper (MTF=True), référence schéma = ADA.
2. **DOT retiré de l'univers** (aucune source raw — pas de téléchargement improvisé, pas de synthétique).
3. Validation stricte + distribution check vs ADA + data registry hashé avant tout re-run modèle.
