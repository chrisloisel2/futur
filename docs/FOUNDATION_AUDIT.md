# FOUNDATION_AUDIT

Single source of truth for the pre-rebuild state of the repo, produced
before any deletion or new code, per the rebuild mission's mandatory
ordering (audit first, everything else after). Every fact below was either
executed directly this session or is cited from a specific prior commit /
doc in `docs/v2/`. Nothing here is inferred or estimated without saying so.

**Base:** the mission named a branch `foundation` that does not exist
(checked: local branches, `git branch -a` after `git fetch --all --prune`,
no match on `origin`, no tag, single remote). Per explicit user
confirmation, `v2/foundation` (created 2026-07-27 off `main`'s
`forensic-baseline-2026-07-27` tag, `ecd93ad`, for an earlier "V2 rebuild"
diagnostic program) is being treated as the intended `foundation`. This new
working branch, `rebuild/foundation`, is cut from it at commit `c9c788e`.

This audit **supersedes `docs/v2/*.md` as the current entry point**, but
does not repeat their content wholesale — the diagnostic work in
`EXECUTION_STATE.md`, `PHASE1_DIAGNOSTIC.md`, `MIGRATION.md`, and
`THREAT_MODEL.md` (4 sessions, 2026-07-27/28) is real, verified, and cited
below rather than redone. Those files stay as the detailed evidence trail;
this file is the synthesis the mission asked for, plus the parts that
program never got to (per-strategy classification, duplicate-file
reproducible scan, an explicit keep/remove decision list).

---

## 1. État exact de la branche

```
$ git status
On branch rebuild/foundation (cut from v2/foundation @ c9c788e)
```

**Uncommitted state found, not authored by this session:** 15 files under
`Server/vm-storage/` (Dockerfiles, SFTP session-archival scripts, a Mistral
email uploader, a crontab) are deleted in the working tree but not staged
or committed — `Server/` is now empty on disk. This predates this
conversation turn; it was not produced by any command run here. It is left
exactly as found (not staged, not committed, not restored) — see §7 for why
the evidence independently supports removing it, and §13 for why it is not
being committed as part of this audit-only phase.

```
$ git log --oneline --decorate -20
c9c788e (HEAD -> rebuild/foundation, v2/foundation) v2: close CarryBasisAdapter invariants question
8e03e56 v2: fix orchestrator tests -- bypass deployment guard in no_network fixture
4f4342e v2: correct the live exposure-cap diagnostic -- governor is real, scale isn't applied
344c94b v2: source review -- still blocked on the 2 named files
f454787 (origin/v2/foundation) v2: Phase 1 diagnostic
8ceaaf1 v2: source review addendum -- Audit.txt/Etat-de-l-art.txt not found (blocked)
d5e27b2 v2: Phase 0 forensic freeze + reproducible Phase 1 diagnostic
ecd93ad (tag: forensic-baseline-2026-07-27, origin/main, main) ok
...
```

```
$ git ls-files | wc -l
8523            # tracked files, this commit

$ git ls-files | grep -c '\.py$'
1737            # tracked .py files

$ find legacy -type f | wc -l
7388            # legacy/ is 87% of tracked files, by file count

$ git ls-files -z | xargs -0 du -ch | tail -1
35M             # total tracked-file size (data/ is 305M but mostly gitignored/untracked;
                # 35M is what a `git clone` actually downloads)
```

`origin/main` was up to date at session start; `main` itself was not
touched this session (rule: never work on `main` — respected).

---

## 2. Runtime réellement utilisé

There is no single canonical runtime. Concretely, three things are alive
today:

1. **The ALPHA_20 tournament** (`src/alpha20/`) — the only piece that
   actually places paper decisions on a real, shared, append-only ledger.
   `src/alpha20/tournament/orchestrator.py::run_cycle()` → deployment guard
   → `market_bus` snapshot → per-runner `_run_one()` → 3 runner adapters
   (`runner_adapters.py`) → `global_governor.evaluate()` → `PaperBroker.execute()`
   / direct ledger marks → `PaperAccount`. Config-driven from
   `configs/alpha20_runners.yaml` (the file open in your editor) and
   `configs/alpha20.yaml`. This is the system `docs/v2/PHASE1_DIAGNOSTIC.md`
   traced end-to-end.
2. **`frontend_pipeline/command_center.py`** — the dashboard actually
   deployed via Docker (`docker-compose.yml`'s `command-center` service,
   exposed through an ngrok tunnel). Reads precomputed `reports/`/`data/`
   files only; no ML endpoints, no trading logic.
3. **`research/edge_factory/`** — a disciplined, separate
   preregister → falsify → govern research loop, most recently touched
   2026-07-22, not connected to the live paper path at all except through
   `configs/alpha20_runners.yaml`'s justification comments, which cite its
   verdicts when excluding runners.

Everything else (`core/`, `ai/`, `trading-system/`, `hedge_fund/`,
`production/`, `Server/`, `frontend_pipeline/api_server.py`) is either a
second, divergent copy of similar logic, unreachable, or both — detailed in
§6-7.

---

## 3. Entrypoints

| entrypoint | status |
|---|---|
| `launch.sh` | **broken 3 independent ways**: starts a Docker service `frontend` that doesn't exist (only `command-center` does); falls back to `pip install -r requirements-api.txt`, which doesn't exist at repo root; `docker compose config` itself fails with no `.env` (`NGROK_AUTHTOKEN` interpolation error). (`docs/v2/EXECUTION_STATE.md`, verified 2026-07-27.) |
| `frontend_pipeline/api_server.py` | what `launch.sh` starts as "the API" — **cannot import** (`ModuleNotFoundError: mongo_utils`). Contains the named EMA-fallback-and-trade defect in source, but is dead-by-breakage, not live. (`docs/v2/PHASE1_DIAGNOSTIC.md` §5.) |
| `frontend_pipeline/api_server_paper.py` | imports cleanly, explicit anti-mock policy, but **deployment status unverified** — not referenced by `docker-compose.yml`, `launch.sh`, or any systemd unit. |
| `frontend_pipeline/command_center.py` | the one thing actually running in Docker. Report-reader only, no ML, no trading. |
| `src/alpha20/tournament/orchestrator.py` | the real decision loop — invoked by scripts, not by a CLI. No single `futur`-style command exists anywhere in the tree. |

No root CLI. No `pyproject.toml` at repo root — only `trading-system/pyproject.toml` (`dependencies=[]`, describes a different, second `institutional` package, not the live one).

---

## 4. Dépendances

- No root `pyproject.toml`, `uv.lock`, or `requirements*.txt`. `requirements*.txt` files exist only under `legacy/`.
- 4 reachable Python interpreters on this machine disagree on installed packages; bare `python3` on `PATH` (Homebrew 3.14) has neither `pandas` nor `yaml`.
- Interpreter pinned for all V2/rebuild diagnostic work: `/opt/homebrew/Caskroom/miniconda/base/bin/python3` (3.12.2) — widest coverage, only `lightgbm` missing. (`docs/v2/INTERPRETER.md`.)

---

## 5. Tests exécutables

Re-run this session with the pinned interpreter, unchanged from session 4's numbers (no code touched between then and now):

```
$ python3 -m pytest tests/ -q --continue-on-collection-errors
6 failed, 378 passed, 21 warnings, 4 errors in ~16s

$ cd trading-system && python3 -m pytest tests/ -q --continue-on-collection-errors
5 failed, 4 passed, 7 skipped, 4 errors in ~2.3s
```

`trading-system/tests/` exercises a **second, independently-rooted**
`institutional.*` package (from `trading-system/pyproject.toml`), separate
from root `src/institutional/*` — two divergent copies of similarly-named
engine code, both broken on missing submodules independently. Concrete
evidence for "plusieurs générations incompatibles."

All 6 failures + 4 errors in the root suite are previously root-caused
(`docs/v2/PHASE1_DIAGNOSTIC.md` §1): 4 collection errors + 4 failures are
genuinely-missing `src/institutional/data/*` modules (never existed in git
history at those paths); 2 failures are missing local enriched parquet data
(data-locality, not a code bug — lives on `qbee@100.127.59.114`, not this
machine).

---

## 6. Fichiers dupliqués (reproducible, not a stale number)

Built `scripts/dedupe_scan.py` (content-hash over every `git ls-files`
entry) rather than trust an old cited figure. Regenerate with
`python3 scripts/dedupe_scan.py --write` → `docs/DEDUPE.generated.md`.

```
tracked files scanned: 8523
duplicate groups (2+ identical files): 1190
files that are part of a duplicate group: 4836
```

The largest group (258 files) is entirely under `legacy/dead_reports/` and
`legacy/dead_runs/` — repeated horizon-variant dumps of the same backtest
output (`trades_dataout_v2_h2.json`, `_h4`, `_h8`, ... all byte-identical).
This is a `legacy/` retention-policy problem, not a live-code problem — the
duplication is entirely inside the directory already flagged for
non-importable archival, not spread across active code.

---

## 7. Imports cassés

Confirmed absent from git history at any branch, any path (`git log --all`):

- `src.institutional.data.derivatives`, `.derivatives.features.cross_exchange_features`, `.loaders`, `.news_collector.lexicon`, `.derivatives_collector`, `.dataset_builder`, `.asof_join`, `.data_quality` — `src/institutional/data/` on disk contains only `positioning_archiver.py` and `atomic_parquet.py`.
- `frontend_pipeline/api_server.py`'s `mongo_utils`, `prediction_engine`, `data_integrity_analyzer` — exist only under `legacy/dead_frontend/`, not on that file's `sys.path`. `data.s3_data_source` doesn't exist anywhere, including `legacy/`.

---

## 8. Résultats historiques invalidés

- **V5 walk-forward** (`reports/walk_forward_v5/V5_REPRODUCTION_VERDICT.md`): claimed +5.88%/mo, reproduced at −0.90%/mo median, PF 0.81, **0/5 folds passed**.
- **V1.2 / `carry_basis_v12`** (`reports/parallel_50/PARALLEL_50_V12_VALIDATION.md`, claims +8.6%/yr, PF 1.03): the backtester it was measured with (`multileg_backtester.py`) has a default borrow cost of 1bp/yr (vs. 8%/yr in the paper simulator), spot and perp priced off the same series (basis risk forced to zero), no initial/maintenance margin, no liquidation modeling. Separately (this session's own finding, §9): the exposure limits it's supposed to respect are computed but never enforced. **The published number is not currently trustworthy and should not be treated as evidence of edge.**
- **The overlay `+25.3%/yr`** (`scripts/measure_v12_plus_stack_overlay.py`): multiplies independently-normalized sleeve curves (`combo = combo * x`) as if one shared capital base — each sleeve is actually sized on its own full NAV. Not a shared-capital simulation; invalid as published.
- **Multi-horizon (MH) ensemble scripts**: several (`run_three_engine_wave_portfolio.py`, `measure_v12_plus_stack_overlay.py`, `measure_multihorizon_ensemble.py`, `train_multihorizon_all.py`) rank or threshold scores over the **entire OOS year at once** (`groupby(["engine","year"])["score"].rank(pct=True)`, `np.nanquantile()` over concatenated OOS) — the engine sees the future distribution of the year it's meant to trade causally. Lookahead-contaminated; any PF reported from these paths is unreliable until thresholds are refit train/validation-only and frozen per fold.

---

## 9. Simulations comptablement incorrectes (this session's own finding, verified executable)

`src/institutional/portfolio/invariants.py::InvariantLimits` declares
`max_gross_exposure=1.00` and `max_net_long_exposure=0.75` — but
`check_portfolio_invariants()` computes both exposures and **never compares
them to the limits**; it only raises on hedge-exposure and per-position
checks (naked short, unlinked legs, carry-delta tolerance). Proven with a
test at 2x both caps returning cleanly, no exception
(`tests/test_v2_phase1_live_exposure_cap_diagnostic.py::test_check_portfolio_invariants_never_enforces_its_own_gross_or_net_caps`).

This function is reachable from the **live paper path** exactly once — via
`CarryBasisAdapter.decide()`'s internal `MultiLegBacktester` replay (the
engine behind `carry_basis_v12`, currently `status: ACTIVE` in
`configs/alpha20_runners.yaml`). But even the invariants it does enforce
never reach live capital either: `CarryBasisAdapter.decide()` wraps the
whole replay in a blanket `except Exception`, silently downgrading any
`InvariantViolation` to an ordinary "abstain" ledger event
(`test_carry_basis_adapter_swallows_invariant_violations_as_silent_abstain`).

Four independent, non-unified "max gross exposure" definitions exist across
the codebase (`constraints.py`, `portfolio_backtester.py`, `invariants.py`,
`risk_engine.py`), three genuinely enforced in their own (backtest-only)
call paths, one — the one live-reachable via `carry_basis_v12` — silently
decorative. **Practical consequence: the currently-ACTIVE V1.2 runner has
no live-enforced gross/net exposure cap, and its internal backtest replay's
own risk checks cannot halt anything even when they fire.**

---

## 10. Classification des stratégies

Built from `configs/alpha20_runners.yaml`'s own versioned justification
comments (git-committed, cites specific commits/docs per verdict — treated
as authoritative, not re-derived) plus `research/edge_factory/`'s
preregistration/results docs, cross-checked directly this session where
noted.

| stratégie / runner | classification | évidence |
|---|---|---|
| `carry_basis_v12` (V1.2, BTC+ETH carry Δ-neutre) | **INVALID_ACCOUNTING** | ACTIVE in tournament, but see §8-9: backtester has wrong borrow rate, zero basis risk, no margin/liquidation; its one live-reachable risk check is unenforced and exception-swallowed. Not evidence of edge until Phase 2 accounting fixes land and it's re-measured. |
| `carry_solusdt` | **NO_EDGE** | OBSERVE_ONLY since 2026-07-21; ret/mo +0.14%, DD −20%, PF 0.80 (own file comment, `PORTFOLIO_OS_STATUS.md`). Excluded from selection, execution/telemetry only. |
| `carry_bnbusdt` | **NO_EDGE** | OBSERVE_ONLY since 2026-07-21; ret/mo −0.33%, DD −26%, PF 0.26. Same status as above. |
| `basis_term_v0` (calendar_basis_v1) | **NON_REPRODUCIBLE (overfit)** | OBSERVE_ONLY since 2026-07-21; independent falsification found PBO (CSCV) = 0.47 vs. threshold ≤0.10 — the winning variant was selected post-hoc from 6, indistinguishable from chance despite individually-passing DSR/costs×2/leave-one-out gates. |
| `mh_events_exec` | **CANDIDATE (frozen upstream, not re-evaluated here)** | ACTIVE; reads a frozen, already-shadow-validated multi-horizon consensus model read-only — does not train or touch the shadow. Out of scope for this audit's re-verification. |
| `v1_1_baseline` | **VALIDATED (protected reference)** | EXCLUDED from tournament by explicit mission constraint ("ne touche pas à V1.1"); external, unconcurrenced reference. Not independently re-verified this session. |
| `funding_xvenue_v0` | **NO_EDGE (permanently locked)** | Definitive 2026-07-19; name-pattern-blocked from ever being recycled as a runner. |
| `carry_gate_v2_execution` | **NO_EDGE as execution gate** | Rejected at portfolio level (churn/fees); kept only as a research feature, never as an execution gate. |
| `hyperliquid_metaorders` | **INSUFFICIENT_DATA** | Data collector running since 2026-07-18; H1/H2 protocol preregistered but **no strategy defined yet**. Earliest evaluation ≥2026-08-17 (needs ≥30 days AND ≥300 contemporary book-eligible events). |
| `liquidation_relative_reversal_v1` | **CANDIDATE (in progress)** | Preregistered (`research/edge_factory/liquidation_relative_reversal_v1/PREREGISTRATION.md`); per prior session, blocked on real `aggTrades`/spot-5min/L2 data — current `taker_buy_*` columns are largely placeholders (half of volume). Not yet testable end-to-end. |
| `momentum_tokenized_macro_v1` | **NOT_STARTED (deprioritized by design)** | Preregistered as a research hypothesis, explicitly deprioritized (smaller universe, needs its own macro-beta neutralization) — not yet tested, not a rejected result. |
| `cross_sectional_momentum_crypto_v1` | **NO_EDGE (frozen)** | `CLOSED_NO_EDGE` governance freeze 2026-07-21 (reversal dominant, not momentum). |
| `ctrend_v0_v1` | **LOOKAHEAD** | `CTREND_REJECTED` — the positive v0 result was survivorship bias (non-point-in-time universe). |
| `options_positioning_all_variants` | **NO_EDGE** | `NO_EDGE_DEFINITIF` across all tested variants (OTM flow, 4h, daily). |
| `stablecoin_regime_overlay` | **NO_EDGE** | Statistical leg passed, sizing rule rejected — not re-tunable per governance. |
| `funding_extreme_level` (basis_dispersion) | **NO_EDGE** | `NO_INCREMENTAL_EDGE` (PHASE3_FREEZE) — extreme funding = continuation, not reversal. |
| `funding_relative_value_cross_venue` | **NO_EDGE** | `CLOSED_NO_EDGE` 2026-07-21, both Binance↔Bybit and Binance↔Hyperliquid legs. |
| `protocol_fundamentals` | **NO_EDGE** | Negative spread despite favorable survivorship bias. |
| `onchain_flows` | **INSUFFICIENT_DATA** | `NOT_TESTABLE` — whale Mongo empty, no historical CEX flow data. |
| `top_traders_divergence` | **NO_EDGE** | Effect arbitraged away post-2024 (t=−0.75). |
| `liquidation_exhaustion` | **NO_EDGE** | CVD confirmation flips the setup's sign. |
| `legacy_ml_fleet_trm` (Level 0-7 ML cascade) | **ARCHIVED (protected, out of scope)** | The existing shadow system the mission explicitly forbids touching or reconstructing. |
| `l2_execution`, `lst_pegs`, `multileg_engine` (edge_factory dirs) | **REQUIRES_RETEST (not read this session)** | Directories exist with no verdict cross-checked this pass — flag for a dedicated per-hypothesis review before Phase 8/9 (strategy reproduction/closure), don't assume either way. |

**13 entries in `reports/registry/experiments.jsonl`** are a distinct,
older layer (May 2026, TRM/LightGBM ML-fleet-era experiments — `incubate`/
`reject`/`promote`/`retire` decisions) migrated verbatim from
`reports/experiments.yaml`, not yet reconciled with the classification
above. Phase 6 (registre expérimental) should absorb both into one schema,
not this audit.

---

## 11. Composants à conserver (avec raison factuelle)

- `research/edge_factory/` — real preregister → falsify → govern discipline, produces the classification table above; the one part of the repo already doing what the mission asks for.
- `src/alpha20/` — the event-sourced ledger, governor, and paper-account design pattern is sound (append-only, decision events carry full context); needs the fixes in §9, not a rewrite.
- `src/institutional/` engine and backtest logic (`multileg_backtester.py`'s cost/funding/regime-gate machinery) — real, tested logic, needs its accounting corrected (§9), not replaced.
- `configs/alpha20_runners.yaml` / `configs/alpha20_selection_protocol.yaml` — exactly the kind of versioned, justified, auditable config the mission wants; the selection protocol YAML is a target contract Phase 3 should make executable, not redesign.
- `docs/v2/*.md` — the evidence trail this audit builds on; keep as history, don't re-derive.
- `reports/`, `data/` — append-only, explicitly protected by every prior session's rules.

## 12. Composants à supprimer ou archiver (avec raison factuelle)

- `Server/vm-storage/` — already deleted uncommitted in the working tree (§1). Independent evidence supports this: `Server/` was already `UNVERIFIED`/zero-references in `docs/v2/MIGRATION.md`, its content (SFTP session archival, Mistral email upload) is unrelated to trading, and its last real git touch (2026-06-14) predates the trading runtime's active work. **Recommendation: commit this deletion as the first, isolated Phase 2 commit** — not part of this audit commit, and not silently absorbed.
- `legacy/` (7388 files, 87% of the tree by count, 78M) — self-declared, last touched 2026-05-27, zero references from active dirs, contains the largest duplicate-file cluster (§6). Phase 1 gate: make non-importable, don't delete (ledger/history value).
- `trading-system/` — second, divergently-broken `institutional.*` package (§5); duplicate of `src/institutional/`, not a superset. Needs a diff against root `src/institutional/`, not a merge-by-copy.
- `frontend_pipeline/api_server.py` — broken import chain (§7) hiding a real EMA-fallback-and-trade defect (§8 mission-prohibited pattern). Repair-vs-retire is a named design decision, not a delete-on-sight — do not repair the import without also removing the fallback-and-trade behavior.
- `Server/` (remainder), `production/`, `hedge_fund/`, `deploy/`, `bin/` — `UNVERIFIED` in `docs/v2/MIGRATION.md`, no static references found from active dirs; needs the second-pass per-file check that session never got to (dynamic imports, subprocess calls, systemd units) before final deletion, but no evidence found yet that any of it is load-bearing.
- `ai/`, `core/`, `config/`, `risk/`, `data_pipeline/` — still imported by `scripts/`, so not dead, but substantially overlap `src/institutional/`/`src/alpha20/` in purpose; needs consolidation into the target `src/futur/` layout, not deletion-on-sight.

---

## 13. Blocages

- `project_sources/01-Audit.txt` / `02-Etat-de-l-art.txt` — named by an earlier version of this mission, searched for across 3 separate prior sessions (`mdfind`, full home `find`, Downloads/Desktop/Documents), **confirmed absent from this machine**. Not re-searched this session — ask directly if source review is needed, don't re-run a 4th identical search.
- **The `Server/vm-storage/` deletion (§1) is not committed by this audit.** It sits in the working tree, matches what Phase 2 would independently conclude, but committing someone else's uncommitted change inside an audit-only commit would blur "audit" and "suppression du code mort" into one commit, against the mission's own rule against mixing concerns. It will be the first action of the next phase, with its own justification.
- No `pyproject.toml`/lockfile yet exists — Phase 1 (base Python minimale) item, correctly not started before this audit.

---

## 14. Plan de migration minimal (proposition)

Target layout, following the mission's indicative structure — no separation
added beyond what's already justified by the distinctions found in this
audit (accounting vs. execution vs. risk are already separate concerns in
the current `src/institutional`/`src/alpha20` split, worth keeping):

```
pyproject.toml
uv.lock
README.md
configs/
src/futur/
  cli.py
  domain.py
  data.py
  accounting.py      # absorbs invariants.py + constraints.py + portfolio_backtester's
                      # exposure logic + risk_engine.py's — one canonical implementation (§9)
  execution.py        # absorbs paper_broker.py, runner_adapters.py's routing
  risk.py              # absorbs global_governor.py, wires GovernorDecision.scale (pending
                        # your design decision, docs/v2/PHASE1_DIAGNOSTIC.md "Next minimal
                        # modification")
  backtest.py          # absorbs multileg_backtester.py once accounting is fixed
  research.py           # thin wrapper around research/edge_factory's existing discipline
  runtime.py             # orchestrator.py, market_bus.py
tests/
reports/
research/edge_factory/   # kept as-is, not moved into src/futur/
legacy/                    # non-importable archive
```

Not proposed for this pass: exact file-by-file move list — that's Phase 2's
job, informed by §12's directory-level verdicts.

---

## Prochaine action exacte

Phase 2 (suppression du code mort), as its own small, isolated commit(s),
per the mission's explicit ordering and "don't mix concerns" rule:
1. Commit the already-present `Server/vm-storage/` deletion, with its own justification (§12).
2. Make `legacy/` non-importable (not deleted).
3. Get your explicit decision on the two items already flagged as blocked on you from the prior diagnostic program (`docs/v2/PHASE1_DIAGNOSTIC.md`'s "Next minimal modification" items 2-3): wire `GovernorDecision.scale` into execution (yes/no), and repair-vs-retire `frontend_pipeline/api_server.py`.
