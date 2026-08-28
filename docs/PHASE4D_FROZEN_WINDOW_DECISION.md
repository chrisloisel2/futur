# Phase 4D commit 8 — frozen replay window decision

Decided and committed at **2026-07-28T21:46:18Z**, BEFORE running the real
replay or looking at any of its results (Commit 8 point 2: "fige cette
règle avant d'examiner les résultats du runner"). Whatever this window
actually produces once replayed is accepted as-is -- no rule below may be
adjusted afterward to change, avoid, or reduce a divergence or to select
a more "interesting" or profitable period.

## Available data (from data/manifests/carry_shadow_data_manifest.json,
## commit 2b9ebc1)

Both BTCUSDT and ETHUSDT enriched series (real close + real funding_rate)
span the identical, fully contiguous range:

```
2024-07-01T00:00:00Z  ->  2026-07-28T21:00:00Z
18190 hourly rows each, 0 gaps, 0 duplicates
```

This is the complete intersection of what's available for both assets --
neither series needs trimming to align with the other.

## Rule (fixed, mechanical, decided now)

1. **`end`** = the LAST available bar in the intersection above:
   `2026-07-28T21:00:00Z`. Not chosen for any property of the results --
   it is simply the most recent data that exists.
2. **Warm-up** = 14 days. `FundingGateConfig.window_periods = 21` (~7 days
   of funding events) is the only lookback state carry-open eligibility
   depends on; 14 days is double that, a fixed safety margin, not tuned
   after seeing what it produces.
3. **`paper_start`** = `end` − 60 days = `2026-05-29T21:00:00Z`
   (`2026-05-29` as the date string `CarryBasisAdapter.decide()`
   consumes), i.e. an ACTIVE replay window of 60 days, preceded by
   (`paper_start` − `2024-07-01`) ≈ 693 days of real history already
   sitting behind it for
   the funding-gate's own rolling lookback (far more than the 14-day
   minimum above -- the warm-up requirement is trivially satisfied by the
   data's own range, not by shrinking the active window to fit it).
4. **60 days** was chosen for tractability (a real multi-year hourly
   backtest is unnecessarily slow for a validation exercise that produces
   no profitability claim either way) -- fixed BEFORE this rule was
   applied, not adjusted afterward. If commit 8's coverage requirement
   ("ouverture spot et perp; marks; funding réel; frais; réduction ou
   clôture; clôture terminale") is not met inside this window, the
   verdict is `BLOCKED_COVERAGE`, per commit 8 point 7 -- the window is
   NOT widened or moved to manufacture the missing coverage.

## Runner and config (unchanged, per commit 8 point 4)

- `runner_id = carry_basis_v12`
- `venue = binance_usdm`
- Configuration: the REAL entry from `configs/alpha20_runners.yaml`, loaded
  via `src.alpha20.tournament.runner_registry.get_spec("carry_basis_v12")`
  -- `engines_long`, `carry_fraction`, `long_fraction`, `max_open_longs`,
  every gate flag, all exactly as registered. Nothing overridden.
- Costs: `MultiLegConfig`'s own `taker_fee_bps`/`slippage_bps`/
  `maker_fee_bps`/`borrow_bps_per_year` defaults, exactly as
  `CarryBasisAdapter.decide()` constructs them today -- no shadow-side
  override of any of these.

## Four provenance identifiers (commit 8 point 5 -- never conflated)

1. **Shadow execution commit**: the git HEAD of THIS repository at the
   moment the replay is actually run (recorded by the replay script at
   run time, not hand-typed here -- see the run's own output/report).
2. **Historical experiment commit**: `2fe693b` -- `RunnerSpec.git_commit`
   for `carry_basis_v12` in the registry, i.e. the commit the ORIGINAL
   backtest/registration of this runner's config is associated with.
   Unrelated to (1); this repository has moved on since.
3. **Registry config hash**: `9e025f4590c1dd39aec94210` --
   `RunnerSpec.config_hash`, `configs/alpha20_runners.yaml`'s own hash of
   the runner's registered config block.
4. **Effective serialized config hash**: SHA-256 of the ACTUAL
   `MultiLegConfig` object `CarryBasisAdapter.decide()` constructs at run
   time (every field, canonically serialized) -- proves what ACTUALLY ran
   matches what's in the manifest, independent of whether the registry's
   own `config_hash` (3) was computed the same way or over the same
   fields.
