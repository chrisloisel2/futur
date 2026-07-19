# VALIDATION_PROTOCOL — INSTITUTIONAL_ENGINE

## Principes fondamentaux

**Un modèle n'est jamais "validé" sur les données qui ont servi à l'entraîner.**
**Un threshold n'est jamais choisi sur le test set.**
**Un scaler n'est jamais fit sur le test set.**

## Pipeline de validation obligatoire

```
1. Build features (feature_store.py)
2. Build labels (label_store.py)
3. Split temporel ← jamais shuffle
4. Scaler fit sur TRAIN uniquement
5. Entraînement + early stopping sur VAL
6. Métriques sur TEST (jamais utilisé pendant fit)
7. Walk-forward (4 folds minimum)
8. Anti-overfit battery
9. Verdict (REJECT / INCUBATE / PAPER / PROMOTE / LIVE_READY)
```

## Walk-Forward

| Fold | Train | Val | Test |
|------|-------|-----|------|
| 2022 | 2021 | Oct-Déc 2021 | 2022 |
| 2023 | 2021-2022 | Oct-Déc 2022 | 2023 |
| 2024 | 2021-2023 | Oct-Déc 2023 | 2024 |
| 2025 | 2021-2024 | Oct-Déc 2024 | 2025 |

Embargo obligatoire : **7 jours** entre train et test.

## Anti-Overfit Battery (obligatoire)

| Test | Description | Attendu |
|------|-------------|---------|
| Shuffle labels | Labels permutés aléatoirement | PF → 1.0 |
| Cost ×2 | Frais doublés | PF > 1.00 |
| Cost ×3 | Frais triplés | PF > 0.90 |
| Best year suppression | Retirer la meilleure année | PF > 1.05 |
| Top 5 trades suppression | Retirer les 5 meilleurs trades | PF > 1.05 |
| Feature ablation | Retirer les top features une par une | IC stable |
| Threshold cliff | PF vs threshold (ne pas avoir de falaise) | Graduel |
| Random seed sensitivity | 5 seeds différents | std < 0.05 |

## Critères de promotion

| Verdict | PF net | PF cost×2 | Worst year | N trades | Sharpe |
|---------|--------|-----------|------------|----------|--------|
| REJECT  | < 1.05 | -         | -          | -        | -      |
| INCUBATE| ≥ 1.10 | > 1.00    | -          | ≥ 30     | -      |
| PAPER   | ≥ 1.20 | > 1.05    | > -10%     | ≥ 50     | ≥ 0.8  |
| PROMOTE | ≥ 1.30 | > 1.10    | > -5%      | ≥ 100    | ≥ 0.8  |
| LIVE    | PROMOTE + paper trading validé + drift OK         |

## Gates Paper Trading (non négociables)

- Durée : ≥ 90 jours
- Trades : ≥ 100 trades portefeuille
- PF paper : > 1.15
- Max DD : < 3%
- Slippage réalisé : conforme aux hypothèses (±30%)
- 0 erreur comptable

## Interdictions absolues

- Threshold sélectionné sur test set → INVALIDATION
- Scaler fit sur val/test → INVALIDATION
- Feature sélectionnée sur test → INVALIDATION
- Calibration sur test → INVALIDATION
- Résultat reporté sans frais et slippage → REJET
