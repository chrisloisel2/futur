# Model Card — LightGBMClassifier v1.0

**Asset** : BNBUSDT
**Target** : event_cont_4h
**Train period** : {}
**N train** : 15288
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
- auc_ovr: 0.7107
- logloss: 0.4642
- n_estimators_used: 1.0000

## Validation metrics
- auc_ovr: 0.5500
- logloss: 0.3943

## Top 10 features
- rv_zscore_24h: 0.1308
- lsr_zscore: 0.0650
- price_oi_div_24h: 0.0477
- funding_cum_72h: 0.0472
- rv_1d: 0.0417
- rv_pct_24h: 0.0397
- log_ret_72h: 0.0364
- gk_vol_24h: 0.0351
- atr_pct_rank: 0.0316
- log_ret_24h: 0.0302