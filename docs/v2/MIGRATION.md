# V2 Migration Log

Tracks directory-level classification decisions and the evidence behind them.
Per-file migration into `src/futur/` has not started. See
`docs/v2/EXECUTION_STATE.md` for session-by-session progress.

## Classification methodology (Phase 0, first pass)

For each top-level directory: `.py` file count, last git-touch date, and
whether any file under the currently-active dirs (`src`, `scripts`,
`research`, `tests`, `configs`) imports it (`grep -rlE '^(from|import) <mod>'`).
Reproducible via `python3 scripts/v2_inventory.py --write` →
`docs/v2/INVENTORY.generated.md`, which is the authoritative, always-current
table. This file records the *reasoning*, not the numbers — read the
generated table for current facts.

Classification is per-directory, not per-file, and is a **first pass**.
`MIGRATE` and `UNVERIFIED` verdicts are provisional until a deeper,
per-file pass happens (tracked in `EXECUTION_STATE.md` → Next action).

## Verdicts (as of `forensic-baseline-2026-07-27`)

- **CANONICAL_CANDIDATE**: `src/`, `research/`, `configs/`, `reports/`, `data/`.
  `src/institutional` has real, tested exposure-limit enforcement
  (`constraints.py`, `invariants.py`) exercised from the backtest path
  (`backtest/portfolio_backtester.py:329-330`, `backtest/multileg_backtester.py`).
  `research/edge_factory` runs a disciplined preregister → falsify → govern
  loop (see `QUARANTINE_2026-07-21.md`, `governance:` commits in `git log`)
  that must not be disrupted or treated as "legacy to replace."
- **MIGRATE**: `scripts/`, `tests/`, `core/`, `config/`, `ai/`, `risk/`,
  `data_pipeline/`. Still actively imported by the CANONICAL_CANDIDATE dirs,
  so none of this is dead — it needs consolidation into `src/futur/`, not
  deletion. `core/` in particular looks like it may substantially overlap
  `src/institutional/` and needs a diff, not a copy.
- **LEGACY**: `legacy/` (1247 of the repo's 1734 `.py` files). Self-declared
  by name, last touched 2026-05-27 (two months stale relative to `src/` and
  `research/` at 2026-07-22), zero references found from any active dir.
  Phase 1 gate requires this be made non-importable by the runtime — not
  deleted (ledgers/history rule).
- **UNVERIFIED**: `Server/`, `production/`, `trading-system/`, `hedge_fund/`,
  `deploy/`, `bin/`, `artifacts/`, `runs/`, `state/`, `frontend_pipeline/`.
  No references found from active dirs in a static grep, but that grep only
  catches `import`/`from` statements — it misses dynamic imports, subprocess
  invocation, and systemd unit files (`deploy/systemd/`). Do not treat
  "UNVERIFIED" as "safe to delete." `frontend_pipeline/` specifically is
  bind-mounted live into the `command-center` Docker service and reachable
  via an ngrok public tunnel per `docker-compose.yml` — it may be dead in
  the Python-import sense while still being the live-running dashboard.

## Known-defect verification log (master-prompt claims checked against HEAD)

| Claim | Verdict | Evidence |
|---|---|---|
| "Clone from scratch is broken" | **CONFIRMED** | `docker compose config` fails on missing `NGROK_AUTHTOKEN` with no `.env`; independently, `launch.sh:107` starts a `frontend` compose service that doesn't exist (only `command-center` does); independently, `launch.sh`'s pip-install fallback references `requirements-api.txt` at repo root, which doesn't exist (only under `legacy/*`). Three independent breaks stacked in the same startup path. |
| "No single canonical interpreter / dependency spec" | **CONFIRMED** | No root `pyproject.toml`, `uv.lock`, or `requirements*.txt`. 4 reachable interpreters on this machine disagree on installed packages; the one that resolves first on `PATH` (`/opt/homebrew/bin/python3` → Homebrew 3.14) has neither `pandas` nor `yaml` installed. |
| "MongoDB / admin routes may be exposed without sufficient auth" | **CONFIRMED** (config-level) | `docker-compose.yml` publishes `mongodb` on `0.0.0.0:27017` with no auth env vars set anywhere. See `THREAT_MODEL.md`. Whether it's currently *running* and reachable wasn't checked this session. |
| "`max_gross_exposure`/`max_net_long_exposure` declared but not applied" | **NOT REPRODUCED in `src/institutional`, UNVERIFIED for live/paper path** | `src/institutional/portfolio/constraints.py:48` and `backtest/portfolio_backtester.py:329-330` actually clip/reject on the limit; `invariants.py` raises `InvariantViolation`. Only backtest-path callers were found this pass — whether the live/paper decision path (likely under `src/alpha20`, not yet inspected) calls into this module is an open question, not a confirmed pass. Do not cite this as "fixed" without that follow-up. |
| "Some endpoints return mocks or an EMA fallback instead of the canonical engine" | **NOT REPRODUCED this pass, NOT RULED OUT** | Static grep for mock/EMA-fallback patterns in `src/institutional`, `production/`, `Server/` found nothing. This was a grep over source text, not a live endpoint probe — a real verification needs to hit running endpoints and compare output paths, which wasn't done this session. |

## Explicit non-actions this session

- No files moved, renamed, or deleted.
- `legacy/` untouched (still importable — Phase 1 gate item, not done).
- `reports/experiments.yaml` untouched; new registry is additive
  (`reports/registry/experiments.jsonl`).
- No `pyproject.toml`/`uv.lock` created yet — doing that before pinning an
  interpreter and running the existing test suites would risk locking in
  the wrong Python version.
