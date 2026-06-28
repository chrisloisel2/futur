# Model Card — LightGBMClassifier v1.0

**Asset** : ETHUSDT
**Target** : trend_cont_24h
**Train period** : {}
**N train** : 15300
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
- auc_ovr: 0.7162
- logloss: 0.8572
- n_estimators_used: 1.0000

## Validation metrics
- auc_ovr: 0.5961
- logloss: 0.8140

## Top 10 features
- rv_1d: 0.0795
- vol_of_vol_7d: 0.0780
- rv_24h: 0.0658
- rv_720h: 0.0624
- atr_14h: 0.0480
- ma_cross_55_144: 0.0448
- rv_zscore_240h: 0.0432
- rv_zscore_60h: 0.0427
- rv_120h: 0.0393
- ewma_vol_168h: 0.0364