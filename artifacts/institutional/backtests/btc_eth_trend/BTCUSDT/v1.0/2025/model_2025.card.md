# Model Card — LightGBMClassifier v1.0

**Asset** : BTCUSDT
**Target** : trend_cont_24h
**Train period** : {}
**N train** : 32844
**N features** : 94

## Hyperparameters
```json
{
  "task": "multiclass",
  "n_estimators": 500,
  "early_stopping_rounds": 50,
  "calibrate": true,
  "objective": "multiclass",
  "num_class": 3,
  "boosting_type": "gbdt",
  "num_leaves": 31,
  "learning_rate": 0.05,
  "feature_fraction": 0.8,
  "bagging_fraction": 0.8,
  "bagging_freq": 5,
  "min_child_samples": 30,
  "lambda_l1": 0.1,
  "lambda_l2": 0.1,
  "verbose": -1,
  "seed": 42
}
```

## Train metrics
- auc_ovr: 0.9303
- logloss: 0.5404
- n_estimators_used: 94.0000

## Validation metrics
- auc_ovr: 0.6785
- logloss: 0.7407

## Top 10 features
- rv_pct_24h: 0.0538
- rv_zscore_720h: 0.0531
- rv_zscore_120h: 0.0506
- rv_720h: 0.0499
- rv_zscore_24h: 0.0477
- vol_of_vol_7d: 0.0430
- rv_pct_720h: 0.0429
- rv_zscore_240h: 0.0410
- rv_zscore_60h: 0.0340
- ma_cross_55_144: 0.0327