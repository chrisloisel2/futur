# V2 Execution State

This file is the single source of truth for "where V2 work stands." Update it
every session. Do not let it drift from what was actually run.

## Current position

- **Commit at session start (HEAD of `main`):** `ecd93adefb89a7194222607cec65105f9ac44981`
- **Forensic baseline tag:** `forensic-baseline-2026-07-27` → `ecd93adefb89a7194222607cec65105f9ac44981`
- **Working branch:** `v2/foundation` (created off `main` at the same commit; `main` untouched)
- **Phase:** Phase 0 (freeze & map) — done for this pass. Phase 1 (foundation rebuild) — **diagnostic only**, no code migrated yet.
- **Origin sync:** local `main` was up to date with `origin/main` at session start (`git status`: "up to date", "nothing to commit").
- **Known divergence out of scope for this repo's git state:** per prior-session memory, the deployment host `qbee@100.127.59.114` has diverged from `main` and is unreconciled — not re-verified this session, needs `verify_config_deployment.py` run against that host before any live/deploy action.

## Tasks completed this session

1. Ground-truthed HEAD, branch, remote, tags, worktree cleanliness (Phase 0).
2. Searched for `Audit.txt` / `Etat-de-l-art.txt` referenced by the master prompt — **not found anywhere on disk** (`mdfind` + `find ~`, both empty). Proceeded using the live repo as ground truth, per the master prompt's own instruction to treat those docs as historical/obsolete.
3. Inventoried top-level directories: file counts, last-git-touch date, cross-references from active dirs (`src`, `scripts`, `research`, `tests`, `configs`). Built `scripts/v2_inventory.py` as the reproducible regeneration command; output written to `docs/v2/INVENTORY.generated.md`.
4. Verified two specific "known defects" named in the master prompt against current HEAD (see `MIGRATION.md` for detail):
   - Clone-from-scratch startup: **confirmed broken**, two independent failures found.
   - `max_gross_exposure` / `max_net_long_exposure` "declared but not enforced": **not reproduced** in `src/institutional/portfolio/constraints.py` and `src/institutional/backtest/portfolio_backtester.py` — enforcement code exists and is exercised in the backtest path. Live/paper-path wiring **not yet verified** (see open questions below) — do not treat as confirmed-fixed either.
   - Mock/EMA-fallback endpoints: no hits in `src/institutional`, `production/`, `Server/` for this pass's grep patterns — **absence of evidence, not evidence of absence**; needs a live-endpoint smoke test, not just static grep.
5. Confirmed real security exposure in `docker-compose.yml`: MongoDB published on `0.0.0.0:27017` with no auth configured anywhere in the compose file or `.env.example`. See `THREAT_MODEL.md`.
6. Built `reports/registry/experiments.jsonl` (append-only) and `scripts/v2_migrate_experiments_registry.py`, which migrated the 13 entries in `reports/experiments.yaml` into the new schema with unknown V2-required fields (`commit`, `data_manifest_hash`, `config_hash`, `n_trials`, `costs`, `seed`, `artifact_links`) left explicitly `null` rather than fabricated. `reports/experiments.yaml` itself was not modified.
7. Confirmed no root `pyproject.toml` / `uv.lock` / `requirements*.txt` exists (only `trading-system/pyproject.toml`, and `requirements*.txt` files live only under `legacy/`).
8. Confirmed 4 different Python interpreters are reachable on this machine and disagree on installed packages (see below) — no single canonical interpreter is currently designated for this project.

## Commands actually executed (with results)

```
$ git rev-parse HEAD
ecd93adefb89a7194222607cec65105f9ac44981

$ git status
On branch main / up to date with origin/main / nothing to commit

$ docker compose config
ERROR: required variable NGROK_AUTHTOKEN is missing a value (exit interpolation error)
  → fails on a clean clone with no .env present

$ grep -n "services+=(frontend)" launch.sh
launch.sh:107:  services+=(frontend)
  → docker-compose.yml has no service named "frontend" (only mongodb, qdrant,
    command-center, ngrok) → `docker compose up -d ... frontend` would fail
    with "no such service" even if the NGROK_AUTHTOKEN interpolation error
    above were fixed

$ ls requirements-api.txt
No such file or directory
  → launch.sh falls back to `pip install -r "$ROOT_DIR/requirements-api.txt"`
    when required imports (fastapi, uvicorn, pyarrow, yaml, sklearn, pymongo,
    httpx, dotenv) are missing from the resolved interpreter; that file does
    not exist at repo root, only under legacy/*, so this path crashes too

$ which -a python3
/opt/homebrew/bin/python3          (→ /opt/homebrew/opt/python@3.14/bin/python3.14, stdlib only, no pandas/yaml)
/usr/bin/python3
/opt/homebrew/Caskroom/miniconda/base/bin/python3   (pandas 2.2.3, yaml 6.0.2, py3.12.2)
  + conda env "trading" (pandas 2.2.2, py3.10.19)
  + /opt/homebrew/anaconda3/bin/python3 (pandas 2.2.2, py3.12.4)
  → 4 interpreters, no lockfile, no documented "use this one"; `python3` on
    PATH by default is the bare stdlib-only Homebrew build

$ python3 scripts/v2_inventory.py --write
(ran clean, wrote docs/v2/INVENTORY.generated.md, exit 0)

$ python3 scripts/v2_migrate_experiments_registry.py   (run with the miniconda-base interpreter, which has PyYAML)
appended 13 migrated record(s) to reports/registry/experiments.jsonl
total records now in ledger: 13
legacy source untouched: reports/experiments.yaml (13 entries)
(exit 0)
```

No `pytest` run yet this session — there is no root `pyproject.toml`/`pytest.ini` and no confirmed interpreter with the project's actual dependencies installed system-wide; running `tests/` blind against whichever `python3` resolves first would not be a meaningful signal (see finding above) and risks a false BROKEN verdict caused by interpreter choice rather than code. Next session should pin an interpreter first (see Next action).

## Decisions made

- Did **not** attempt to build `src/futur/` or a root `pyproject.toml` in this pass. The master prompt scopes iteration 1 to "Phase 0 + reproducible Phase 1 diagnostic," not Phase 1 execution. With 1734 tracked `.py` files across ~20 top-level directories and an active, disciplined `research/edge_factory` program already mid-flight (do not disrupt it), a real migration needs its own dedicated phase with per-file triage, not a same-session bolt-on.
- Did not touch `legacy/`, `reports/`, `data/`, or `reports/experiments.yaml` — append-only / non-destructive per the master prompt's hard rules.
- Classified directories at the top-level-directory granularity, not per-file. This is a first pass; `MIGRATE`/`UNVERIFIED` directories need a second, deeper pass before any file is actually moved into `src/futur/`.

## Open questions / things NOT yet verified (do not assume either way)

- Is `PortfolioConstraints.check()` / `check_portfolio_invariants()` actually called on the **live/paper** order path, or only in `backtest`/`meta_allocator`? Only backtest-path callers were found this pass (`src/institutional/backtest/multileg_backtester.py`, `src/institutional/portfolio/meta_allocator.py`). If ALPHA_20's live/paper runner lives elsewhere (e.g. under `src/alpha20`), it hasn't been checked yet for whether it calls into `src/institutional/portfolio/constraints.py` at all.
- Whether `production/`, `Server/`, `trading-system/`, `hedge_fund/` are truly dead or reached through a path this session's grep didn't cover (e.g. dynamic imports, subprocess calls, systemd units in `deploy/`).
- Full git-history secret scan (only a tracked-file-level pass was done this session; `.env` is gitignored but history was not scanned for accidentally-committed secrets).
- Whether Mongo/qdrant/command-center are currently *running* and reachable from outside `localhost` on this machine or on `qbee@100.127.59.114` — the compose file's binding is a static config finding, not a confirmation of current live exposure.

## Files modified this session

- `scripts/v2_inventory.py` (new)
- `scripts/v2_migrate_experiments_registry.py` (new)
- `docs/v2/INVENTORY.generated.md` (new, generated)
- `docs/v2/EXECUTION_STATE.md` (new, this file)
- `docs/v2/MIGRATION.md` (new)
- `docs/v2/THREAT_MODEL.md` (new)
- `docs/v2/ARCHITECTURE.md` (new)
- `docs/v2/CONVENTIONS.md` (new)
- `reports/registry/experiments.jsonl` (new, append-only, 13 migrated records)
- Git: tag `forensic-baseline-2026-07-27`, branch `v2/foundation`

## Next action (exact)

1. Pin one interpreter for V2 work and record it here (candidate: miniconda base, `/opt/homebrew/Caskroom/miniconda/base/bin/python3`, since it already has pandas/yaml and is what memory says this project has used) — then run `tests/` and `trading-system/tests/` against it and record real pass/fail counts before writing a single line of migration code.
2. Verify whether `src/alpha20`'s live/paper decision path calls `src/institutional/portfolio/constraints.py` — resolves the open exposure-cap-enforcement question definitively instead of leaving it UNVERIFIED.
3. Second-pass triage of the five `UNVERIFIED` top-level dirs (`Server`, `production`, `trading-system`, `hedge_fund`, `deploy`) to move them to a real classification before Phase 1 migration starts.
4. Only after 1–3: draft the root `pyproject.toml` + `uv.lock` and the `futur validate` / `futur replay --fixture smoke` CLI skeleton required by the Phase 1 gate.

---

## 2026-07-27, continued — source document review attempt (addendum)

Asked to review `project_sources/01-Audit.txt` (claimed SHA-256
`516cee6261e15c8d30d963940d079e968c3ea2149a2304864250c50799bd2b5c`) and
`project_sources/02-Etat-de-l-art.txt` (claimed SHA-256
`9fbe135e48afd0ec3b287735098b1890e254d7d752e0c366ad2db6cc8919e00e`) before
starting Phase 1. **Neither file was found anywhere on this machine** —
repeated the prior search plus additional bounded searches of
`~/Downloads`, `~/Desktop`, `~/Documents`, and this session's other working
directories. Full detail and exact commands in `docs/v2/SOURCE_REVIEW.md`,
which is created but marked `BLOCKED` — it does not classify any claims,
since no file content exists to classify without inventing it. Proceeding
straight to Phase 1 per the master prompt's own "repo is ground truth" rule.
This is a real, standing blocker (not resolved by working around it) —
surface it to the user again if source review is asked for later without
new file-location information.
