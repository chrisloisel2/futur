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
| "`max_gross_exposure`/`max_net_long_exposure` declared but not applied" | **CONFIRMED — two distinct findings, do not conflate.** (1) live/paper path: reformulated 2026-07-27 session 3, see `docs/v2/PHASE1_DIAGNOSTIC.md` §3-4. (2) the literal file/fields the claim quotes: confirmed 2026-07-27 session 4, see `docs/v2/PHASE1_DIAGNOSTIC.md` §6 — **corrects session 1's "not reproduced" verdict**, which checked a different file. | **(1) Live/paper path** — the literal names/thresholds are confirmed never wired into the live/paper path — that part stands. But the live path is **not** simply "unblocked at 150% gross": `venue_unsecured_frac` is derived from `gross_usdt/nav`, so 150% gross on one venue genuinely trips `venue_unsecured_cap=0.15` and the governor really does move to `state="risk_reduced"` (proven: `tests/test_v2_phase1_live_exposure_cap_diagnostic.py`). The actual gap is one level downstream: `GovernorDecision.scale` (0.5 for risk_reduced, 0.0 for cash/kill) is **never read** by `PaperBroker.execute()` or any of the 3 runner adapters — confirmed by source-inspection test. `PaperBroker` only special-cases the literal string `"kill"`; `risk_reduced`/`cash` fill at full requested notional, identical to `risk_on`. Of the 3 adapters, `BasisTermAdapter`/`MHEventsAdapter` each have an explicit `risk_state == "kill"` guard before opening new positions; `CarryBasisAdapter` never reads `risk_state` at all (doesn't route through `PaperBroker`), so for that runner not even `kill` blocks via this mechanism. Net: governor is real and its state is real; only `kill` is actually enforced as an execution block; `risk_reduced`/`cash` are consultative (ledger-visible, not order-size-changing). **(2) The exact file/fields the master prompt names** — `src/institutional/portfolio/invariants.py::InvariantLimits` literally declares `max_gross_exposure=1.00` and `max_net_long_exposure=0.75` (the exact pair). `check_portfolio_invariants()` computes both and returns them, but never compares either to its own limits — proven with 2 long-only positions at 2x equity returning cleanly at `gross_exposure=2.0`, `net_long_exposure=2.0`, no exception. Session 1 checked a *different* file (`constraints.py::PortfolioConstraints`, genuinely enforced, reachable only from `meta_allocator.py`) and concluded "not reproduced" — that conclusion is correct for `constraints.py`, wrong as an answer to the master prompt's literal claim, which names `invariants.py`. A third and fourth independent gross-exposure definition also exist (`portfolio_backtester.py::PortfolioBacktestConfig`, enforced; `risk_engine.py`'s own dataclass, enforced) — **4 non-unified mechanisms total**, 3 enforced, 1 (the one live-reachable via `CarryBasisAdapter`) silently decorative. Moot in practice anyway: `CarryBasisAdapter.decide()` wraps its entire `MultiLegBacktester(...).run()` call in `except Exception`, so even the invariants `check_portfolio_invariants` *does* enforce (naked short, hedge cap, carry-delta) are silently downgraded to an "abstain" ledger event before they could ever protect real paper capital — proven by monkeypatching an `InvariantViolation` through the real call path. |
| "Some endpoints return mocks or an EMA fallback instead of the canonical engine" | **CONFIRMED in source, but currently unreachable — the endpoint is broken** (resolved 2026-07-27, see `docs/v2/PHASE1_DIAGNOSTIC.md` §5) | `frontend_pipeline/api_server.py` (started by `launch.sh` as "the API," port 8000) has a real autonomous-trading loop that silently falls back to an EMA 7/25 crossover and **still trades on it** when the ML `PredictionEngine` isn't ready — exactly the named defect. But on `ecd93ad` this file cannot even import (`ModuleNotFoundError: No module named 'mongo_utils'`; that module plus `prediction_engine.py`/`data_integrity_analyzer.py` exist only under `legacy/dead_frontend/`, and `data.s3_data_source` doesn't exist anywhere). The actually-deployed dashboard (`frontend_pipeline/command_center.py`, via Docker) has no ML/predict endpoints at all. Net: the dangerous pattern exists but is dead-by-breakage today, not silently live — fixing the import without also removing the EMA-fallback-and-trade behavior would reintroduce the defect. |

## Explicit non-actions this session

- No files moved, renamed, or deleted.
- `legacy/` untouched (still importable — Phase 1 gate item, not done).
- `reports/experiments.yaml` untouched; new registry is additive
  (`reports/registry/experiments.jsonl`).
- No `pyproject.toml`/`uv.lock` created yet — doing that before pinning an
  interpreter and running the existing test suites would risk locking in
  the wrong Python version.
