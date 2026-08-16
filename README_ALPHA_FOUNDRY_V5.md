# Alpha Foundry V5

V5 is a clean-room research-control branch built from `research/market-physics-data-v3`.
It deliberately does **not** inherit Alpha Foundry V4. V4 is treated as a discarded prototype.

The objective is not to accumulate features. The objective is to operate a falsifiable research factory that can discover and admit at least ten economically distinct, execution-valid and portfolio-orthogonal sleeves without hiding the number of experiments tried.

## Core properties

- immutable SHA-256 dataset manifests
- append-only tamper-evident pre-computation search ledger
- persistent family multiplicity ledger
- immutable experiment specs and holdout lineage
- point-in-time auditing over availability timestamps
- purged/embargoed outer walk-forward on unique timestamps
- nested inner model selection
- persistent Benjamini-Hochberg family correction
- block-permutation significance
- Deflated Sharpe Ratio calculation
- CSCV Probability of Backtest Overfitting calculation
- sealed experiment artifacts
- 16 executable fail-closed labs
- conservative maker/taker execution simulator
- economic source deduplication
- PnL correlation gate and effective-number-of-bets diagnostics

## Causal data planes

The old Market Physics DEV tape can be enriched without touching an independent forward window:

```bash
python3 scripts/build_alpha_foundry_v5_data_planes.py \
  --base-tape <market-physics-state-tape> \
  --raw-root <market-physics-root> \
  --out-root <alpha-foundry-plane-root>
```

Implemented planes:

- **event microstructure** — deep-book depletion/replenishment plus signed trade flow, CVD, impact, absorption and arrival intensity; repeated deep snapshots replace prior level state atomically;
- **derivatives** — venue-specific OI, funding, mark, index, premium and liquidation windows with explicit availability clocks;
- **Hyperliquid wallet intelligence** — aggressor-wallet markout scores mature causally; a trade cannot improve its own score;
- **cross asset** — lagged market beta, residual return and leader innovation while preserving base-tape row order.

Every plane emits explicit `*_available_ts_ns` audit metadata. Availability timestamps are forbidden from model feature matrices.

Readiness is fail-closed:

- non-null columns alone do not unlock event labs; minimum observed activity is required;
- zero-filled liquidation/remove/flow columns do not count as evidence;
- A9 requires an explicit executable `perp_spot_basis_bps`, not mark-index premium;
- A10 remains blocked until a canonical funding clock exists;
- A11 requires matured scored-wallet flow, not merely wallet identifiers;
- A12/A13 require at least 8/12 symbols respectively.

## Validation

Base V5 control-plane validation on qbee passed 18/18 tests before the data-plane extension. The data-plane extension has its own adversarial tests and must also pass on qbee before its output is used.

```bash
python3 -m pytest \
  tests/unit/test_alpha_foundry_v5_core.py \
  tests/unit/test_alpha_foundry_v5_research.py \
  tests/unit/test_alpha_foundry_v5_data_planes.py \
  tests/unit/test_alpha_foundry_v5_snapshot_reset.py \
  -v
```

## Operator entry points

```bash
python3 scripts/alpha_foundry_v5_readiness.py --help
python3 scripts/alpha_foundry_v5_freeze_dataset.py --help
python3 scripts/alpha_foundry_v5_discover.py --help
python3 scripts/build_alpha_foundry_v5_data_planes.py --help
```

See `reports/ALPHA_FOUNDRY_V5_PROTOCOL.md` for the full scientific and operational contract.
