# Alpha Discovery V3.2 — Pre-registered protocol

## Purpose

V3.2 is a new research protocol after V3.1 showed a weak but non-zero ranking signal on BTC/ETH/SOL and systematic over-estimation of economic edge after costs. V3.2 does not retune V1/V3/V3.1 thresholds from their test outputs. It changes the model decomposition and validation protocol.

## Development vs holdout

- DEV symbols are fixed to BTCUSDT, ETHUSDT, SOLUSDT.
- V3.2 may be developed and inspected only on those three symbols.
- HOLDOUT is every other symbol present in the canonical Data V2 event panel.
- HOLDOUT execution is forbidden unless DEV produces a frozen `CANDIDATE` under the gate below.
- HOLDOUT mode forbids manual `--symbols` selection and refuses a second economic output file.
- The holdout remains historical research rather than pristine future evidence because the broader 2020-2026 corpus has been used by prior project research. True confirmation remains forward/paper-live after historical qualification.

## Sampling and features

Feature engineering and the common candidate population are frozen from V3.1:

- fixed 4-hour background observations;
- first crossing of the core stress score >= 2.0;
- same A_CORE, B_NORMALIZED and C_STATE feature definitions;
- same causal 7-day ex-ante volatility;
- same next-bar 1-hour residual-return target;
- same main-leg plus absolute-beta BTC/ETH hedge cost accounting.

No B/C feature is allowed to change the candidate population.

## Walk-forward

Evaluation is monthly from 2023-01 through 2026-07.

For every test month:

1. Fit window: immediately preceding 24 months ending before calibration.
2. Calibration window: immediately preceding 120 days.
3. Calibration is split chronologically 50/50:
   - first half calibrates probabilities and magnitudes;
   - second half freezes the economic selection threshold and decides whether trading is enabled for the next month.
4. Embargo: final 8 hours before the test month are excluded from calibration.
5. Test: exactly one calendar month.

The test-month prediction distribution never sets its own threshold.

## Direction / magnitude decomposition

The model is fixed before DEV execution:

- direction: `HistGradientBoostingClassifier` predicts P(return > 0);
- positive magnitude: separate `HistGradientBoostingRegressor` trained only on positive targets;
- negative magnitude: separate regressor trained only on negative targets;
- magnitude targets are standardized by strict-prior ex-ante volatility.

Direction calibration uses logistic calibration on the first calibration half.
Magnitude calibration uses separate monotonic isotonic calibrators for positive and negative magnitudes.

Expected signed return is:

`p_up * calibrated_up_magnitude - (1 - p_up) * calibrated_down_magnitude`

## Economic selector

For the second calibration half:

`expected_net_edge = abs(expected_signed_return) - decision_time_cost_x1`

The threshold is the pre-registered 90th percentile of strictly positive expected net edges.

The following monthly gate is mandatory before the next month can trade:

- at least 40 selected calibration observations;
- realized mean net x1 > 0 on calibration selection;
- calibration PF x1 > 1.0.

If any condition fails, the entire following test month is disabled. No threshold rescue is allowed.

## DEV candidate gate before HOLDOUT

A feature group becomes a DEV `CANDIDATE` only if all conditions hold:

- >= 30 valid monthly folds;
- >= 12 calibration-enabled months;
- >= 500 selected DEV trades;
- pooled net x1 > 0;
- pooled net x2 > 0;
- median PF x2 >= 1.15;
- >= 60% of selected months positive net x2;
- median Spearman IC > 0;
- median direction-calibration Brier improvement >= 0;
- median largest DEV-symbol share <= 80%.

If multiple groups pass, the one with highest pooled net x2 is frozen. If none pass, status is `NO_CANDIDATE` and historical HOLDOUT is not authorized.

## Freeze integrity

`--mode freeze` records:

- selected group;
- DEV result SHA256;
- pipeline SHA256;
- runner SHA256;
- current git SHA;
- full protocol payload and gate reasons.

HOLDOUT refuses to run if the pipeline or runner hash differs from the DEV freeze.
