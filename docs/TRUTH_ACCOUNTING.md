# Truth Engine accounting conventions

`src/futur/truth/` (Phase 4). This document is the single reference for
the accounting convention this engine commits to, its domain model, and
what it deliberately does not do yet. Every module under `src/futur/truth/`
references this file rather than re-explaining the same decisions.

## Scope

In scope: domain models (instruments, orders, fills, events), an
append-only ledger, spot accounting, perpetual accounting, exposure and
margin, accounting invariants, deterministic replay, reconciliation.

Explicitly out of scope for this phase: strategies, signals, features, ML,
exchange connectivity, real historical data, complex tax estimation, a
sophisticated multi-strategy portfolio, or stochastic slippage modelling.
`src/futur/truth/` imports nothing from `src.alpha20`, `src.institutional`,
`legacy`, `frontend_pipeline`, or the second `trading-system` runtime copy
-- enforced by `tests/architecture/test_no_forbidden_imports_from_src.py`.
The old runtime keeps running; the Truth Engine does not use it and is not
used by it yet.

## The accounting convention

Two conventions were possible: fold realized PnL/fees/funding/borrow into
`cash` immediately, or keep them in separate accrued/pending accounts. This
engine chose **immediate integration** -- every one of those events updates
`cash` the moment it's applied, in `Account.apply_event()`. There is no
"pending" bucket anywhere.

This makes NAV a plain 3-term sum, nothing left to double-count:

```
NAV = cash + spot_market_value + perp_unrealized_pnl
```

`spot_market_value` and `perp_unrealized_pnl` are the only components not
already folded into cash, because they are inherently *unrealized* --
paper value that depends on the current mark, not a settled cash flow.

## Domain model

- **Instrument** (`events.py`): `SPOT` or `PERPETUAL`, identified by
  `venue:symbol:type` (`Instrument.key`) -- the same symbol can exist as
  both spot and perp on the same venue with fully separate accounting.
- **Order** (`orders.py`): explicit state machine --
  `CREATED -> SUBMITTED -> ACKNOWLEDGED -> {PARTIALLY_FILLED -> FILLED,
  CANCELLED}`, `SUBMITTED -> REJECTED`. All 3 terminal states
  (`FILLED`/`CANCELLED`/`REJECTED`) accept no further transition.
  `apply_fill()` is the one place `filled_quantity` changes, so over-fill
  and fill-after-terminal are structurally impossible, not just checked.
- **Fill** (`orders.py`): immutable record of one execution --
  price/quantity/fee validated `> 0`/`>= 0` at construction.
- **Event** (`events.py`): every state change is one of 14 typed events
  (`CASH_DEPOSIT`, `CASH_WITHDRAWAL`, `ORDER_SUBMITTED`,
  `ORDER_ACKNOWLEDGED`, `ORDER_REJECTED`, `ORDER_CANCELLED`, `FILL`,
  `MARK`, `FUNDING`, `BORROW_COST`, `FEE`, `MARGIN_UPDATE`, `LIQUIDATION`,
  `RECONCILIATION`), each with its own frozen payload dataclass -- a
  mismatched payload/event_type pair is a construction-time `TypeError`,
  never a runtime surprise. Canonical order: `(ts_received, sequence,
  event_id)`; `sequence` is assigned by the ledger on append, never by the
  event's constructor.

## Ledger

`ledger.py`'s `Ledger` has exactly one mutator, `append()` -- no delete, no
update, no rewrite. Each entry's `cumulative_hash` chains SHA-256 of
(previous hash + canonical JSON of the stamped event), so any change to
any past event's content or order changes every hash after it. `sequence`
always comes from the ledger's own monotonic counter, never trusted from
the caller.

## Spot accounting

Buying spends `quantity * price + fee` from cash; selling receives
`quantity * price - fee`, both expressed as one signed-quantity formula in
`Account._apply_spot_fill`. Short spot is rejected by default
(`ShortSpotNotAllowedError`) -- enable with `Account(allow_short_spot=True)`
if a borrow mechanism is modeled elsewhere. `SpotPosition.last_price`
tracks the last fill price, used as a fallback by `margin.py` when there's
no mark yet.

## Perpetual accounting

Weighted-average-cost. The full notional never touches cash on open or
increase -- only the fee does; margin is a separate concept (below), not a
cash movement. Three cases in `Account._apply_perp_fill`:

- **same-direction fill** (opening or increasing): entry price becomes the
  quantity-weighted average of old and new.
- **opposite-direction fill, `|fill| <= |existing|`** (reducing): realizes
  PnL on the closed portion at `(price - avg_entry) * closed_qty`,
  sign-adjusted for long vs. short; the remaining position's
  `avg_entry_price` is unchanged (same cost basis, smaller quantity) --
  resets to `0.0` only once fully closed.
- **opposite-direction fill, `|fill| > |existing|`** (flip): realizes PnL
  on 100% of the old position, then opens a brand-new position for the
  remainder at the fill price (not a blend with the old entry price).

`PerpPosition.unrealized_pnl()` is always priced off the current `MARK`,
never the last trade price. `FUNDING` moves cash by its signed amount
directly (`+` received, `-` paid).

## Exposure and margin

`margin.py`'s functions are pure -- they read `account.spot_positions`/
`perp_positions`/`marks` fresh every call, nothing is cached or updated
independently of the positions it describes.

Pricing fallback when there's no `MARK` yet: the position's last known
transaction price (`avg_entry_price` for perp, `last_price` for spot), not
zero -- treating unmarked risk as zero exposure would be an invisible-
leverage hole, unlike `Account.spot_market_value()`/`perp_unrealized_pnl()`
(NAV display), where "unmarked contributes 0" is the correct, different
choice.

```
initial_margin_required     = perp_notional * initial_margin_rate
maintenance_margin_required = perp_notional * maintenance_margin_rate
margin_available            = NAV - initial_margin_required
```

`perp_notional` uses the *current* mark (or fallback), so maintenance
margin shrinks as the mark falls, same as a real perpetual -- liquidation
only triggers once a position is levered enough that unrealized loss
outpaces that shrinkage, not from price movement alone (see
`test_should_liquidate_true_once_adverse_mark_breaches_maintenance` for a
worked example). `can_open_additional_notional()` answers "would this much
more perp exposure leave sufficient initial margin?" against the account's
one shared collateral pool -- opening exposure on one instrument reduces
what's available for another, never evaluated in isolation
(`test_collateral_is_a_single_shared_pool_not_reused_per_instrument`).

## Liquidation

`LIQUIDATION` is a forced closure that bypasses the Order/Fill machinery
entirely (no client order is involved) -- same realized-PnL math as a
normal closing perp fill, applied directly to the position. Raises rather
than silently clamping if there's no such position or the requested close
exceeds what's open. At the current mark, a liquidation can only ever
*reduce* NAV by its fee, never improve it
(`test_liquidation_never_improves_nav_beyond_minus_fee`).

## Invariants

`invariants.check(account, ledger, margin_config)` runs after every event
(via `engine.py`'s `TruthEngine.apply()`) and never catches or downgrades a
violation -- raises `InvariantViolation` (or lets a lower-layer
`ValueError`/`TypeError` through) immediately. Checked: cash/NAV finite,
mark prices positive, position quantities finite, `filled_quantity <=
quantity`, no naked short spot without the explicit opt-in, maintenance
margin `<=` initial margin and both `>= 0`, ledger sequence strictly
monotonic, no duplicate `event_id`/`fill_id`, no `client_order_id` mapping
to conflicting order details, positions equal to the sum of signed
fills/liquidations replayed straight from the ledger (independent of
`_apply_perp_fill`'s own weighted-average state), and cash equal to the
sum of 7 categorized cash-flow counters tracked alongside `cash` itself
(a double-entry-style cross-check: it can't prove the formula is right,
but it catches a future edit that updates one without the other).

Float comparisons use `math.isclose(rel_tol=1e-9, abs_tol=1e-6)`, not a
bare fixed epsilon -- found necessary via property testing (see commit
10's history): a fixed absolute tolerance breaks down once cumulative sums
reach real-world magnitude (float64 has ~15-17 significant digits).

## Replay

`replay.py` does not implement determinism separately -- it falls out of
`Ledger`/`Account`/`invariants` already being deterministic, plus
`replay()` calling the exact same `TruthEngine.apply()` live processing
would use, for every event, in file order. A fixture is one JSON object per
line (`event_to_dict`/`event_from_dict`, explicit per-payload-class
dispatch, not generic reflection). `tests/fixtures/truth/basic_replay.jsonl`
covers all 14 required scenarios (deposit, spot buy/sell, favorable and
adverse marks, perp open, both funding signs, a partial fill, cancellation
of the unfilled remainder, a standalone fee, borrow, partial liquidation,
final reconciliation) -- built by actually running it through
`TruthEngine` and recording the real output, not hand-computed.

## Reconciliation

`reconciliation.py`'s `reconcile()` is pure -- it never mutates the
account. Compares `cash`, `nav` (the external source's own reported total,
not re-derived -- perp unrealized PnL isn't independently computable from
quantity alone without the external source's entry-price convention),
per-instrument quantities, and open-order sets against an
`ExternalSnapshot`, producing `MATCH` or `MISMATCH` with the specific
diffs. Never auto-corrects -- a caller decides what to do with a
`MISMATCH`; `to_event_payload()` only converts a result into the
`RECONCILIATION` event `engine.apply()` would record, it doesn't record it.

## CLI

```
futur truth replay <fixture.jsonl>     # prints a deterministic summary
futur truth validate <fixture.jsonl>   # exit 0 if every invariant held, 1 otherwise
```

Only `InvariantViolation` is caught and turned into a clean message; any
other error (a malformed fixture, a domain `ValueError`) propagates as a
normal traceback rather than being swallowed behind a blanket
`except Exception`.

## Known debts (documented, not silent)

- **Float, not `Decimal`.** Matches the rest of this codebase's convention
  (`src.alpha20`/`src.institutional` are float throughout); invariants
  explicitly check finiteness and use scale-aware tolerance as a
  compensating control, but this is not bit-exact decimal accounting.
- **Mono-currency.** Every cash-affecting event's `currency` must equal
  `Account.base_currency` (default `"USD"`) -- no FX conversion. Real
  multi-currency accounting needs FX rates, a data/market-data concern
  this phase excludes.
- **Simplified margin model.** One flat `initial_margin_rate`/
  `maintenance_margin_rate` pair, no per-asset tiers, no cross-margining
  rules beyond "one shared collateral pool." Real venues have far more
  structure here; this is deliberately the minimal model the mission asked
  for, not a production margin engine.
- **Not wired into the old runtime.** `src.alpha20`/`src.institutional`
  keep running independently; migrating strategies onto this engine is
  explicitly a later phase's work, not this one's.
