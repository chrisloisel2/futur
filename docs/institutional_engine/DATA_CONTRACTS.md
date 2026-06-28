# DATA_CONTRACTS — INSTITUTIONAL_ENGINE

## Schéma OHLCV 1h (canonique)

| Colonne | Type | Description |
|---------|------|-------------|
| index (timestamp) | DatetimeIndex UTC | Clôture de la barre |
| open | float64 | Prix d'ouverture |
| high | float64 | Plus haut |
| low | float64 | Plus bas |
| close | float64 | Prix de clôture |
| volume | float64 | Volume en base asset |
| quote_vol | float64 | Volume en USD |
| n_trades | int64 | Nombre de trades |
| taker_buy | float64 | Taker buy volume USD |
| asset | str | Symbole (ex: "BTCUSDT") |
| source | str | "futures" / "spot" / "enriched" |

## Schéma SignalFrame (interface engines)

Toutes les colonnes sont **obligatoires**. Aucun NaN toléré.

| Colonne | Type | Contrainte |
|---------|------|-----------|
| timestamp | Timestamp UTC | - |
| asset | str | Non vide |
| engine_name | str | "TRM_EVENT_ENGINE" \| "INSTITUTIONAL_ENGINE" |
| signal_name | str | Non vide |
| direction | str | "long" \| "short" \| "flat" |
| raw_score | float | Non borné |
| calibrated_score | float | [0.0, 1.0] |
| confidence | float | [0.0, 1.0] |
| expected_return | float | Fraction (E[r] sur horizon) |
| expected_vol | float | > 0 (annualisée) |
| horizon_minutes | int | > 0 |
| max_holding_minutes | int | > 0 |
| stop_distance | float | > 0 |
| take_profit_distance | float | > 0 |
| model_version | str | - |
| feature_version | str | - |
| label_version | str | - |
| run_id | str | - |

## Règle d'as-of join

Toute jointure entre séries de fréquences différentes **doit** utiliser
`asof_join()` (backward, jamais forward).

```python
# CORRECT : as-of backward
master = asof_join(ohlcv_1h, funding_8h, max_stale_minutes=10*60)

# INTERDIT : merge direct
master = ohlcv_1h.merge(funding_8h, on="timestamp")  # ← LOOKAHEAD
```

## Règle de no-lookahead features

Chaque feature au timestamp T utilise **uniquement** des données ≤ T :

```python
# CORRECT : rolling backward
ema_8h = close.ewm(span=8, adjust=False).mean()

# INTERDIT : expanding forward
ema_8h = close.shift(-4).ewm(span=8).mean()  # ← LOOKAHEAD

# INTERDIT : scaler global
scaler.fit(X_full)  # ← LOOKAHEAD
# CORRECT :
scaler.fit(X_train_only)
```

## Partitionnement parquet

```
artifacts/institutional/features/{version}/{ASSET}_features.parquet
artifacts/institutional/labels/{version}/{ASSET}_labels.parquet
artifacts/institutional/models/{engine}/{asset}/{version}_lgbm.pkl
artifacts/institutional/backtests/{portfolio}/{version}/
```

## Versioning

| Artifact | Version | Hash |
|---------|---------|------|
| Features | config JSON SHA256[:12] | - |
| Labels | barrier params SHA256[:12] | - |
| Models | train period + params | - |
| Expériences | auto-incrémental + timestamp | - |
