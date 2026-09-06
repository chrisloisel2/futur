# V2 Conventions

## Commit messages

`git log` on `main` already shows a consistent, working convention —
adopt it rather than inventing a new one:

```
<phase-ish prefix>: <imperative description>
```

Prefixes observed in use: `research:`, `governance:`, `data:`, `ops:`,
`engine:`, `test:`, `fix:`. Examples from `main`'s history: `governance:
freeze cross_sectional_momentum_crypto_v1 as CLOSED_NO_EDGE`, `ops: build
deployment hash-lock + startup refusal on drift`, `research: preregister
liquidation relative reversal v1`. V2 work should use the same style, adding
`v2:` only when a change is specific to the migration scaffolding itself
(e.g. this session's commit) rather than to a phase already covered by an
existing prefix.

## Branches

- `main` — never force-pushed, never rewritten.
- `v2/<topic>` — V2 migration work. This session's branch: `v2/foundation`.
- Forensic tags: `forensic-baseline-<date>` at any commit taken as a
  before-migration snapshot.

## Governance / research artifacts

Do not invent a new format for preregistration or verdict docs —
`research/edge_factory/*/PREREGISTRATION.md` and the `QUARANTINE_*.md` /
`governance:`-prefixed commit pattern already in use are the working
convention and predate this V2 effort. V2's Phase 3/5 tooling should target
*that* format, not replace it.

## Registry

`reports/registry/experiments.jsonl` — append-only, one JSON object per
line, schema in `scripts/v2_migrate_experiments_registry.py`
(`REQUIRED_V2_FIELDS`). Never edit or delete a line; a corrected verdict is
a new line referencing the old `experiment_id` in `reason`, not a rewrite.
`reports/experiments.yaml` (pre-V2) stays as historical record; new
experiments go to the `.jsonl` ledger only.

## Status: minimal on purpose

This file intentionally does not yet prescribe Python style, module layout
inside `src/futur/`, or CLI subcommand conventions — none of that code
exists yet (see `ARCHITECTURE.md`). Extend this file when Phase 1 actually
produces a `pyproject.toml` with real `ruff`/`mypy` config, rather than
prescribing rules ahead of the tooling that would enforce them.
