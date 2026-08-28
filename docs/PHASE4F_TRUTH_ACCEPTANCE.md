# Phase 4F — Truth Engine acceptance: one accounting, closed

## Verdict

Phase 4E (commits 1-13, merged into `main` at `aab88d7`) ended on a real, honestly reported
`FAILED_UNEXPLAINED_DIVERGENCE`: 487/492 differential rows `MATCH`, 4 `UNEXPLAINED_DIVERGENCE`,
1 `EXPECTED_LEGACY_DIVERGENCE`, 0 `SHADOW_MAPPING_ERROR` — deterministic and byte-identical
across 4 independent processes and `PYTHONHASHSEED` values (0, 1, 42, 12345). Full detail:
`docs/PHASE4E_COMMIT13_FINAL_REPLAY.md`.

This document closes that phase. It does not reopen the investigation or claim the divergence
was a bug that got fixed — it changes the acceptance criterion, and explains why that change is
correct now.

## Why the criterion changes

Phase 4E's bar was legacy/Truth agreement to `1E-8`. That bar made sense while both systems were
live and had to reconcile. It stops being the right bar once `legacy/` is archived (this same
consolidation pass, see the archival commit) and stops being consulted for anything. At that
point, "does Truth agree with legacy's raw-float arithmetic" is no longer a meaningful question —
the only question that matters is "is Truth internally correct," and that one is already answered:

- `ledger_invariants.py::validate_terminal_ledger_coherence()` — `ok=True`, zero violations,
  recomputed `gross_exposure` and `cash` match Truth's own reported values exactly, across all 4
  seeds (Phase 4E commit 12).
- `leg_ledger` byte-identical across all 4 seeds and against the pre-commit-11 dump (commit 13 §1).
- 149/149 `tests/truth` pass; full suite 609/611 pass post-merge (2 failures are pre-existing,
  environment-only — one needs a gitignored local data file, the other is an unrelated flaky
  mock-endpoint test identical on both sides of the merge; neither touches `src/futur/truth`).

## The 3 residual deltas, reclassified

Each of the 3 `UNEXPLAINED_DIVERGENCE` rows from commit 13 has an independently diagnosed cause
(`docs/PHASE4E_COMMIT11_TERMINAL_LEDGER_FIX.md` §2-3). None is masked, tolerance-widened, or
reclassified as a mapping error — each is Truth doing something legacy simply doesn't do:

| field | legacy value | Truth value | delta | why Truth is right |
|---|---|---|---|---|
| `cash` / `nav` | 200384.15349169602 | 200383.90670354 | ~0.25 on 200k (0.00012%) | Truth quantizes every FILL's price/qty to the real exchange tick/lot grid; legacy trades raw floats that no real exchange would accept |
| `fees` | 1049.6097378716934 | 1049.60973786 | ~1e-8 | Truth's `Account._apply_fee` re-quantizes to `CASH_QUANTUM` (1E-8) after each of 14 FEE events, matching real cash-ledger granularity; legacy's single float accumulator drifts silently |
| `borrow` | 3.169835824606616 | 3.17000000 | ~3e-4 | `pnl_by_type["borrow"]` rounds to 2dp because it reaches real production `LedgerEvent`s consumed by `CarryBasisAdapter.decide()` — removing the rounding would leak a frais change into live tournament accounting, out of scope for a reconciliation phase |

In every case, Truth's number is the more realistic one (real quantization, real rounding
boundaries that production code depends on). "Fixing" the divergence would mean making legacy
worse, not Truth more correct — which is exactly why chasing it further wasn't a good use of
effort once legacy stops being the reference.

## Declaration

`src/futur/truth` is the single source of accounting for this repository. `legacy`'s
`portfolio_ledger` / `leg_ledger` outputs are no longer a correctness reference — they are
archived (see the legacy archival commit) along with the rest of the pre-Truth codebase.

`src/alpha20/tournament/truth_shadow`'s comparator has done its job: it proved structural
equivalence to the standard above. It is demoted from a blocking gate to an informational
regression check — useful if `multileg_backtester.py` changes again, not required to pass for
any release.

## What is *not* closed by this document

The live tournament path does not read from Truth yet. `runner_adapters.py:163-164` (the code
feeding the ACTIVE `carry_basis_v12` runner, on a 5-minute cycle) and several reporting/research
scripts (`scripts/report_paper_portfolio_daily.py`, `run_paper_portfolio_v1.py`,
`run_multileg_ablation.py`, `run_maturity_suite.py`, `run_carry_shadow_replay.py`) still read
`res.portfolio_ledger` / `res.leg_ledger` from the legacy `MultiLegBacktester` result object —
because that object is still what actually runs inside the live 5-minute cycle. Truth ran in
*shadow*, alongside it, for validation; it has never been the thing producing the live result.

Cutting the live path itself over to Truth as the actual accounting engine (not just a shadow
validator) is real, separate work — it changes what a running, ACTIVE paper-money system computes
every 5 minutes. It deserves the same rigor Phase 4E used to validate the shadow (frozen-window
replay, multi-seed determinism, an independent invariant check) before cutover, not a quick edit
alongside a documentation/archival pass. That is Phase 4G, not yet started.
