# Distribution check — AVAXUSDT_1h_enriched.parquet

- candidat : 4139 cols × 50,499 rows
- référence(s) : ['ADAUSDT_1h_enriched.parquet'] (4060 cols union)
- colonnes manquantes vs réf : 15 (dont **non-MTF : 2**)
- colonnes en trop : 94
- features plates (absolu) : 238 — dont **plates SEULEMENT côté candidat : 1**
- features >50% NaN : 0
- features avec inf : 0
- features NaN nettement pire que réf (+30pts) : 0

## Échantillons

- manquantes non-MTF (≤30) : ['extreme_fear', 'fred_fedfunds']
- plates côté candidat seulement (≤30) : ['normalized_price_mean_1']
- inf (≤30) : []
- nan_worse (≤30) : []

## Verdict : **PASS**
(MTF lag-variants manquantes = delta documenté, non utilisé par les moteurs alpha)