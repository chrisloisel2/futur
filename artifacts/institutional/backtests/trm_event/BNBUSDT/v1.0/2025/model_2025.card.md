# Model Card — LightGBMClassifier v1.0

**Asset** : BNBUSDT
**Target** : event_cont_4h
**Train period** : {}
**N train** : 32832
**N features** : 67

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
- auc_ovr: 0.7392
- logloss: 0.4322
- n_estimators_used: 11.0000

## Validation metrics
- auc_ovr: 0.5324
- logloss: 0.3970

## Top 10 features
- rv_zscore_24h: 0.0952
- rv_pct_24h: 0.0664
- log_ret_168h: 0.0549
- atr_14h: 0.0360
- rv_zscore_720h: 0.0350
- rv_zscore_120h: 0.0305
- log_ret_72h: 0.0291
- log_ret_48h: 0.0252
- rv_zscore_240h: 0.0247
- rv_zscore_60h: 0.0246