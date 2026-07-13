# Maturity Backtest Suite Report

- fenêtre : 2022-11-03 → 2026-07-05
- **SCORE : 95.0/100 → TIER : STRONG_PAPER**
- **DÉCISION : PAPER_CONTINUE / MICRO_LIVE_BLOCKED_UNTIL_60_90D_REAL_TRACKING**

## Checks

| Check | Statut | score | poids |
|---|---|---:|---:|
| reproducibility | PASS | 1.00 | 10 |
| data_integrity | PASS | 1.00 | 15 |
| leakage_audit | PASS | 1.00 | 15 |
| baseline | PASS | 1.00 | 10 |
| regime_splits | PASS | 1.00 | 10 |
| cost_stress | PASS | 1.00 | 10 |
| sleeve_ablation | PASS | 1.00 | 10 |
| paper_replay | WARN | 0.50 | 10 |
| event_readiness | INFO | 1.00 | 5 |
| operational_stress | PASS | 1.00 | 5 |
| sizing_sensitivity | PASS | 1.00 | 0 |
| walk_forward | PASS | 1.00 | 0 |
| monte_carlo | PASS | 1.00 | 0 |

## Détails
```json
{
 "reproducibility": {
  "status": "PASS",
  "score_frac": 1.0,
  "ledger_hash": "e501c363a71e",
  "detail": "2 runs identiques",
  "weight": 10
 },
 "data_integrity": {
  "status": "PASS",
  "score_frac": 1.0,
  "files": 50,
  "fail": 0,
  "weight": 15
 },
 "leakage_audit": {
  "status": "PASS",
  "score_frac": 1.0,
  "regime_causal": true,
  "weight": 15
 },
 "baseline": {
  "status": "PASS",
  "score_frac": 1.0,
  "roi": 0.3374,
  "expected": 0.336,
  "maxDD": -0.0228,
  "pf": 1.032,
  "weight": 10
 },
 "regime_splits": {
  "status": "PASS",
  "score_frac": 1.0,
  "by_year": {
   "2022": {
    "roi": -0.0059,
    "maxDD": -0.0076
   },
   "2023": {
    "roi": 0.061,
    "maxDD": -0.0111
   },
   "2024": {
    "roi": 0.1896,
    "maxDD": -0.0193
   },
   "2025": {
    "roi": 0.0472,
    "maxDD": -0.0228
   },
   "2026": {
    "roi": 0.017,
    "maxDD": -0.0179
   }
  },
  "worst_annual_dd": -0.0228,
  "weight": 10
 },
 "cost_stress": {
  "status": "PASS",
  "score_frac": 1.0,
  "runs": {
   "x1.0": {
    "roi": 0.3374,
    "maxDD": -0.0228
   },
   "x2.0": {
    "roi": 0.1465,
    "maxDD": -0.0329
   },
   "x3.0": {
    "roi": -0.0046,
    "maxDD": -0.0562
   }
  },
  "fees_x2_roi": 0.1465,
  "weight": 10
 },
 "sleeve_ablation": {
  "status": "PASS",
  "score_frac": 1.0,
  "runs": {
   "CASH": {
    "roi": 0.0,
    "maxDD": 0.0
   },
   "CARRY_ONLY": {
    "roi": 0.2644,
    "maxDD": -0.0076
   },
   "LONG_ONLY": {
    "roi": 0.0638,
    "maxDD": -0.0554
   },
   "FULL_V1.1": {
    "roi": 0.3374,
    "maxDD": -0.0228
   }
  },
  "carry_is_engine": true,
  "weight": 10
 },
 "paper_replay": {
  "status": "WARN",
  "score_frac": 0.5,
  "forward_days_real": 0,
  "verdict": "INSUFFICIENT_FORWARD_DATA (d\u00e9terministe = backtest ; besoin 30-60-90j r\u00e9els)",
  "note": "tracking 0 par construction \u2014 non validable en L4 tant que pas d'ex\u00e9cution r\u00e9elle",
  "weight": 10
 },
 "event_readiness": {
  "status": "INFO",
  "score_frac": 1.0,
  "n_events": 1166,
  "n_significant": 41,
  "verdict": "ML_READY",
  "weight": 5
 },
 "operational_stress": {
  "status": "PASS",
  "score_frac": 1.0,
  "checks": {
   "missing_funding_blocks": true,
   "corrupt_refused": true
  },
  "weight": 5
 },
 "sizing_sensitivity": {
  "status": "PASS",
  "score_frac": 1.0,
  "runs": {
   "carry20": {
    "roi": 0.1235,
    "maxDD": -0.0383
   },
   "carry35": {
    "roi": 0.178,
    "maxDD": -0.0282
   },
   "carry50": {
    "roi": 0.2358,
    "maxDD": -0.0243
   },
   "carry75": {
    "roi": 0.3374,
    "maxDD": -0.0228
   }
  },
  "weight": 0
 },
 "walk_forward": {
  "status": "PASS",
  "score_frac": 1.0,
  "n_folds": 8,
  "catastrophic_folds": 0,
  "median_fold_roi": 0.0192,
  "weight": 0
 },
 "monte_carlo": {
  "status": "PASS",
  "score_frac": 1.0,
  "P_roi_neg": 0.0,
  "P_dd_gt_3pct": 0.002,
  "method": "monthly_equity_block_bootstrap",
  "weight": 0
 }
}
```