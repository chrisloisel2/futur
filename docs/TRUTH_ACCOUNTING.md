# Truth Engine accounting conventions

`src/futur/truth/` (Phase 4). This document is the single reference for
the accounting convention this engine commits to, its domain model, and
what it deliberately does not do yet. Every module under `src/futur/truth/`
references this file rather than re-explaining the same decisions.

## Scope

In scope: domain models (products, orders, fills, events), a durable
append-only ledger, spot accounting, perpetual accounting, exposure and
margin, accounting invariants, deterministic replay, reconciliation.

Explicitly out of scope for this phase: strategies, signals, features, ML,
exchange connectivity, real historical data, complex tax estimation, a
sophisticated multi-strategy portfolio, or stochastic slippage modelling.
`src/futur/truth/` imports nothing from `src.alpha20`, `src.institutional`,
`legacy`, `frontend_pipeline`, or the second `trading-system` runtime copy
-- enforced by `tests/architecture/test_no_forbidden_imports_from_src.py`.
The old runtime keeps running; as of Phase 4C, one runner (CarryBasisAdapter)
feeds TruthEngine a copy of its events in a read-only shadow (see
`docs/PHASE4C_CARRY_SHADOW.md`) -- TruthEngine still does not decide, size,
or place anything.

## Numeric model

Every money-like field (`cash`, fees, funding, borrow, realized/unrealized
PnL, margin, NAV) is `Decimal`, quantized to `numeric.CASH_QUANTUM`
(`Decimal("0.00000001")`, 8 decimal places) on every write. Price and
quantity fields quantize instead to their `ProductSpec`'s own
`tick_size`/`lot_size` (`ProductSpec.quantize_price`/`quantize_quantity`) --
a fixed global quantum would be wrong across products with different tick
sizes. `FillPayload`, `MarkPayload`, and `LiquidationPayload` all quantize
their price (and `FillPayload`/`LiquidationPayload` their quantity) to the
same instrument's grid at construction, not just `MarkPayload` -- a
liquidation or fill priced off-grid could otherwise round differently than
a mark at the same real price and silently move NAV (found by the
Hypothesis property suite; see commit history for `account.py`).

A float only ever enters this engine through `numeric.to_decimal()`, which
converts via `Decimal(str(x))`, never `Decimal(x)` directly on a float --
`Decimal(0.1)` imports the float's exact binary noise
(`Decimal('0.1000000000000000055511151231257827021181583404541015625')`),
`Decimal(str(0.1))` does not. This is a structural rule enforced at every
payload's `__post_init__`, not a calling convention callers must remember.

Cash and its per-category cumulative counters (`Account.cumulative_*`) are
kept exactly consistent by quantizing each individual term **once**,
independently, before it is combined into either `cash` or its category --
never quantizing a multi-term delta as a single operation for `cash` while
quantizing the same terms separately for the categories, which can round
differently at an exact tie under `ROUND_HALF_EVEN` (also found by property
testing).

## Domain model

- **ProductSpec** (`events.py`): `venue`, `symbol`, `type`, `base_ccy`,
  `quote_ccy`, `tick_size`, `lot_size`, `multiplier`. `type` is
  `ProductType`, a **closed enum**: `SPOT` or `LINEAR_PERP` only --
  constructing anything else (inverse perps, futures, options) raises
  `ValueError` structurally, not via a runtime branch that might be
  forgotten. Identified by `venue:symbol:type` (`ProductSpec.key`) -- the
  same symbol can exist as both spot and perp on the same venue with fully
  separate accounting. `quote_ccy` must be in
  `SUPPORTED_QUOTE_CURRENCIES` (`{"USD"}` today) --
  `UnsupportedCurrencyError` otherwise.
- **Order** (`orders.py`): explicit state machine --
  `CREATED -> SUBMITTED -> ACKNOWLEDGED -> {PARTIALLY_FILLED -> FILLED,
  CANCELLED}`, `SUBMITTED -> REJECTED`. All 3 terminal states
  (`FILLED`/`CANCELLED`/`REJECTED`) accept no further transition.
  `apply_fill()` is the one place `filled_quantity` changes, so over-fill
  and fill-after-terminal are structurally impossible, not just checked.
- **Event** (`events.py`): every state change is one of 14 typed events
  (`CASH_DEPOSIT`, `CASH_WITHDRAWAL`, `ORDER_SUBMITTED`,
  `ORDER_ACKNOWLEDGED`, `ORDER_REJECTED`, `ORDER_CANCELLED`, `FILL`,
  `MARK`, `FUNDING`, `BORROW_COST`, `FEE`, `MARGIN_UPDATE`, `LIQUIDATION`,
  `RECONCILIATION`), each with its own frozen payload dataclass -- a
  mismatched payload/event_type pair is a construction-time `TypeError`,
  never a runtime surprise. Canonical order: `(ts_received, sequence,
  event_id)`; `sequence` is assigned by the ledger on append, never by the
  event's constructor. `events.event_to_dict`/`event_from_dict` are the
  ONE canonical dict representation of an Event -- used by the JSONL
  fixture reader/writer (`replay.py`) and by the ledger's own hash chain
  and durable WAL (`ledger.py`), so the bytes that get hashed are always
  exactly the bytes that get persisted and read back.

## Ledger

`ledger.py`'s `Ledger` has exactly one mutator, `append()` -- no delete, no
update, no rewrite. Each entry's `cumulative_hash` chains SHA-256 of
(previous hash + canonical JSON of the stamped event), so any change to
any past event's content or order changes every hash after it. `sequence`
always comes from the ledger's own monotonic counter, never trusted from
the caller. The chain's genesis (`head_hash` before any append) is bound
to `(engine_version, margin_config)`, not a fixed constant -- two ledgers
replaying identical events under a different engine version or margin
config provably diverge.

**Durability.** Pass `wal_path` to persist every appended entry to a
newline-delimited JSON file, fsync'd before the in-memory append completes
-- a crash between "wrote to disk" and "updated in-memory state" loses at
most an in-flight append, never a torn one. Reopening the same `wal_path`
replays and verifies the entire file (every stored hash recomputed and
checked against the chain, every sequence checked for exact monotonicity)
before a single new event can be appended -- any truncated line, malformed
record, sequence gap, duplicate id, or hash mismatch raises
`LedgerCorruptionError` immediately; nothing is silently dropped or
repaired.

**Atomicity.** `Account.apply_event()` is all-or-nothing: state is
snapshotted before a handler runs and restored on any exception, so a
rejected event (a domain `ValueError`, `ShortSpotNotAllowedError`,
`InvalidOrderTransition`, ...) is indistinguishable from an event that was
never received -- no partial mutation (e.g. an `Order`'s
`filled_quantity` incremented while cash/positions never moved) can
survive a rejection. The ledger itself stays append-only and fail-stop:
a rejected event's WAL line is only ever written by `Ledger.append()`
*after* the caller has decided to append it (see `engine.py`'s own
ordering) -- an event that `Account.apply_event()` rejects never reaches
`Ledger.append()` in the first place when the caller checks validity
before appending; an event that the ledger accepts but the account then
rejects stays on the ledger (append-only, it cannot be un-appended) while
the account itself is left byte-identical to before.

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
  quantity-weighted average of old and new, quantized to the product's
  `tick_size` immediately (Decimal division is not guaranteed to
  terminate).
- **opposite-direction fill, `|fill| <= |existing|`** (reducing): realizes
  PnL on the closed portion at `(price - avg_entry) * closed_qty`,
  sign-adjusted for long vs. short; the remaining position's
  `avg_entry_price` is unchanged (same cost basis, smaller quantity) --
  resets to `0` only once fully closed.
- **opposite-direction fill, `|fill| > |existing|`** (flip): realizes PnL
  on 100% of the old position, then opens a brand-new position for the
  remainder at the fill price (not a blend with the old entry price).

`PerpPosition.unrealized_pnl()` is always priced off the current `MARK`,
never the last trade price. `FUNDING` moves cash by its signed amount
directly (`+` received, `-` paid).

## Exposure and margin

`margin.py`'s functions are pure -- they read `account.spot_positions`/
`perp_positions`/`marks` fresh every call, nothing is cached or updated
independently of the positions it describes. All values are `Decimal`;
`MarginConfig`'s rates are converted via `numeric.to_decimal` so a plain
float/str rate still converts safely.

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
outpaces that shrinkage, not from price movement alone. `can_open_
additional_notional()` answers "would this much more perp exposure leave
sufficient initial margin?" against the account's one shared collateral
pool -- opening exposure on one instrument reduces what's available for
another, never evaluated in isolation
(`test_collateral_is_a_single_shared_pool_not_reused_per_instrument`,
and independently, `test_multiple_assets_share_one_collateral_pool_matches_oracle`).

## Liquidation

`LIQUIDATION` is a forced closure that bypasses the Order/Fill machinery
entirely (no client order is involved) -- same realized-PnL math as a
normal closing perp fill, applied directly to the position. Raises rather
than silently clamping if there's no such position or the requested close
exceeds what's open. Carries an explicit `fee` AND an explicit `slippage`,
tracked in separate `cumulative_*` categories so neither is double-counted
against the other -- this is what makes `NAV_before - NAV_after == fee +
slippage` exactly true (realizing PnL at `price` is NAV-neutral: unrealized
shrinks by exactly what realized grows by), verified independently in
`tests/truth/test_reference_fixtures.py`'s oracle-checked scenario.

## Invariants

`invariants.check(account, ledger, margin_config)` runs after every event
(via `engine.py`'s `TruthEngine.apply()`) and never catches or downgrades a
violation -- raises `InvariantViolation` (or lets a lower-layer
`ValueError`/`TypeError` through) immediately. All comparisons are
**exact Decimal equality** -- no `math.isclose`, no epsilon, anywhere in
`invariants.py`. This is only correct because every value that reaches
this file is already quantized (`numeric.quantize_cash` for money,
`ProductSpec.quantize_price`/`quantize_quantity` for price/quantity) at
the point it is produced -- two quantities that are "supposed to be equal"
are computed from the same quantized inputs via the same rounding rule, so
they land on the exact same Decimal value, not just a close one. A
tolerance would hide the exact bug this file exists to catch (tolerance
still has a legitimate place -- external reconciliation against a real
venue's numbers, which can't be assumed to use the same quantization; see
`reconciliation.py`'s own explicit, per-`(venue, field)` `ToleranceConfig`).

Checked: cash/NAV finite, mark prices positive, position quantities
finite, `filled_quantity <= quantity`, no naked short spot without the
explicit opt-in, maintenance margin `<=` initial margin and both `>= 0`,
ledger sequence strictly monotonic, no duplicate `event_id`/`fill_id`, no
`client_order_id` mapping to conflicting order details, positions equal to
the sum of signed fills/liquidations replayed straight from the ledger
(independent of `_apply_perp_fill`'s own weighted-average state), and cash
equal to the sum of 8 categorized cash-flow counters tracked alongside
`cash` itself (a double-entry-style cross-check: it can't prove the
formula is right, but it catches a future edit that updates one without
the other).

## Replay

`replay.py` does not implement determinism separately -- it falls out of
`Ledger`/`Account`/`invariants` already being deterministic, plus
`replay()` calling the exact same `TruthEngine.apply()` live processing
would use, for every event, in file order. A fixture is one JSON object
per line (`events.event_to_dict`/`event_from_dict`, explicit
per-payload-class dispatch, not generic reflection).
`tests/fixtures/truth/basic_replay.jsonl` covers 14 scenarios built by
actually running them through `TruthEngine` (not hand-computed).
`tests/fixtures/truth/reference/*.jsonl` are a SEPARATE set, exported from
scenarios whose expected values were computed independently by
`tests/truth/oracle.py` -- a from-scratch reimplementation that imports no
reducer/mutation code from `src.futur.truth` -- and cross-checked against
the real engine's output (Phase 4B commit 4).

## Reconciliation

`reconciliation.py`'s `reconcile()` is pure -- it never mutates the
account. Compares `cash`, `nav` (the external source's own reported total,
not re-derived -- perp unrealized PnL isn't independently computable from
quantity alone without the external source's entry-price convention),
per-instrument quantities, and open-order sets against an
`ExternalSnapshot` (which now carries a required `venue`), producing
`MATCH` or `MISMATCH` with the specific diffs. `ToleranceConfig` is the
**only** place in the engine a numeric tolerance is permitted: an explicit
`default` plus a `per_venue_field: {(venue, field_name): Decimal}`
override -- never a hidden global fudge factor, and proven not to leak
across venues (`test_tolerance_is_explicit_per_venue_and_field`). Never
auto-corrects -- a caller decides what to do with a `MISMATCH`;
`to_event_payload()` only converts a result into the `RECONCILIATION`
event `engine.apply()` would record, it doesn't record it.

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

- **Mono-currency.** Every cash-affecting event's `currency` must be in
  `SUPPORTED_QUOTE_CURRENCIES` (`{"USD"}` today) -- no FX conversion. Real
  multi-currency accounting needs FX rates, a data/market-data concern
  this phase excludes.
- **Simplified margin model.** One flat `initial_margin_rate`/
  `maintenance_margin_rate` pair, no per-asset tiers, no cross-margining
  rules beyond "one shared collateral pool." Real venues have far more
  structure here; this is deliberately the minimal model the mission asked
  for, not a production margin engine.
- **No real concurrency model.** The ledger and account assume a single
  writer; "race" tests (Phase 4B commit 3) exercise mis-ordered event
  *sequences*, not actual thread/async contention.
- **Not authoritative for any runner.** As of Phase 4C, CarryBasisAdapter
  feeds TruthEngine a read-only shadow copy of its events for differential
  comparison -- TruthEngine still never decides, sizes, or places an
  order, and the legacy runtime remains the sole source of truth for
  results. Migrating a runner onto this engine is explicitly a later
  phase's work.
