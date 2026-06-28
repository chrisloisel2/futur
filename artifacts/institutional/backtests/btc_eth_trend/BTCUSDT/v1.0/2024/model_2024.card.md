# Model Card — LogisticBaselineClassifier v1.0

**Asset** : BTCUSDT
**Target** : trend_cont_24h
**Train period** : {}
**N train** : 24060
**N features** : 94

## Hyperparameters
```json
{
  "C": 1.0,
  "multi_class": "ovr"
}
```

## Train metrics
- auc_ovr: 0.6863
- logloss: 0.7608

## Validation metrics
- auc_ovr: 0.7211
- logloss: 0.7257