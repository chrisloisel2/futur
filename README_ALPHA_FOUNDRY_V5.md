# Alpha Foundry V5

V5 is a clean-room research-control branch built from `research/market-physics-data-v3`.
It deliberately does **not** inherit Alpha Foundry V4. V4 is treated as a discarded prototype.

The objective is not to accumulate features. The objective is to operate a falsifiable research factory that can discover and admit at least ten economically distinct, execution-valid and portfolio-orthogonal sleeves without hiding the number of experiments tried.

## Core properties

- immutable SHA-256 dataset manifests
- append-only pre-computation search ledger
- immutable experiment specs and holdout lineage
- point-in-time auditing over availability timestamps
- purged/embargoed outer walk-forward
- nested inner model selection
- Benjamini-Hochberg family correction
- block-permutation significance
- Deflated Sharpe Ratio calculation
- CSCV Probability of Backtest Overfitting calculation
- sealed experiment artifacts
- 16 executable fail-closed labs
- conservative maker/taker execution simulator
- economic source deduplication
- PnL correlation gate and effective-number-of-bets diagnostics

## Validation

```bash
python3 -m pytest tests/unit/test_alpha_foundry_v5_core.py tests/unit/test_alpha_foundry_v5_research.py -q
```

## Operator entry points

```bash
python3 scripts/alpha_foundry_v5_readiness.py --help
python3 scripts/alpha_foundry_v5_freeze_dataset.py --help
python3 scripts/alpha_foundry_v5_discover.py --help
```

See `reports/ALPHA_FOUNDRY_V5_PROTOCOL.md` for the full scientific and operational contract.
