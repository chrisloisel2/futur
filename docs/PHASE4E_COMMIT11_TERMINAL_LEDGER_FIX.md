# Phase 4E — commit 11: reproduce, then fix, the terminal ledger inconsistency

## 1. Reproduction (before any source change)

`tests/integration/test_multileg_terminal_ledger_coherence.py` calls the
real `MultiLegBacktester.run()` (only `load_enriched` is patched, via the
same `synthetic_load_enriched` fixture `tests/integration/
test_portfolio_multileg.py` already uses) with `enable_carry=True` over a
14-day synthetic window. The synthetic funding rate is constant and
positive, so the funding gate never regime-flips the carry position
closed -- it is still open when the main per-bar loop reaches its last
grid timestamp, and is only closed by `run()`'s own post-loop "close
residuals" step. This is exactly the case Phase 4D commit 9 found broken
by direct source inspection.

Run against the unmodified `multileg_backtester.py` (commit `ec5c211`):

```
tests/integration/test_multileg_terminal_ledger_coherence.py::test_carry_position_still_open_at_window_end_is_residually_closed PASSED
tests/integration/test_multileg_terminal_ledger_coherence.py::test_terminal_ledgers_agree_on_open_closed_state FAILED
tests/integration/test_multileg_terminal_ledger_coherence.py::test_terminal_row_cash_matches_leg_ledger_realized_total FAILED

AssertionError: terminal portfolio_ledger row reports gross_exposure=np.float64(0.3767106285196187)
but leg_ledger shows every leg closed -- portfolio_ledger and leg_ledger disagree
about open/closed state (Phase 4D commit 9's terminal-ledger inconsistency)
```

`leg_ledger` shows both carry legs closed (`exit_time` set to the window's
last grid timestamp); `portfolio_ledger`'s last row still reports nonzero
`gross_exposure`/`net_exposure`/`carry_exposure` -- the two ledgers
contradict each other about whether the position is open, the same
contradiction commit 9's real replay hit
(`gross_exposure` legacy=3.11 vs truth=0 at the terminal event,
`data/manifests/carry_shadow_differential.jsonl`).

## 2. A narrower fix than commit 9's message implied -- and why

Commit 9's message attributes all 5 terminal-event divergences (`cash`,
`nav`, `fees`, `borrow`, `gross_exposure`) to the single ordering bug.
Direct investigation in this commit found that framing incomplete: only
`gross_exposure`/`net_exposure` are actually caused by the ordering bug.
The other three have separate, independent causes, discovered by
computing (from the real frozen-window data) what a "leg-ledger-consistent"
terminal cash would be and comparing it field-by-field against Truth:

- `cash`/`nav`: Truth quantizes every FILL's price/quantity to the real
  exchange tick/lot grid (`ProductSpec.quantize_price`/`quantize_quantity`,
  applied in `FillPayload.__post_init__`); legacy trades the raw,
  unquantized float. Summed over all 14 legs in the real replay this is a
  **~$0.2466 permanent gap** in aggregate cash/nav -- invisible everywhere
  else because per-instrument comparisons already compensate for it by
  quantizing the legacy side first (see `comparator.py`'s own docstring),
  but never compensated for in the once-only terminal cash/nav comparison.
  Outside this file's reach: fixing it means touching `comparator.py`'s
  compensation logic or Truth's own engine, both out of Phase 4E's scope
  (`multileg_backtester.py` only) and effectively forbidden
  ("aucune reclassification opportuniste", "aucune bascule vers
  TruthEngine").
- `fees`: Truth's `Account._apply_fee` re-quantizes `cumulative_fees_paid`
  to `CASH_QUANTUM` (1E-8, satoshi-level) after *each* of the real
  replay's 14 FEE events -- compounding rounding drift of ~1.17E-8 against
  legacy's single float accumulator. An engine-internal characteristic,
  not something this file can or should touch.
- `borrow`: `pnl_by_type["borrow"]` is rounded to 2dp while
  `portfolio_ledger.borrow_total` is not -- a genuine, in-file precision
  bug (see §3 below for why it was still **not** fixed here).

None of this changes what Phase 4E commit 11 point 4 actually asks for: a
narrow fix so the terminal snapshot is produced after residual closes.
That fix (§4) resolves exactly the one thing it's scoped to resolve
(`gross_exposure`/`net_exposure`), and leaves `cash`/`nav`/`fees`/`borrow`
exactly as they already were -- because, per §3, "fixing" them turned out
to not be safely confined to a "terminal snapshot" concern at all.

## 3. A blind alley: `pnl_by_type` reaches real production events

Two candidate fixes were built, verified against the real frozen-window
replay to close `fees` and `borrow` too, and then **reverted** after a
before/after diff of the *entire* `MultiLegResult` (not just
`portfolio_ledger`) showed they were unsafe:

1. Routing the residual close through the same `close_leg()` normal exits
   already use (attributing the exit cost to `l.fees_exit` and
   `pnl_acc["fees"]` instead of silently deducting it from `cash` only).
2. Not rounding `pnl_by_type["borrow"]` to 2dp.

Both look, from inside `multileg_backtester.py`, like they only touch
"the terminal snapshot and metrics directly derived from it." They are
not: `CarryBasisAdapter.decide()`
(`src/alpha20/tournament/runner_adapters.py:135-147`) reads
`res.pnl_by_type` directly, computes a delta against the previous
cycle's cumulative totals, and emits **real** `LedgerEvent`s (`fees`,
`borrow`, `carry_funding`, `directional`, `hedge`) into the tournament's
actual paper-trading ledger. A before/after dump of the real frozen-window
replay's `events` (`build_adapter(spec).decide(...)`'s own return value,
captured via `_capture_multileg_result`) proved both candidate fixes
changed those real amounts:

```
fees event:   amount_usdt -1049.61   ->  -1236.6      (candidate fix 1)
borrow event: amount_usdt -3.17      ->  -3.169835824606616   (candidate fix 2)
```

That is a real change to `signal`/`sizing`/`coûts` reaching production
accounting through a side door -- exactly what Phase 4E's invariance
section forbids, just one layer removed from where it was first tested.
Both candidates were reverted in full; `pnl_by_type`'s construction is
untouched, and the residual-close cash/cost logic is byte-for-byte the
same as before this commit.

## 4. The fix actually applied

`multileg_backtester.py`:

- The per-bar `port_rows.append(...)` (used to run unconditionally,
  including on the loop's last iteration) now skips the last grid
  timestamp.
- After the post-loop "close residuals" step (itself **unchanged** --
  same cost formula, same `cash` deduction, no new attribution), a single
  terminal row is appended once, using `positions` now that every leg is
  closed (so `check_portfolio_invariants` correctly reports zero
  exposure) and a `cash`/`equity` value reconstructed as
  `initial_capital + Σ(leg.net_pnl() for every leg, open or closed) +
  pnl_acc["borrow"]` -- entirely from `PositionLeg.net_pnl()`, an
  existing, unmodified method, and the same `pnl_acc` dict the rest of
  the file already produces. Nothing here is reconstructed from
  TruthEngine or the comparator.
- `fees_total`/`borrow_total`/`funding_pnl_total`/etc. on the terminal
  row are still `pnl_acc[...]` directly, unrounded, exactly as every
  other row already was.

## 5. Invariance proof: before vs. after

`_capture_multileg_result` was run against the real frozen window
(`carry_basis_v12`, `binance_usdm`, `2026-05-29`→`2026-07-28T21:00:00Z`)
twice -- once with `multileg_backtester.py` at commit `ec5c211` (before),
once with the fix applied (after) -- and the full `MultiLegResult` dumped
each time (`leg_ledger`, `portfolio_ledger`, `pnl_by_type`, `metrics`),
plus the real `events`/`new_state` `CarryBasisAdapter.decide()` returns.

```
leg_ledger:      IDENTICAL (byte-for-byte CSV diff, all 14 legs)
events (decide()'s real LedgerEvents): IDENTICAL
pnl_by_type:     IDENTICAL
metrics:         IDENTICAL
portfolio_ledger: 1 row differs out of 1441 (the terminal row only)
```

Terminal row, before -> after (only the exposure fields and `drawdown`'s
sign-of-zero move; `cash`/`equity` are numerically unchanged because this
particular residual position is delta-neutral, so its price PnL nets to
~0 either way):

| field | before | after |
|---|---|---|
| gross_exposure | 3.110540387870883 | **0.0** |
| net_exposure | 0.0 | 0.0 |
| net_long_exposure | 1.5552701939354414 | **0.0** |
| carry_exposure | 3.110540387870883 | **0.0** |
| cash / equity | 200384.15349169614 | 200384.15349169614 (unchanged) |
| fees_total / borrow_total / funding_pnl_total | unchanged | unchanged |

## 6. Real frozen-window replay after the fix

`scripts/run_carry_shadow_replay.py` re-run against the exact same frozen
inputs (commit 10's proof-freeze):

```
classification_counts: MATCH=487, UNEXPLAINED_DIVERGENCE=4, EXPECTED_LEGACY_DIVERGENCE=1
```

`gross_exposure` moved from `UNEXPLAINED_DIVERGENCE` to `MATCH` (486->487).
`cash`, `nav`, `fees`, `borrow` remain `UNEXPLAINED_DIVERGENCE`, with the
exact same legacy/truth values as commit 9's original run (§2's three
separate, out-of-scope causes) -- not worsened, not silently reclassified.
This is not the 491 MATCH / 0 UNEXPLAINED_DIVERGENCE Phase 4E's own text
predicts; commit 13 records the honest final verdict.

## 7. Test suite

`tests/integration/test_multileg_terminal_ledger_coherence.py`'s 3 tests
pass against the fixed code. Full existing suite (`tests/`, excluding the
pre-existing, unrelated `tests/future/test_fold_aware_loader.py` import
error present before this commit too):

```
608 passed
```

`tests/truth` (149 tests), `tests/integration/test_portfolio_multileg.py`,
`tests/unit/test_multileg_engine.py` all still pass. `ruff`/`mypy` on
`multileg_backtester.py` show the exact same pre-existing findings (25 /
5 respectively) before and after this commit -- no new violations
introduced.
