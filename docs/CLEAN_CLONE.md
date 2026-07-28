# Clean-clone workflow

Phase 3 (base Python minimale) deliverable: this is the exact, verified
sequence a fresh `git clone` of this repo needs to get a working,
reproducible environment. Every command below was actually run against a
freshly-rebuilt `.venv` before this doc was written -- not assumed correct
from reading `pyproject.toml`.

## Setup

```bash
git clone <this repo> && cd futur
rm -rf .venv          # only needed if you're re-running this, not on a real fresh clone
uv sync --frozen
```

`uv sync --frozen` installs the 10 direct runtime dependencies (numpy,
pandas, pyarrow, pyyaml, lightgbm, scikit-learn, joblib, pymongo,
websockets, requests) plus the `dev` dependency group (pytest, pytest-cov,
ruff, mypy) -- both are installed by a bare `uv sync`, no `--extra`/
`--group` flags needed (see `pyproject.toml`'s `[dependency-groups]`
comment for why this matters and what it replaced).

## Verification gates

```bash
uv run python -c "import src.alpha20"      # canonical runtime importable
uv run futur --help                         # canonical CLI entrypoint
uv run ruff check .                         # lint (not zero-gated, see below)
uv run mypy src                             # types (not zero-gated, see below)
uv run pytest                               # canonical suite -- MUST be 0 failed, 0 errors
uv run python -m compileall src             # canonical runtime compiles clean
```

Expected results as of this phase's last commit:

| command | result |
|---|---|
| `import src.alpha20` | imports cleanly |
| `futur --help` | prints usage, exit 0 |
| `ruff check .` | 4,211 findings (informational -- not part of the mission's zero-tolerance list; `legacy/` excluded, see below) |
| `mypy src` | 139 findings in 32/169 files (informational, same reason) |
| `pytest` | **391 passed, 0 failed, 0 errors** |
| `compileall src` | clean, exit 0 |

The mission's explicit zero-tolerance requirements -- 0 collection errors,
0 failed tests, 0 forbidden imports, 0 missing dependencies, 0 dependency
on unversioned local data -- are all met. `ruff`/`mypy` are not on that
list; see below for why they're not being driven to zero in this phase.

## Debts this phase deliberately left open, and why

These are not oversights -- each was a real decision, documented here so a
future session doesn't have to rediscover the reasoning.

- **`src.alpha20` / `src.institutional` keep their dotted `src.` prefix**,
  while the new `futur` package (this phase's CLI) does not. The mission's
  target layout shows `futur` nested under `src/`, and its required entry
  point is the bare `futur.cli:main` (no `src.` prefix) -- reconciled via
  two independent `[tool.setuptools.packages.find]` discovery roots
  (`.` finds `src`/`src.alpha20.*`/`src.institutional.*`; `src` finds
  `futur`/`futur.*`), not by renaming the existing tree. Renaming
  `src/alpha20` -> `src/futur/alpha20` would touch hundreds of
  `from src.X import Y` call sites across `src/`, `tests/`, `scripts/`,
  `research/` for zero behavioral gain in a packaging-only phase that must
  not touch strategies (per the mission's own explicit escape hatch: "si
  ça ne peut pas se faire sans rupture massive, conserve temporairement
  src.alpha20"). Revisit once/if a dedicated migration phase moves
  `src/institutional`'s and `src/alpha20`'s logic into `src/futur/`
  file-by-file with its own tests.
- **`ai/`, `core/`, `scripts/` are not installed packages.** `src/institutional/engines/legacy_bridge.py`
  and `src/institutional/universe/asset_quality_filter.py` import from them,
  but only inside functions (never at module load time), and
  `legacy_bridge.py`'s usage resolves its own path from `__file__` rather
  than relying on CWD -- verified this doesn't break `import src.alpha20`,
  `futur --help`, or the canonical test suite before treating it as safe
  to leave alone. `pyproject.toml`'s `pythonpath = ["."]` pytest setting is
  a second safety net for the test suite specifically. Consolidating these
  into `src/futur/` is Phase-1-master-plan-level architectural work, out
  of this phase's scope.
- **`ruff`/`mypy` are not zero-gated.** 1700+ files (most of `legacy/`,
  `scripts/`, `ai/`, `core/`, `data_pipeline/`, `frontend_pipeline/`) have
  never been linted or type-checked. Turning either fully strict here
  would produce thousands of pre-existing findings unrelated to this
  packaging phase, drowning any real new signal. `legacy/` is excluded
  from ruff entirely (72% of raw findings, none of it maintained code);
  `mypy` is scoped to `src/` only, `ignore_missing_imports = true`, not
  `strict`. The 4,211 / 139 current findings are real and worth working
  down eventually, just not a gate for this phase.
- **`tests/future/`** holds `test_fold_aware_loader.py`: real, valuable
  logic (walk-forward fold integrity) blocked by a host script
  (`scripts/run_backtest_engine.py`) that imports two modules
  (`src.institutional.data.loaders`, `.dataset_builder`) that have never
  existed anywhere in this repo's history. Building them is Phase 4 (data
  lake causal) work; faking them was explicitly forbidden by the mission.
  Excluded from `pytest`'s `testpaths`, not deleted -- see
  `tests/future/README.md`.
- **`trading-system/pyproject.toml.retired`**: a second, independently-
  broken `institutional` package this repo has carried since before this
  rebuild. Renamed (not deleted) so no build tool auto-discovers it as a
  second live manifest. If `trading-system/`'s code is ever reconciled
  with `src/institutional/`, that should be a diff-and-merge into the
  canonical tree, not a revived second manifest.
- **`pandas<3`**: pinned after a fresh `uv lock` mid-phase resolved 3.0.5
  and broke 9 tests that use frequency aliases (`"1H"`, `"M"`) pandas 3.0
  removed outright. This codebase has been validated against pandas 2.x
  throughout this rebuild program's diagnostic sessions
  (`docs/v2/INTERPRETER.md`). Migrating every alias string to its 3.x
  spelling across `src/`, `tests/`, `scripts/`, `research/` is a real,
  separate, much larger mechanical change -- not attempted here.

## What this phase explicitly did not touch

Per the mission's own instruction: no Truth Engine, no strategy code, no
change to any reported result or backtest number. `futur`'s only
subcommand is `version` -- `validate`/`replay`/`experiment run` and
everything else belong to later phases.
