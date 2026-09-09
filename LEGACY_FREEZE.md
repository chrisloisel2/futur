# LEGACY FREEZE

As of the branch `p0-clean-research-kernel`, the following directories are **not**
part of the P0 trading path:

- `ai/`
- `trading-system/`
- `frontend_pipeline/`
- `signals/`
- `scrapers/`
- `legacy/`
- `core/`, `risk/`, `hedge_fund/`, `production/`, `research/`, `data_pipeline/`
- `src/futur/` (including the Truth engine)
- `alpha_foundry_v5/`, `market_physics_v3/`, `data_v2/`
- every top-level `alpha_sweep*.py`, `patch_v3.py`, `sweep_mirrors.py`

They may be **read** for reference. No P0 module may import them. The rule is
enforced by `research_kernel/tests/test_no_legacy_imports.py`, which walks the
abstract syntax tree of every P0 file and fails on a forbidden import.

The P0 active path is:

- `research_kernel/`
- `mechanisms/`
- `data_lake/`
- `sealed_forwards/`
- `paper_engine/`
- `execution_engine/` (empty, not wired)

## Why a freeze rather than a repair

The audit of the old path found faults that are not bugs to be fixed one by one
but a shape that cannot produce a trustworthy answer:

- horizons that disagree between components, from twelve minutes to eight hours
  inside one decision chain;
- two implementations of the same edge scorer;
- alternative signals collected and never consumed by any decision;
- a risk controller that exists, is never instantiated in a trading path, and
  whose `on_fill_pnl` is never called;
- no end-to-end backtest of the full pipeline;
- machine-learning endpoints returning mock data to a dashboard that presents it
  as real;
- state that is not persisted.

Repairing that while also using it is how a system stays broken. The kernel is
built beside it instead, with a hard import boundary, and the old path is left
running and readable.

## What is explicitly not touched

The live trading path continues to run from the `/home/qbee/futur` working tree
on `feat/free-derivatives-backfill`, with its timers, its collectors and its
paper accounting. This branch changes none of it. In particular the TRM Fleet
cluster remains untouched, as it has been ruled to be throughout this project.

## Reading data is allowed

A mechanism may read files any of the frozen systems produced. That is a file
read, not an import: no assumption, no horizon and no threshold crosses the
boundary with the bytes. Every dataset read this way carries a manifest and
passes gate 0 before anything is computed on it.
