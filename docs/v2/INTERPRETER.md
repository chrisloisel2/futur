# V2 Interpreter Selection (Phase 1)

## Decision

Pinned for all V2 diagnostic/migration work on this Mac, until a real
`pyproject.toml`/`uv.lock` replaces this ad hoc pin:

```
/opt/homebrew/Caskroom/miniconda/base/bin/python3
```

- **Version:** Python 3.12.2 (conda-forge build)
- **Activation:** `source /opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh && conda activate base`
  (or just invoke the absolute path directly, which is what every command in
  `docs/v2/PHASE1_DIAGNOSTIC.md` does, to stay independent of shell RC state)
- **pip:** 24.2

## Why this one, not the other three reachable interpreters

Checked all 4 interpreters reachable via `which -a python3` plus the conda
`trading` env against the non-stdlib imports actually used in `src/` and
`tests/` (`numpy`, `pandas`, `pytest`, `sklearn`, `yaml`, `lightgbm`,
`fastapi`, `uvicorn`, `pyarrow`, `pymongo`, `httpx`, `dotenv`):

| interpreter | numpy | pandas | pytest | sklearn | yaml | lightgbm | fastapi | uvicorn | pyarrow | pymongo | httpx | dotenv |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `/opt/homebrew/bin/python3` (Homebrew 3.14, PATH default) | — | — | — | — | — | — | — | — | — | — | — | — |
| miniconda `base` | ok | ok | ok | ok | ok | **MISS** | ok | ok | ok | ok | ok | ok |
| miniconda `trading` | ok | ok | ok | ok | ok | **MISS** | ok | ok | ok | **MISS** | ok | ok |
| `anaconda3` | ok | ok | ok | ok | ok | **MISS** | **MISS** | **MISS** | ok | **MISS** | **MISS** | ok |

miniconda `base` has the widest coverage (11/12; only `lightgbm` missing,
used by `src/institutional/engines/ml_engine.py`,
`src/institutional/engines/event_production.py`, and several `scripts/train_*`
— none of which were exercised in this session's test runs). Selected on
that basis.

## Known gap

`lightgbm` is not installed in the pinned interpreter. Not installed this
session — per the Phase 1 rule "don't fix before recording the initial
diagnostic," this is recorded as a gap, not silently patched. Any future
session running gradient-boosting model code needs to install it first and
note that as a deliberate environment change here, not fold it into an
unrelated commit.

## Observed anomaly (not investigated further this session)

`pip freeze` on this interpreter reports `numpy==2.1.2`, but
`import numpy; numpy.__version__` returns `2.4.4` (confirmed via `conda list
numpy`, which shows `2.4.4` installed from PyPI). pip's installed-package
metadata and the actually-importable version disagree — consistent with a
stale/duplicate `dist-info` left in `site-packages` from an earlier install.
Not blocking for this session's diagnostic work; worth cleaning up
(`pip check`, `pip list --outdated`, or just rebuild the env from a lockfile
once one exists) before trusting `pip freeze` output from this interpreter
for anything load-bearing.

## Full package snapshot

387 packages via `pip freeze`, captured 2026-07-27. Not committed in full
(no lockfile discipline yet — that's Phase 1's actual foundation work, not
this diagnostic pass) but the load-bearing subset is the table above.
