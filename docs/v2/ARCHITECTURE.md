# V2 Architecture

## Status: target only — not yet built

Nothing in this file describes code that exists on `HEAD`
(`ecd93adefb89a7194222607cec65105f9ac44981`) today. There is no root
`pyproject.toml`, no `src/futur/` package, no unified `futur` CLI, and no
single event-driven Truth Engine yet. This document records the target
shape and, as migration actually happens, which current directory each
target module is meant to absorb. Do not treat anything below as "done"
without a corresponding entry in `docs/v2/EXECUTION_STATE.md` and a
passing gate command.

## Target layout

```
pyproject.toml
uv.lock
src/futur/
  domain/
  data/
  features/
  research/
  backtest/
  execution/
  accounting/
  risk/
  portfolio/
  runtime/
tests/
configs/
docs/v2/
reports/registry/
legacy/
```

## Current-state → target mapping (provisional, see `MIGRATION.md`)

This is a first hypothesis based on the Phase 0 directory inventory, not a
committed migration plan:

| Current | Likely target | Confidence |
|---|---|---|
| `src/institutional/portfolio/` | `src/futur/portfolio/` | Medium — has real, tested exposure-limit enforcement already; needs a design check against the Truth Engine's single-ledger requirement (Phase 2) before a straight move. |
| `src/institutional/backtest/` | `src/futur/backtest/` | Medium — same caveat: Phase 2 requires backtest/replay/paper to share one event-driven core; current code's relationship to that requirement is unverified. |
| `src/institutional/risk/`, `risk/` | `src/futur/risk/` | Low — two directories (`src/institutional/risk/` and top-level `risk/`) may overlap; not yet diffed. |
| `research/edge_factory/` | `src/futur/research/` (engine code) + stays as `research/` (preregistrations, governance docs) | Medium — the preregister/falsify/govern process itself should not move; only its reusable engine code (`multileg_engine/`) is a migration candidate. |
| `core/`, `config/`, `ai/`, `data_pipeline/` | Partially `src/futur/data/`, `src/futur/features/` | Low — still actively imported by `scripts/`, meaning a naive move breaks live tooling; needs per-file triage. |
| `legacy/` | Nowhere — stays as non-importable archive | High |
| `Server/`, `production/`, `trading-system/`, `hedge_fund/` | Unknown | Very low — classified `UNVERIFIED`, see `MIGRATION.md`. Could be dead, could be load-bearing for the live dashboard/deploy path. |

## Truth Engine (Phase 2) — not started

The single event-driven core (`MarketDataReceived` → ... → `Reconciled`)
required by the master prompt does not exist yet in any form that's been
verified to satisfy its invariants. `src/institutional` has *some* of the
required concepts (positions, invariants, constraints) but has not been
audited against the full event list, the ledger-conservation invariant, or
the backtest/replay/paper determinism requirement. That audit is future
work, not started this session.
