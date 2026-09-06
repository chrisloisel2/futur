# tests/future/

Tests that exercise real, valuable logic but are currently blocked by a
dependency this packaging phase (Phase 3) deliberately does not fix, per
the rebuild mission's own rule: never fake a module just to satisfy an
import.

Not part of the canonical suite -- `pytest`'s `testpaths` (see
`pyproject.toml`) points at `tests/unit`, `tests/integration`, and
`tests/architecture` only, so nothing here runs by default. Run explicitly
with `pytest tests/future/ -v` to check on it.

## test_fold_aware_loader.py

Tests `FoldAwareModelLoader` / `BacktestFoldPlan` (walk-forward fold
integrity checking) — real, working code in `scripts/run_backtest_engine.py`.
That script has two unrelated, module-level imports of code that has never
existed anywhere in this repo's git history:
`src.institutional.data.loaders.load_asset_1h` (used only inside `main()`)
and `src.institutional.data.dataset_builder.*` (used both inside `main()`
and at module level, in `CONFIG_MAP`). Both are Phase 4 (data lake causal)
work, not Phase 3 packaging.

Move this back to `tests/unit/` or `tests/integration/` once
`scripts/run_backtest_engine.py`'s data-loading dependencies are real, or
once its import structure is made lazy enough that a missing data layer
doesn't block the fold-plan logic this test actually exercises.
