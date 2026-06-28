# Distribution check — BNBUSDT_1h_enriched.parquet

- candidat : 4141 cols × 54,977 rows
- référence(s) : ['ADAUSDT_1h_enriched.parquet'] (4060 cols union)
- colonnes manquantes vs réf : 13 (dont **non-MTF : 0**)
- colonnes en trop : 94
- features plates (absolu) : 239 — dont **plates SEULEMENT côté candidat : 4**
- features >50% NaN : 0
- features avec inf : 0
- features NaN nettement pire que réf (+30pts) : 0

## Échantillons

- manquantes non-MTF (≤30) : []
- plates côté candidat seulement (≤30) : ['feature_count', 'liquidity_shock_proxy_10', 'liquidity_shock_proxy_100', 'normalized_price_mean_1']
- inf (≤30) : []
- nan_worse (≤30) : []

## Verdict : **PASS**
(MTF lag-variants manquantes = delta documenté, non utilisé par les moteurs alpha)