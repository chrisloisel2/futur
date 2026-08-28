# Phase 4E — commit 13: strictly identical Phase 4D replay, final verdict

Replays exactly what commit 10 froze: `carry_basis_v12`, `venue=binance_usdm`,
`2026-05-29` → `2026-07-28T21:00:00Z`, the same enriched data (same SHA-256s),
the same real ProductSpec registry, the same runner configuration, the same
frozen-window rule, the same signals/costs/sizing, the same
`ToleranceConfig` (unmodified default, `1E-8`). No new period was selected;
no comparator behavior was changed to manufacture a PASS.

## 1. Four independent processes, four `PYTHONHASHSEED` values

```
$ for SEED in 0 1 42 12345; do
    PYTHONHASHSEED=$SEED uv run python3 scripts/run_carry_shadow_replay.py \
      > seed_${SEED}_stdout.json
  done
```

SHA-256 of each run's stdout report, differential JSONL, and replay-report
JSON:

| artifact | seed 0 | seed 1 | seed 42 | seed 12345 |
|---|---|---|---|---|
| stdout report | `92e9dbb9...d0628` | same | same | same |
| `carry_shadow_differential.jsonl` | `b2dc55b8...6bd21` | same | same | same |
| `carry_shadow_replay_report.json` | `b7ff8899...decfd2` | same | same | same |

All three artifacts are byte-for-byte identical across all four hash seeds.

## 2. Full replay-report content (identical across all four seeds)

```json
{
  "provenance": {
    "historical_experiment_commit": "2fe693b",
    "registry_config_hash": "9e025f4590c1dd39aec94210",
    "effective_config_sha256": "9a54145d805e2e93bb15f35c7c948c12bef33d80bec5e8de369fa72251fe861d"
  },
  "window": {"paper_start": "2026-05-29", "end": "2026-07-28T21:00:00Z", "n_decide_calls": 1},
  "legacy_identical_shadow_on_vs_off": true,
  "coverage_counts": {
    "spot_leg_opened": 7, "perp_leg_opened": 7,
    "spot_mark": 80, "perp_mark": 80,
    "funding": 7, "fee": 14, "reduction_or_close": 14, "terminal_close": 4
  },
  "missing_coverage": [],
  "differential": {
    "n_rows": 492,
    "classification_counts": {"MATCH": 487, "UNEXPLAINED_DIVERGENCE": 4, "EXPECTED_LEGACY_DIVERGENCE": 1}
  },
  "verdict": "FAILED_UNEXPLAINED_DIVERGENCE",
  "cause": "4 UNEXPLAINED_DIVERGENCE rows"
}
```

All four coverage-completeness requirements hold exactly: 7 spot opens, 7
perp opens, 80 spot MARK, 80 perp MARK, 7 funding, 14 fees, 14
reductions/closes, 4 terminal closes -- unchanged from commit 10's frozen
baseline (coverage did not shrink). Provenance identifiers
(`historical_experiment_commit`, `registry_config_hash`,
`effective_config_sha256`) are identical to commit 10's freeze -- proves
the config/data actually used is unchanged, not merely asserted.

## 3. The 4 `UNEXPLAINED_DIVERGENCE` rows and the 1 `EXPECTED_LEGACY_DIVERGENCE`

All at the single terminal event (`cycle-1-borrow-2026-07-28`):

| field | classification | legacy | truth | cause |
|---|---|---|---|---|
| `margin_used` | `EXPECTED_LEGACY_DIVERGENCE` | N/A | 0E-8 | legacy has no margin model at all -- pre-existing, documented, unrelated to this phase |
| `cash` | `UNEXPLAINED_DIVERGENCE` | 200384.15349169602 | 200383.90670354 | Truth quantizes every FILL's price/qty to the real exchange tick/lot grid; legacy trades raw floats. §2 of `PHASE4E_COMMIT11_TERMINAL_LEDGER_FIX.md` |
| `nav` | `UNEXPLAINED_DIVERGENCE` | 200384.15349169602 | 200383.90670354 | same root cause as `cash` (nav = cash here, no unrealized left) |
| `fees` | `UNEXPLAINED_DIVERGENCE` | 1049.6097378716934 | 1049.60973786 | Truth's `Account._apply_fee` re-quantizes to `CASH_QUANTUM` (1E-8) after each of 14 FEE events; legacy's single float accumulator doesn't |
| `borrow` | `UNEXPLAINED_DIVERGENCE` | 3.169835824606616 | 3.17000000 | `pnl_by_type["borrow"]` rounds to 2dp for `CarryBasisAdapter.decide()`'s real production events (§3 of the commit 11 doc explains why that rounding could not be removed) |

`gross_exposure` and `net_exposure` -- the two fields Phase 4D commit 9's
bug actually broke -- are both `MATCH` (487 of 492 rows, up from commit 9's
486; `gross_exposure` moved from `UNEXPLAINED_DIVERGENCE` to `MATCH`,
`net_exposure` already matched). Zero `SHADOW_MAPPING_ERROR` rows.

## 4. Requirements checklist

| requirement | result |
|---|---|
| hashes strictly identical (4 `PYTHONHASHSEED` values) | **yes** -- §1 |
| legacy identical with shadow on vs. off | **yes** -- `legacy_identical_shadow_on_vs_off: true`, all 4 runs |
| `leg_ledger` identical before/after correction | **yes** -- diffed byte-for-byte against the pre-fix dump captured in commit 11 (`before.leg_ledger.csv`); also byte-identical across all 4 seeds this commit |
| inter-ledger invariant valid | **yes** -- `src/institutional/backtest/ledger_invariants.py`'s `validate_terminal_ledger_coherence()` run against this exact replay's result, all 4 seeds: `ok=True`, zero violations, recomputed `gross_exposure=0.0` matching leg_ledger, recomputed cash matching reported cash exactly |
| coverage complete | **yes** -- §2, unchanged from commit 10 |
| zero mapping error | **yes** -- 0 `SHADOW_MAPPING_ERROR` |
| zero unexplained divergence | **no** -- 4 remain (`cash`, `nav`, `fees`, `borrow`); see §3 for each one's independently-verified, non-residual-close-related cause |

Note on scope: "inter-ledger invariant valid" above is commit 12's
validator confirming `portfolio_ledger` and `leg_ledger` -- the two
LEGACY outputs -- agree with each other. It is a different, narrower
claim than legacy-vs-Truth agreement (the shadow comparator's job, §3's
table). The former is fully achieved; the latter is not, for the three
independently-diagnosed reasons in §3, none of which involve the
terminal-ledger ordering bug this phase's mandate covers.

## 5. Gates

```
$ uv sync --frozen
Audited 39 packages in 10ms

$ uv run pytest tests/truth
149 passed

$ uv run pytest --ignore=tests/future/test_fold_aware_loader.py
613 passed
(tests/future/test_fold_aware_loader.py: pre-existing ModuleNotFoundError,
 confirmed present identically before Phase 4E's first commit -- unrelated)

$ uv run ruff check src/futur/truth tests/truth src/alpha20/tournament/truth_shadow src/institutional/backtest
src/futur/truth: All checks passed!
tests/truth: All checks passed!
src/alpha20/tournament/truth_shadow: All checks passed!
src/institutional/backtest: 150 errors (125 pre-existing in event_backtester.py/
  portfolio_backtester.py/walk_forward.py/metrics.py, none touched by Phase 4E;
  25 pre-existing in multileg_backtester.py, identical count before/after this
  phase's own changes, confirmed by diffing against commit ec5c211; 0 in the
  new ledger_invariants.py)

$ uv run mypy --follow-imports=silent src/futur/truth src/alpha20/tournament/truth_shadow
Success: no issues found in 12 source files
Success: no issues found in 5 source files

$ uv run python -m compileall src
(clean, exit 0)
```

## 6. Verdict

```
FAILED_UNEXPLAINED_DIVERGENCE
```

The terminal-ledger ordering bug Phase 4E was chartered to fix
(`gross_exposure`/`net_exposure` disagreeing between `portfolio_ledger`
and `leg_ledger`) is fixed, proven fixed by a minimal reproducing test
(commit 11) and an independent cross-ledger validator that would catch a
recurrence (commit 12), and confirmed fixed in the real frozen-window
replay above -- deterministically, across four independent processes and
hash seeds. `TRUTH_ENGINE_CARRY_SHADOW_VALIDATED` is not reported: 4 of
the 5 originally-bundled terminal-event divergences turned out, on direct
investigation, to have three separate causes with no fix available inside
this phase's scope (`multileg_backtester.py` only, no signal/sizing/
frais/leg_ledger/tolerance changes, no TruthEngine changes) --
`cash`/`nav` (Truth's real-exchange-grid fill quantization vs legacy's
raw floats), `fees` (Truth's own per-event `CASH_QUANTUM` requantization
drift), and `borrow` (a 2dp rounding in `pnl_by_type` that reaches real
production `LedgerEvent`s and could not be removed without leaking a
frais change into the tournament's actual accounting -- see commit 11
§3). None of the three is masked, reclassified, or tolerance-widened away
here; all three are reported with their real legacy/truth values,
unchanged from what the real replay actually produced.
