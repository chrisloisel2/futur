# V2 Phase 1 Diagnostic (2026-07-27)

Diagnostic only, per the session's explicit instruction: nothing here was
fixed. All commands below were actually executed with the interpreter
pinned in `docs/v2/INTERPRETER.md`
(`/opt/homebrew/Caskroom/miniconda/base/bin/python3`, 3.12.2). Repo state:
branch `v2/foundation`, starting commit `8ceaaf1` (source-review addendum),
itself on top of `d5e27b2` / forensic tag `forensic-baseline-2026-07-27` at
`ecd93ad`.

## 1. `tests/` (root suite)

```
$ python3 -m pytest tests/ -ra --tb=short -q
... 4 errors during collection, pytest aborts (exit 2) before running anything
```

Default pytest behavior aborts the whole run on collection errors. Re-ran
with `--continue-on-collection-errors` to get a full picture in one pass:

```
$ python3 -m pytest tests/ -ra --tb=short -q --continue-on-collection-errors
9 failed, 361 passed, 21 warnings, 4 errors in 14.98s   (exit 1)
```

**4 collection errors — genuinely missing modules, not an environment
problem:**

| test file | imports | verified absent |
|---|---|---|
| `tests/test_cross_exchange.py` | `src.institutional.data.derivatives` | not in git history on any branch (`git log --all`), not under `legacy/` |
| `tests/test_cross_exchange_funding_edge.py` | `src.institutional.data.derivatives.features.cross_exchange_features` | same |
| `tests/test_fold_aware_loader.py` | (via `scripts/run_backtest_engine.py`) `src.institutional.data.loaders` | same |
| `tests/test_news_collector.py` | `src.institutional.data.news_collector.lexicon` | same |

`src/institutional/data/` on disk contains only `positioning_archiver.py`
and `atomic_parquet.py` — these four modules were never committed at this
path on this repo, on any branch.

**9 failures, three distinct root causes:**

- **4×** `tests/test_derivatives_collector.py` — same missing-module cause
  as above (`src.institutional.data.derivatives_collector`), but the import
  is inside the test function rather than at module top level, so pytest
  collects the file and fails each test individually instead of one
  collection error.
- **3×** `tests/test_alpha20_tournament_orchestrator.py` — `SystemExit: 2`
  raised by `src/alpha20/deployment_guard.py:60`, message: *"DEPLOYMENT
  GUARD — aucun manifeste approuvé trouvé
  (configs/DEPLOYMENT_MANIFEST.json). Démarrage refusé."* This is the
  fail-closed deployment-drift guard (git log `eb94ddf`) doing exactly what
  it's designed to do — there is no approved manifest file in this checkout
  — but these 3 tests don't mock/bypass it, so they fail rather than
  exercising the orchestrator behavior they're named for. **Not a bug in
  the guard; a test-fixture gap** (the tests need an approved-manifest
  fixture, not a guard bypass).
- **2×** `tests/test_hedge_governor_backtest.py`,
  `tests/test_portfolio_multileg.py` — `ValueError: no prices`, caused by
  `data/enriched/BTCUSDT_1h_enriched.parquet` and `ETHUSDT_1h_enriched.parquet`
  being absent on this machine. Consistent with prior-session memory: this
  Mac has no local enriched historical dataset (it lives on
  `qbee@100.127.59.114`). **Data-locality gap, not a code bug.**

## 2. `trading-system/tests/`

```
$ cd trading-system && python3 -m pytest tests/ -ra --tb=short -q --continue-on-collection-errors
collected 16 items / 4 errors
5 failed, 4 passed, 7 skipped, 4 errors in 2.75s   (exit 1)
```

All 4 collection errors and 3 of the 5 failures are `ModuleNotFoundError:
No module named 'institutional.data'` — a **different package root** than
the primary suite (`institutional.*`, from `trading-system/pyproject.toml`'s
`institutional` package, vs. `src.institutional.*` at repo root). One
failure (`test_partial_failed_message_when_asset_fails`) chains into the
root-level `scripts/build_engine_datasets.py`, which itself fails on
`src.institutional.data.dataset_builder` — another genuinely-missing module,
same pattern as section 1. 7 skips are explicit ("Données enriched BTC/ETH
absentes") — same data-locality gap as above, handled with a real skip
rather than a silent pass, which is the correct behavior.

This confirms two independent, differently-rooted `institutional` packages
exist in the repo (root `src/institutional/` vs.
`trading-system/src/institutional/`, imported as `institutional.*` from
that subproject's own `pyproject.toml`) — consistent with the
`MIGRATION.md` UNVERIFIED classification of `trading-system/` and a
concrete instance of the master prompt's "generations incompatibles" claim.

## 3. Live/paper decision path: `src/alpha20` → exposure control → order

Traced by reading, then confirmed by the executable test in section 4.

```mermaid
flowchart TD
    A["orchestrator.run_cycle()"] --> B["assert_paper_only() (guard.py)"]
    B --> C["assert_deployment_matches_approved() (deployment_guard.py)\nFAILS CLOSED — SystemExit(2) if no approved\nconfigs/DEPLOYMENT_MANIFEST.json"]
    C --> D["runnable_specs() — load ACTIVE/OBSERVE_ONLY runners"]
    D --> E["market_bus.build_snapshot() — one shared cutoff for all runners"]
    E --> F["per runner, isolated thread, hard timeout:\n_run_one(spec, snapshot, broker)"]
    F --> G["PaperAccount.evaluate_risk(gross_usdt, net_delta_usdt, venue_unsecured_frac)"]
    G --> H["PaperAccount.risk_metrics(...)\ngross_usdt used ONLY as:\nmargin_used = gross_usdt * 0.10 / nav\n(fixed 10% IM proxy — NOT a direct gross/NAV ratio check)"]
    H --> I["global_governor.evaluate(metrics, profile=ALPHA20_LOW_RISK)\nchecks: dd_kill/dd_cash/dd_reduce, daily_loss,\nweekly_loss, es99_1d, net_delta_cap=0.05,\nmargin_used_cap=0.20, venue_unsecured_cap=0.15,\nnaked_leg_max_s"]
    I --> J["GovernorDecision(state, scale, reasons)"]
    J --> K["adapter.decide(snapshot, broker, state, decision.state)"]
    K --> L["account.emit(events) → append-only ledger"]
    L --> M["account.mark(nav) + save_state()"]

    N["src.institutional.portfolio.constraints.PortfolioConstraints\n(literal max_gross_exposure, max_net_long_exposure)"] -.not on this call graph.-x G
    O["src.institutional.portfolio.invariants.check_portfolio_invariants\n(naked-short, carry-delta checks)"] -.only reached transitively,\nvia CarryBasisAdapter.decide()\nre-running MultiLegBacktester\nfor signal generation — not as\na live capital-allocation gate.-> K
```

Key facts behind the diagram (all confirmed by reading the actual code, not
inferred):

- `src/alpha20/tournament/paper_account.py::risk_metrics` computes
  `margin_used = gross_usdt * 0.10 / nav` — the *only* place `gross_usdt`
  is used. There is no `gross_usdt / nav <= max_gross_exposure` comparison
  anywhere in `src/alpha20`.
- `src/alpha20/risk/global_governor.py::evaluate` reads
  `configs/alpha20.yaml`'s `ALPHA20_LOW_RISK` profile: `net_delta_cap: 0.05`,
  `margin_used_cap: 0.20`, `venue_unsecured_cap: 0.15`, plus
  drawdown/daily/weekly/ES99 checks. None of these keys are named
  `max_gross_exposure` or `max_net_long_exposure`.
- `grep -rn "institutional" src/alpha20/**/*.py` shows exactly 3 import
  sites, all for market data or engine-building
  (`src.institutional.live.paper_portfolio`,
  `src.institutional.engines.legacy_bridge`,
  `src.institutional.engines.registry`,
  `src.institutional.backtest.multileg_backtester`) — never
  `src.institutional.portfolio.constraints` or `...invariants` directly.
- `src/institutional/portfolio/constraints.py::PortfolioConstraints.check()`
  has exactly one caller in the whole tree:
  `src/institutional/portfolio/meta_allocator.py` — not reached from
  `src/alpha20` at all.
- `check_portfolio_invariants()` (the naked-short / carry-delta invariant)
  **is** transitively reachable from the live path, but only through
  `CarryBasisAdapter.decide()` (one of 3 adapter classes in
  `runner_adapters.py`; the other two, `BasisTermAdapter` and
  `MHEventsAdapter`, were not individually traced this session) calling
  into `MultiLegBacktester`, which is a *signal-generation* replay, not a
  portfolio-level cap on the runner's actual paper capital.

## 4. Executable proof: do the named caps block a paper/live decision?

New test: `tests/test_v2_phase1_live_exposure_cap_diagnostic.py`, run
directly against `PaperAccount.evaluate_risk` — the exact call
`orchestrator._run_one` makes.

```
$ python3 -m pytest tests/test_v2_phase1_live_exposure_cap_diagnostic.py -v
test_150pct_gross_exposure_is_not_blocked_by_the_live_governor PASSED
test_10pct_net_delta_does_trigger_a_real_but_differently_named_cap PASSED
2 passed in 0.61s
```

- **150% gross/NAV** (breaches both the historical named caps: 0.75 in
  `configs/portfolio_v1_1_parallel_50.yaml`, 1.00 in
  `src/institutional/portfolio/invariants.py`) → live governor returns
  `state="risk_on"`, no reason fires. **The order is not blocked.**
- **10% net delta** (well under the historical named 0.50 cap) → live
  governor returns `state="risk_reduced"`, `reasons={"net_delta": ...}`.
  **A real, different, much tighter (5% NAV) cap does fire** — the live
  path is not unmanaged, it just doesn't use the `max_gross_exposure` /
  `max_net_long_exposure` names or thresholds anywhere.

### Verdict: `max_gross_exposure` / `max_net_long_exposure` — **CONFIRMED (defect reproduced)**

As literally named in the master prompt and in
`configs/portfolio_v1_1_parallel_50.yaml` /
`src/institutional/portfolio/invariants.py`, these two caps do **not** gate
the ALPHA_20 live/paper decision path. The path is not unguarded — a
differently-named, differently-thresholded governor (`global_governor.py`)
is real, configured, and executes correctly on a 5% net-delta breach — but
gross exposure specifically is capped only via a fixed 10%-IM proxy
(`margin_used`), which is ~2.5x looser than the historical 0.75/1.00
named caps on a pure gross/NAV basis. This corrects last session's
"UNVERIFIED" into a confirmed, precise finding.

## 5. ML endpoint probe: real data vs. EMA fallback vs. mocks

Three FastAPI surfaces exist under `frontend_pipeline/`:

| module | how it's started | import result | ML/predict endpoint? |
|---|---|---|---|
| `command_center.py` | Docker `command-center` service (`docker-compose.yml`, actually deployed) | **imports cleanly** (`uvicorn frontend_pipeline.command_center:app`) | No `predict`/`signal`/`model` routes found among its 22 endpoints — reads precomputed `reports/`/`data/` files, no live inference. |
| `api_server_paper.py` | not referenced by `docker-compose.yml`, `launch.sh`, or any unit in `deploy/systemd/` — deployment status **UNVERIFIED** from this repo alone | **imports cleanly**, mounts `portfolio_ops_api`'s router | `portfolio_ops_api.py` carries an explicit stated policy: *"jamais de mock. Si une source est absente → {"status": "disabled"}"* — good design, not independently exercised against a live source this session. |
| `api_server.py` | `launch.sh` explicitly starts this as "the API" (`cd frontend_pipeline && exec python api_server.py`, port 8000) | **confirmed BROKEN — cannot import**: `ModuleNotFoundError: No module named 'mongo_utils'` | **Yes — and this is the one with the EMA fallback.** `_run_autonomous_trade()` (an autonomous loop firing every 60s once started) uses `_prediction_engine.last_prediction` when ready, else silently falls back to a naive EMA 7/25 crossover on Binance klines (`_fetch_ema_signal`) and **still executes a paper trade on that fallback signal** — exactly the pattern the master prompt names as prohibited ("un composant manquant doit échouer explicitement, jamais lancer silencieusement une EMA"). |

Executed, not inferred:

```
$ python3 -c "import frontend_pipeline.command_center as cc; print(cc.app)"
IMPORT OK

$ python3 -c "import api_server_paper; print(api_server_paper.app)"   # run from frontend_pipeline/
IMPORT OK

$ python3 -c "import api_server"   # run from frontend_pipeline/, sys.path set exactly как launch.sh sets it
ModuleNotFoundError: No module named 'mongo_utils'
```

`mongo_utils.py`, `prediction_engine.py`, and `data_integrity_analyzer.py` —
all imported by `api_server.py` at module load time — exist **only** under
`legacy/dead_frontend/`, which is not on `api_server.py`'s `sys.path`.
`data.s3_data_source` (also imported there) does not exist anywhere in the
repo, including `legacy/`.

### Verdict: mock/EMA-fallback endpoint — **CONFIRMED to exist in source, but currently BROKEN (cannot run)**

The dangerous pattern is real and would be live-reachable if `api_server.py`
could start — but on current HEAD it cannot: it crashes on import before
the FastAPI app object is even constructed, because 3 of its dependencies
were apparently moved to `legacy/dead_frontend/` without updating this
file's imports (git history shows no prior commit at the expected paths
either — see `docs/v2/MIGRATION.md`). Net effect: as of `ecd93ad`, nobody
running `launch.sh` gets a working API on port 8000 at all, so the
EMA-fallback autonomous-trading code is dead-by-breakage rather than
silently active. This is not a clean bill of health — it just means the
specific risk (autonomous EMA-based paper trading executing unnoticed) is
currently blocked by an unrelated, also-broken import chain, not by any
deliberate safeguard. Fixing the import without also removing/gating the
EMA-fallback-and-trade behavior would re-introduce the named defect.

`command_center.py` (what's actually deployed via Docker) does not have
this pattern — it has no ML/predict endpoints at all, only report readers.

## Next minimal modification (deliberately not done yet)

Do **not** fix the broken imports or the exposure-cap gap in this pass —
Phase 1's rule was to record the diagnostic first. The smallest next change,
in order:

1. Add a fixture/mock for `configs/DEPLOYMENT_MANIFEST.json` (or a
   `monkeypatch` of `assert_deployment_matches_approved`) to the 3
   `test_alpha20_tournament_orchestrator.py` tests so they exercise
   orchestrator behavior instead of failing on the (correctly-functioning)
   deployment guard — smallest, safest, zero behavior-changing fix in the
   whole diagnostic.
2. Separately and explicitly: decide whether `frontend_pipeline/api_server.py`
   should be repaired (restore/relocate `mongo_utils.py`,
   `prediction_engine.py`, `data_integrity_analyzer.py`,
   `data/s3_data_source.py`) or retired — repairing it without also removing
   the silent-EMA-fallback-and-trade behavior would reintroduce a named
   defect, so this is a design decision, not a one-line fix, and should not
   be bundled with anything else.
