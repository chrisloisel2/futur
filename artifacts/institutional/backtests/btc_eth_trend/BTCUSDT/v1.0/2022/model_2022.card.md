# Model Card — LightGBMClassifier v1.0

**Asset** : BTCUSDT
**Target** : trend_cont_24h
**Train period** : {}
**N train** : 6540
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
- auc_ovr: 0.9402
- logloss: 0.5546
- n_estimators_used: 16.0000

## Validation metrics
- auc_ovr: 0.6805
- logloss: 0.7483

## Top 10 features
- rv_zscore_120h: 0.0784
- funding_cum_72h: 0.0555
- rv_720h: 0.0488
- rv_zscore_24h: 0.0482
- rv_zscore_60h: 0.0429
- rv_1d: 0.0414
- vol_of_vol_7d: 0.0414
- rv_zscore_240h: 0.0361
- parkinson_vol_72h: 0.0309
- ma_cross_55_144: 0.0303