# Model Card — LogisticBaselineClassifier v1.0

**Asset** : BTCUSDT
**Target** : trend_cont_24h
**Train period** : {}
**N train** : 15300
**N features** : 94

## Hyperparameters
```json
{
  "C": 1.0,
  "multi_class": "ovr"
}
```

## Train metrics
- auc_ovr: 0.7179
- logloss: 0.7474

## Validation metrics
- auc_ovr: 0.6045
- logloss: 0.8256