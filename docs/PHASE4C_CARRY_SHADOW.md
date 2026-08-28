# Phase 4C — CarryBasisAdapter shadow onto TruthEngine

Read-only, no-effect shadow of one legacy runner (`CarryBasisAdapter`,
`src/alpha20/tournament/runner_adapters.py`, registry id `carry_basis_v12`)
onto `src.futur.truth`'s `TruthEngine`, for differential validation. The
legacy runtime remains the sole source of truth for decisions, sizing, and
results — nothing here migrates a runner, corrects legacy accounting, or
claims a profitable strategy. See each module's own docstring for the
full rationale; this file is the map between them.

## Why CarryBasisAdapter

It exercises spot, linear perp, fees, funding, basis, margin exposure, and
closing both legs of a position — the richest single runner for exercising
`src/futur/truth`'s domain model end to end.

## Package layout (`src/alpha20/tournament/truth_shadow/`)

Lives outside `src/futur/truth` on purpose: the truth domain must never
import `src.alpha20`/`src.institutional` back (enforced by
`tests/architecture/test_truth_domain_has_no_alpha20_dependency.py`). Only
this package, and code above it, depends on both.

- **`mapping.py`** — converts `MultiLegBacktester`'s `leg_ledger` (one row
  per `PositionLeg`, from `src.institutional.backtest.multileg_backtester`)
  into `src.futur.truth` `Event`s. Full field-by-field mapping table in
  the module docstring: asset→`ProductSpec` (USDT treated 1:1 as USD, a
  documented convention), `leg_type`→`ProductType`/fill side, fee/funding
  attribution via cross-cycle delta-tracking (never a fabricated split),
  MARK price recovered algebraically from `price_pnl` (never guessed).
  `UnmappableLegError` rejects anything it cannot honestly convert.
- **`shadow_runner.py`** — `CarryBasisShadowRunner.run_cycle()` calls the
  real, unmodified `CarryBasisAdapter.decide()` exactly once and returns
  its `(events, new_state)` completely untouched; the shadow observation
  (capturing the internal `MultiLegResult` via a narrow, reversible
  monkeypatch of `MultiLegBacktester.run`, then mapping+applying to
  `TruthEngine`) happens strictly after and is fully exception-isolated —
  a shadow failure can never affect the legacy result already computed
  and returned. `ShadowConfig.enabled=False` is the kill switch: none of
  the shadow's own machinery runs at all when off.
- **`comparator.py`** — `DifferentialComparator` compares, after every
  state-changing Truth event, per-instrument fields (against the same
  `leg_ledger` snapshot used to build the events — a mismatch here is a
  `SHADOW_MAPPING_ERROR`, i.e. this package's own bug) and portfolio-level
  fields (against the legacy `portfolio_ledger`'s as-of row — a mismatch
  here is `MATCH` or `UNEXPLAINED_DIVERGENCE`, gated by an explicit
  per-`(venue, field)` `ToleranceConfig`, never a global fudge factor).
  `margin_used` has no legacy analog at all and is always
  `EXPECTED_LEGACY_DIVERGENCE`, documented, not hidden as a `MATCH`.
  `DifferentialLog` writes one append-only JSONL line per
  `(event, field)`: `run_id`, `sequence`, `event_id`, `timestamp`, `field`,
  `legacy_value`, `truth_value`, `difference`, `tolerance_applied`,
  `classification`, `cause`.

## Commit 5 — real-data replay: BLOCKED

`CarryBasisAdapter.decide()` sources all price/funding data through
`load_enriched()`, which reads `data/enriched/{asset}_1h_enriched.parquet`.
That directory does not exist in this environment — confirmed exhaustively
(no enriched file for `BTCUSDT`/`ETHUSDT` anywhere under `data/`, only raw,
unenriched klines, which this shadow does not attempt to featurize itself,
since doing so would mean fabricating the conditions for a PASS rather
than replaying already-existing real data). See
`tests/integration/test_alpha20_carry_truth_shadow_commit5_real_replay.py`
for the exact, verified determination (not just asserted here) and the
full list of blocked coverage points, and for what unblocking would take.

## Verdict

`TRUTH_ENGINE_CARRY_SHADOW_VALIDATED` means: the shadow wiring is correct,
exception-isolated, and has no effect on the legacy runner, and every
comparison the available data allowed produced `MATCH` or a documented
`EXPECTED_LEGACY_DIVERGENCE`/`SHADOW_MAPPING_ERROR` (zero of the latter
after fixes) — never `UNEXPLAINED_DIVERGENCE`. It does NOT mean a runner
was migrated, that legacy's accounting is correct, or that any strategy
is profitable.
