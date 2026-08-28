# Phase 4 provenance correction (Phase 4B, commit 1)

## What this corrects

The final deliverable report at the end of the Phase 4 (Truth Engine)
conversation turn contained a real error: it listed

```
Commit de départ: 5c4d300~11 = 78e8193 de la Phase 3 (fin de la phase précédente)
```

This is wrong on both counts, and this file is the correction, per your
instruction to fix "uniquement la documentation erronée" -- the git
history itself needed no repair (verified below, and confirmed: this
error was never written into any tracked file, including
`docs/TRUTH_ACCOUNTING.md` -- it existed only in that turn's chat-only
summary text).

## Commands actually run, and their real output

```
$ git status --short --branch
## rebuild/foundation

$ git rev-parse HEAD
5c4d3005f628621d1f063f48d65e93fe790ea662

$ git rev-parse 5c4d300~11
9a8be6be2240bd56050a5ff2867d9e1d61b778eb

$ git rev-list --first-parent --reverse 78e8193..5c4d300
410a1decbe150659d77668df60f213858b3c170a
5c4d3005f628621d1f063f48d65e93fe790ea662

$ git log --graph --decorate --oneline -20
* 5c4d300 (HEAD -> rebuild/foundation) docs: document truth accounting conventions
* 410a1de tests: add truth engine property tests
* 78e8193 cli: expose truth replay and validation
* bc8863f truth: add reconciliation
* 486ac70 truth: add deterministic replay
* 0bab682 truth: enforce accounting invariants
* dd16484 truth: implement exposure and margin model
* 88f4fa3 truth: implement perpetual accounting
* 6e77723 truth: implement spot accounting
* 3c3f6f8 truth: add append-only ledger
* 4cac134 truth: add canonical event and order domain
* 9a8be6b docs: document clean-clone workflow
* 820b0a6 build: fix dependency-manifest gaps found during clean-environment verification
* 098b641 cleanup: retire competing Python manifests from runtime
* 3b00a48 tests: replace local-data dependencies with synthetic fixtures
* 0887a3f tests: isolate canonical test suite
* 8afa097 cli: add canonical futur entrypoint
* c9c9c80 build: add locked reproducible environment
* 2ef7b21 build: add canonical root Python manifest
* 4b49598 rebuild: gate test -- src/ must never import legacy, frontend_pipeline, or trading-system
```

## The actual facts

- `5c4d300~11` is **`9a8be6b`** ("docs: document clean-clone workflow") --
  the real last commit of Phase 3, correctly 11 commits behind HEAD.
- `78e8193` ("cli: expose truth replay and validation") is **`5c4d300~2`**
  -- it is commit 9 *of Phase 4's own 11 commits*, not a Phase 3 commit
  and not the start-of-Phase-4 boundary. `git rev-list --first-parent
  --reverse 78e8193..5c4d300` above shows exactly what's really after it:
  only 2 commits (`410a1de`, `5c4d300`), confirming it sits near the *end*
  of the Phase 4 sequence, not the beginning.
- The graph is linear (`git log --graph` shows a single line, no merge
  commits, no branching) and `git status --short --branch` shows a clean
  working tree on `rebuild/foundation` with nothing uncommitted. Nothing
  here needed rebasing or rewriting -- confirmed, not assumed -- so none
  was done, per your explicit instruction.

## Root cause

A transcription error when hand-building the summary table at the end of
that turn: the two commit references (`5c4d300~11`, the intended "Phase 3
boundary" citation, and `78e8193`, one of the actual Phase 4 commit hashes
listed earlier in the same table) got paired incorrectly when the table
was written. It was never verified against a fresh `git rev-parse` before
being reported -- this file exists specifically because that verification
step was skipped then and is not being skipped now.

## Verdict correction

The previous turn's final verdict, `TRUTH_ENGINE_ACCOUNTING_VALIDATED`, is
withdrawn. It was premature: it was reported before the arithmetic model
(float, not yet Decimal), ledger durability, and independent
(engine-blind) validation work Phase 4B covers had been done or even
scoped. Replaced with:

```
TRUTH_ENGINE_SYNTHETIC_SELF_CONSISTENCY_PASS
```

This means only: the 503 tests passing at the end of Phase 4 show the
engine is *internally self-consistent* against its own synthetic fixtures
and its own invariant checks. It does **not** mean `ACCOUNTING_VALIDATED`
-- that stronger claim requires (and is what the rest of Phase 4B is for):
exact quantized arithmetic instead of float, a durable/adversarially-tested
ledger, and reference fixtures whose expected values were computed by hand
and checked against an oracle that imports no production reducer code.
