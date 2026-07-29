"""src/alpha20/tournament/truth_shadow/mapping.py -- converts
CarryBasisAdapter's legacy leg/portfolio ledgers into src.futur.truth
Events, with an explicit, documented field-by-field mapping table and
strict rejection of anything it cannot map honestly.

Source data (from src.institutional.backtest.multileg_backtester.
MultiLegResult, produced by re-running MultiLegBacktester exactly the way
CarryBasisAdapter.decide() already does -- this module never runs its own
copy of the strategy, only converts an ALREADY-COMPUTED result):

  - `leg_ledger` (pandas DataFrame, one row per PositionLeg that has ever
    existed in the replay window): position_id, leg_id, asset, leg_type,
    position_type, engine, entry_time, exit_time, qty, notional,
    entry_price, exit_price, price_pnl, funding_pnl, costs, net_pnl.
  - `pnl_by_type` (dict): directional, carry_funding, hedge, fees, borrow
    -- portfolio-level CUMULATIVE totals since the backtest's own start.
  - `market_prices` (Phase 4D commit 6, dict[asset] -> pandas Series of
    real close prices, datetime-indexed): the EXACT per-asset price
    series MultiLegBacktester._load() fed into its own `px(a, t)` closure
    -- captured via a reversible monkeypatch in shadow_runner.py, never
    reconstructed or inverted from a derived field.

CarryBasisAdapter itself is re-run from `paper_start` to the latest bar on
EVERY cycle (a fresh, deterministic replay, not an incremental one), so
`leg_ledger` on cycle N contains the FULL history again, including legs
already seen on cycle N-1. `LegLedgerToTruthEvents` is STATEFUL across
cycles for exactly this reason: it remembers what it already emitted per
leg_id (`_seen`), and on each new cycle emits only the DELTA -- a brand
new leg's entry, a previously-open leg's exit (if it closed since last
seen), and fee/funding DELTAS since last observed. This mirrors
CarryBasisAdapter's own `last_cum_pnl_by_type` delta-tracking pattern
exactly, and is what makes fee/funding attribution honest instead of an
arbitrary split (see the FIELD MAPPING TABLE below).

FIELD MAPPING TABLE (leg_ledger column -> Truth field, one row = one
documented decision):

  leg_ledger.asset (e.g. "BTCUSDT")
      -> ProductSpec.symbol = asset (unchanged, traceable to the source)
      -> ProductSpec.base_ccy / tick_size / lot_size / multiplier: looked
         up in a REAL, versioned registry (product_specs.py), extracted
         from Binance's own official exchangeInfo endpoints and frozen
         with a SHA-256 of the full raw response (Phase 4D commit 6 --
         earlier phases used a neutral 1E-8 grid; that placeholder is
         gone). An asset/product with no entry in the registry raises
         ProductSpecUnavailableError (BLOCKED_PRODUCT_SPEC), never a
         fallback grid.
      -> ProductSpec.quote_ccy = "USD"
         DOCUMENTED CONVENTION, not a silent guess: this codebase's legacy
         runners quote exclusively in USDT (Binance USD-M/spot), and
         Truth's mono-currency scope only recognizes "USD" (see
         SUPPORTED_QUOTE_CURRENCIES). USDT is treated 1:1 as USD, the
         same convention the legacy backtester itself uses implicitly
         (amount_usdt fields, no FX model anywhere in either domain) --
         LIMITED TO THIS SHADOW: this is not a claim that USDT carries no
         depeg risk, and no such risk is modeled or validated anywhere in
         this package. Any asset NOT ending in "USDT" is REJECTED
         (UnmappableLegError), not guessed at.
      -> ProductSpec.venue = the shadow's configured venue (from
         RunnerSpec.venue, e.g. "binance_usdm") -- read from the spec,
         never invented.

  leg_ledger.leg_type -> ProductSpec.type and each fill's side:
      LONG_SPOT, CARRY_LONG_SPOT   -> ProductType.SPOT,       entry BUY / exit SELL
      SHORT_HEDGE, CARRY_SHORT_PERP -> ProductType.LINEAR_PERP, entry SELL / exit BUY
      (LEG_DELTA_SIGN in src.institutional.portfolio.position already
      encodes exactly this: +1 -> BUY-to-open, -1 -> SELL-to-open.)
      Any other leg_type is REJECTED -- LEG_TYPES is a closed set in the
      legacy model too, so this can only happen if that set changes
      without this mapping being updated, which must fail loudly rather
      than silently mis-map a new leg type.

  leg_ledger.qty, .entry_price, .entry_time
      -> the ENTRY fill's quantity/price/timestamp, taken verbatim -- this
         is the one and only source for these numbers, never re-derived
         or rounded beyond Truth's own ProductSpec quantization (now a
         REAL tick/lot grid -- see test_off_grid_price_and_quantity_are_
         quantized_to_the_real_product_spec for proof this isn't a no-op).

  leg_ledger.exit_price, .exit_time (only when exit_time is not null)
      -> the EXIT fill's price/timestamp, taken verbatim, quantity =
         leg_ledger.qty again (a leg always closes 100% of itself, the
         legacy model has no partial-close of a single leg).

  FILL.fee is ALWAYS 0 in this mapping, by design, not a fabrication:
      leg_ledger.costs is *combined* entry+exit fees+slippage for the
      leg's full lifetime, with NO per-fill breakdown available anywhere
      in MultiLegResult. Splitting it between the entry and exit fill
      would require guessing an allocation -- exactly the "conversion
      ambiguë" this module is required to reject. Instead, the REAL
      accrued cost is emitted as its own standalone FEE event, sized as
      the DELTA of leg_ledger.costs since this leg_id was last observed
      (0 the first time -- so on a leg's first-ever observation the delta
      IS the true entry-side cost; if a leg is observed for the first
      time already closed within one shadow cycle, the delta is the true
      combined cost, still exact in total, just not split between the
      two fills).

  leg_ledger.funding_pnl (only for CARRY_SHORT_PERP / SHORT_HEDGE legs --
  LEG_FUNDING_SIGN in the legacy model is 0 for the two SPOT leg types)
      -> a FUNDING event on the leg's own instrument, sized as the DELTA
         since this leg_id was last observed. Same delta-tracking
         rationale as fees above -- this is the ONE per-leg-cumulative
         number the legacy model exposes, not a per-funding-period
         series (MultiLegBacktester accrues funding hourly internally,
         see funding_pnl_cum, but only ever surfaces the running total in
         leg_ledger).

  MARK (Phase 4D commit 6 -- CHANGED from earlier phases, refined in
  commit 8): sourced DIRECTLY from `market_prices[asset]`, the real
  close-price series captured from MultiLegBacktester._load()'s own
  return value, looked up with the EXACT SAME searchsorted semantics as
  the backtester's own `px(a, t)` closure (most recent bar at or before
  t, never a future one). Earlier phases inverted PositionLeg.price_pnl()
  algebraically to recover an implied mark price -- that path is REMOVED;
  a leg with no matching series in `market_prices` raises
  MarkSourceUnavailableError (BLOCKED_MARK_SOURCE) rather than falling
  back to inversion or a guess.

  Sampled once per REAL calendar day across the leg's OWN real lifetime
  (entry_time -> exit_time, or entry_time -> the observation timestamp if
  still open) -- NOT only "if currently open" (commit 8 finding:
  MultiLegBacktester.run() force-closes every still-open position at its
  own `end` when the backtest window ends, so a leg observed via
  independently truncated re-runs is NEVER seen "genuinely still open,"
  regardless of how fine the observation cadence is -- sampling from the
  leg's own known real lifetime instead of the live "is it open right
  now" state sidesteps that structural dead end entirely, and does not
  depend on the shadow being invoked more than once). Emitted only if the
  looked-up price differs from the last mark seen for that leg_id (an
  optimization against redundant MARK events, not a requirement).

  Portfolio-level pnl_by_type["borrow"]: NOT tracked per-leg anywhere in
  the legacy model (see multileg_backtester.py's funding-hour block --
  borrow is subtracted straight from portfolio `cash`, never attributed
  to a specific PositionLeg). Mapped as ONE BorrowCostPayload per shadow
  cycle, sized as the delta of the aggregate since the last cycle -- the
  same bucket-delta mechanism CarryBasisAdapter's own decide() already
  uses for this exact number. BorrowCostPayload has no instrument field,
  so this is a faithful (not lossy) mapping, not a workaround.

  LIQUIDATION: NOT APPLICABLE. MultiLegBacktester has no margin-call /
  liquidation mechanic (positions only close on signal, regime flip, DD
  governor, or funding-gate flip) -- documented scope gap, not a silent
  omission. Never emitted by this module.

  ORDER_SUBMITTED / ORDER_ACKNOWLEDGED: synthesized ONE order per fill
  (entry and exit each get their own order_id), quantity = the exact fill
  quantity (so it fills in one shot, no partial-fill ambiguity),
  timestamped at the SAME instant as the fill itself -- the legacy
  backtester has no discrete order lifecycle (fills are instantaneous by
  construction), so using the fill's own timestamp for submit/ack is the
  most honest representation available, not an invented delay.

Ordering: within one shadow cycle, legs are processed in leg_id order
(the backtester's own stable, monotonically-increasing creation-order id
-- "leg_1", "leg_2", ...), and each leg's own event sequence (order,
ack, [mark], [fee], [funding], fill...) is emitted as one contiguous
block. This is a documented, deterministic total order -- reproducible
run to run on the same data -- NOT a claim to reconstruct the true
wall-clock interleaving of independent legs' fills.

Concurrency: single-consumer by construction (one shadow cycle is
processed start-to-finish before the next begins) -- no thread-safety
promise, none needed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

from src.alpha20.tournament.truth_shadow.product_specs import ProductSpecRegistry
from src.futur.truth.events import (
    BorrowCostPayload,
    Event,
    EventType,
    FeePayload,
    FillPayload,
    FundingPayload,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderSubmittedPayload,
    ProductSpec,
    ProductType,
)
from src.futur.truth.numeric import to_decimal
from src.futur.truth.orders import OrderSide, OrderType
from src.institutional.portfolio.position import LEG_DELTA_SIGN, LEG_FUNDING_SIGN, LEG_TYPES

QUOTE_SUFFIX = "USDT"
QUOTE_CCY = "USD"

_REQUIRED_COLUMNS = (
    "position_id", "leg_id", "asset", "leg_type", "entry_time", "entry_price",
    "qty", "price_pnl", "funding_pnl", "costs",
)

_SPOT_LEG_TYPES = frozenset({"LONG_SPOT", "CARRY_LONG_SPOT"})
_PERP_LEG_TYPES = frozenset({"SHORT_HEDGE", "CARRY_SHORT_PERP"})


class UnmappableLegError(Exception):
    """A leg_ledger row could not be honestly converted -- a missing
    field, an unrecognized product, or a value that would require
    guessing. Raised, never silently skipped or defaulted."""


class MarkSourceUnavailableError(UnmappableLegError):
    """No real market price series is available for an open leg's
    instrument -- the BLOCKED_MARK_SOURCE condition. Never falls back to
    algebraic inversion or a guessed price."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UnmappableLegError(message)


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def product_spec_for_leg(asset: str, leg_type: str, venue: str,
                         registry: ProductSpecRegistry) -> ProductSpec:
    """See the module docstring's field mapping table for the full
    rationale behind every choice made here. Raises
    ProductSpecUnavailableError (via `registry.lookup`) if `asset` has no
    real, versioned spec on file -- never a fallback grid."""
    _require(isinstance(asset, str) and asset.endswith(QUOTE_SUFFIX),
             f"asset {asset!r} does not end in {QUOTE_SUFFIX!r} -- unmappable "
             f"to Truth's USD-only scope (no silent FX conversion)")
    _require(leg_type in LEG_TYPES, f"leg_type {leg_type!r} is not one of the "
             f"known legacy LEG_TYPES {sorted(LEG_TYPES)} -- unmappable")
    if leg_type in _SPOT_LEG_TYPES:
        product_type = ProductType.SPOT
    elif leg_type in _PERP_LEG_TYPES:
        product_type = ProductType.LINEAR_PERP
    else:
        raise UnmappableLegError(
            f"leg_type {leg_type!r} is a recognized LEG_TYPES member but has no "
            f"SPOT/LINEAR_PERP mapping defined -- the mapping table in this "
            f"module's docstring must be updated before this can be shadowed")
    real = registry.lookup(asset, product_type.value)
    return ProductSpec(venue=venue, symbol=asset, type=product_type,
                       base_ccy=real.base_ccy, quote_ccy=QUOTE_CCY,
                       tick_size=real.tick_size, lot_size=real.lot_size,
                       multiplier=real.multiplier)


def _entry_side(leg_type: str) -> OrderSide:
    sign = LEG_DELTA_SIGN[leg_type]
    return OrderSide.BUY if sign > 0 else OrderSide.SELL


def _exit_side(leg_type: str) -> OrderSide:
    return OrderSide.SELL if _entry_side(leg_type) == OrderSide.BUY else OrderSide.BUY


def _price_asof(series: pd.Series, ts: pd.Timestamp) -> Decimal | None:
    """Exactly MultiLegBacktester's own `px(a, t)` closure: the most
    recent bar at or before `ts`, never a future one (no lookahead)."""
    if series is None or len(series) == 0:
        return None
    idx = series.index.searchsorted(ts, side="right") - 1
    if idx < 0:
        return None
    value = series.iloc[idx]
    if _is_missing(value):
        return None
    return to_decimal(float(value))


@dataclass
class _LegState:
    entry_emitted: bool = False
    exit_emitted: bool = False
    costs_seen: Decimal = Decimal(0)
    funding_seen: Decimal = Decimal(0)
    last_mark_price: Decimal | None = None
    marked_days: set = field(default_factory=set)


@dataclass
class LegLedgerToTruthEvents:
    """Stateful converter -- one instance per shadowed runner, reused
    across cycles. `venue` comes from the runner's own RunnerSpec, never
    invented. `registry` supplies real, versioned ProductSpecs (Phase 4D
    commit 6) -- see product_specs.py."""
    venue: str
    registry: ProductSpecRegistry
    _seen: dict[str, _LegState] = field(default_factory=dict)

    def events_for_cycle(self, leg_ledger: pd.DataFrame, cycle_ts: str,
                         market_prices: dict[str, pd.Series]) -> list[Event]:
        """Returns the DELTA of Truth events for this cycle's leg_ledger
        snapshot, in the documented deterministic order (leg_id order,
        each leg's own sequence contiguous). `market_prices` must be the
        REAL per-asset close-price series captured from
        MultiLegBacktester._load() (see shadow_runner.py) -- used for
        MARK events on any still-open leg. Raises UnmappableLegError (or
        MarkSourceUnavailableError specifically) on the first row that
        cannot be honestly converted -- nothing partial is returned."""
        events: list[Event] = []
        if leg_ledger.empty:
            return events
        cycle_ts_pd = pd.Timestamp(cycle_ts)
        for _, row in leg_ledger.sort_values("leg_id").iterrows():
            events.extend(self._events_for_row(row, cycle_ts, cycle_ts_pd, market_prices))
        return events

    def _events_for_row(self, row: pd.Series, cycle_ts: str, cycle_ts_pd: pd.Timestamp,
                        market_prices: dict[str, pd.Series]) -> list[Event]:
        for col in _REQUIRED_COLUMNS:
            _require(col in row.index, f"leg_ledger is missing required column {col!r}")
            _require(not _is_missing(row[col]),
                     f"leg {row.get('leg_id', '?')!r}: required field {col!r} is missing/NaN")
        leg_id = str(row["leg_id"])
        asset = str(row["asset"])
        product = product_spec_for_leg(asset, str(row["leg_type"]), self.venue, self.registry)
        qty = to_decimal(row["qty"])
        _require(qty > 0, f"leg {leg_id!r}: qty must be > 0, got {qty!r}")

        state = self._seen.setdefault(leg_id, _LegState())
        events: list[Event] = []

        if not state.entry_emitted:
            events.extend(self._entry_events(row, leg_id, product, qty))
            state.entry_emitted = True

        events.extend(self._fee_delta_events(row, leg_id, state, cycle_ts))
        events.extend(self._funding_delta_events(row, leg_id, product, state, cycle_ts))
        events.extend(self._mark_events_for_leg(row, leg_id, asset, product, state,
                                                cycle_ts_pd, market_prices))

        exit_time = row.get("exit_time")
        if not _is_missing(exit_time) and not state.exit_emitted:
            events.extend(self._exit_events(row, leg_id, product, qty))
            state.exit_emitted = True

        return events

    def _entry_events(self, row: pd.Series, leg_id: str, product: ProductSpec,
                      qty: Decimal) -> list[Event]:
        order_id = f"{leg_id}-order-entry"
        ts = str(row["entry_time"])
        side = _entry_side(str(row["leg_type"]))
        price = to_decimal(row["entry_price"])
        _require(price > 0, f"leg {leg_id!r}: entry_price must be > 0, got {price!r}")
        return [
            Event(event_id=order_id, event_type=EventType.ORDER_SUBMITTED,
                 ts_event=ts, ts_received=ts,
                 payload=OrderSubmittedPayload(order_id=order_id, client_order_id=order_id,
                                               instrument=product, side=side.value,
                                               order_type=OrderType.MARKET.value, quantity=qty)),
            Event(event_id=f"{order_id}-ack", event_type=EventType.ORDER_ACKNOWLEDGED,
                 ts_event=ts, ts_received=ts,
                 payload=OrderAcknowledgedPayload(order_id=order_id)),
            Event(event_id=f"{leg_id}-fill-entry", event_type=EventType.FILL,
                 ts_event=ts, ts_received=ts,
                 payload=FillPayload(fill_id=f"{leg_id}-fill-entry", order_id=order_id,
                                     instrument=product, price=price, quantity=qty,
                                     side=side.value, fee=Decimal(0), fee_ccy=QUOTE_CCY)),
        ]

    def _exit_events(self, row: pd.Series, leg_id: str, product: ProductSpec,
                     qty: Decimal) -> list[Event]:
        order_id = f"{leg_id}-order-exit"
        ts = str(row["exit_time"])
        exit_price = row.get("exit_price")
        _require(not _is_missing(exit_price),
                 f"leg {leg_id!r}: exit_time is set but exit_price is missing")
        price = to_decimal(exit_price)
        _require(price > 0, f"leg {leg_id!r}: exit_price must be > 0, got {price!r}")
        side = _exit_side(str(row["leg_type"]))
        return [
            Event(event_id=order_id, event_type=EventType.ORDER_SUBMITTED,
                 ts_event=ts, ts_received=ts,
                 payload=OrderSubmittedPayload(order_id=order_id, client_order_id=order_id,
                                               instrument=product, side=side.value,
                                               order_type=OrderType.MARKET.value, quantity=qty)),
            Event(event_id=f"{order_id}-ack", event_type=EventType.ORDER_ACKNOWLEDGED,
                 ts_event=ts, ts_received=ts,
                 payload=OrderAcknowledgedPayload(order_id=order_id)),
            Event(event_id=f"{leg_id}-fill-exit", event_type=EventType.FILL,
                 ts_event=ts, ts_received=ts,
                 payload=FillPayload(fill_id=f"{leg_id}-fill-exit", order_id=order_id,
                                     instrument=product, price=price, quantity=qty,
                                     side=side.value, fee=Decimal(0), fee_ccy=QUOTE_CCY)),
        ]

    def _fee_delta_events(self, row: pd.Series, leg_id: str, state: _LegState,
                          cycle_ts: str) -> list[Event]:
        costs_now = to_decimal(row["costs"])
        delta = costs_now - state.costs_seen
        state.costs_seen = costs_now
        if delta == 0:
            return []
        _require(delta > 0, f"leg {leg_id!r}: costs decreased since last observed "
                 f"({costs_now!r} < previous) -- unmappable, costs must be monotonic")
        ts = str(row.get("exit_time")) if not _is_missing(row.get("exit_time")) else cycle_ts
        return [Event(event_id=f"{leg_id}-fee-{cycle_ts}", event_type=EventType.FEE,
                     ts_event=ts, ts_received=ts,
                     payload=FeePayload(amount=delta, currency=QUOTE_CCY,
                                       reason=f"leg {leg_id} cost delta"))]

    def _funding_delta_events(self, row: pd.Series, leg_id: str, product: ProductSpec,
                              state: _LegState, cycle_ts: str) -> list[Event]:
        leg_type = str(row["leg_type"])
        if LEG_FUNDING_SIGN.get(leg_type, 0.0) == 0.0:
            return []
        funding_now = to_decimal(row["funding_pnl"])
        delta = funding_now - state.funding_seen
        state.funding_seen = funding_now
        if delta == 0:
            return []
        ts = str(row.get("exit_time")) if not _is_missing(row.get("exit_time")) else cycle_ts
        return [Event(event_id=f"{leg_id}-funding-{cycle_ts}", event_type=EventType.FUNDING,
                     ts_event=ts, ts_received=ts,
                     payload=FundingPayload(instrument=product, amount=delta, currency=QUOTE_CCY))]

    def _mark_events_for_leg(self, row: pd.Series, leg_id: str, asset: str, product: ProductSpec,
                             state: _LegState, cycle_ts_pd: pd.Timestamp,
                             market_prices: dict[str, pd.Series]) -> list[Event]:
        """Samples REAL daily bars across the leg's OWN real lifetime
        (entry_time -> exit_time, or entry_time -> `cycle_ts_pd` if still
        open), one MARK per calendar day, from `market_prices[asset]` --
        the exact series MultiLegBacktester._load() fed the backtest.

        This does NOT depend on observing the leg "still open" at the
        moment this method runs: a fully-CLOSED leg's marks are just as
        real and just as required (Phase 4D commit 8's "marks spot/perp
        issus des données réelles" coverage) as an open one's. It is
        computed directly, once, from the leg's own real entry/exit
        timestamps -- not tied to how many times, or when, this shadow
        happens to be invoked, so it does not depend on simulating
        discrete "cycles over time" against a backtester
        (MultiLegBacktester.run()) that always force-closes every
        still-open position at its OWN end -- which would otherwise make
        "genuinely still open at observation time" structurally
        unobservable through repeated truncated re-runs, independent of
        cadence."""
        series = market_prices.get(asset)
        if series is None:
            raise MarkSourceUnavailableError(
                f"leg {leg_id!r}: no real market price series for asset {asset!r} -- "
                f"BLOCKED_MARK_SOURCE (never inverted from price_pnl)")
        entry_ts = pd.Timestamp(row["entry_time"])
        exit_time = row.get("exit_time")
        end_ts = pd.Timestamp(exit_time) if not _is_missing(exit_time) else cycle_ts_pd
        if end_ts <= entry_ts:
            return []
        sample_points = pd.date_range(entry_ts.normalize() + pd.Timedelta(days=1), end_ts,
                                      freq="1D", tz="UTC")
        events: list[Event] = []
        for t in sample_points:
            if t in state.marked_days:
                continue
            state.marked_days.add(t)
            mark = _price_asof(series, t)
            if mark is None:
                raise MarkSourceUnavailableError(
                    f"leg {leg_id!r}: market price series for {asset!r} has no bar at or "
                    f"before {t} -- BLOCKED_MARK_SOURCE")
            mark = product.quantize_price(mark)
            if state.last_mark_price is not None and mark == state.last_mark_price:
                continue
            state.last_mark_price = mark
            ts_str = t.isoformat()
            events.append(Event(event_id=f"{leg_id}-mark-{ts_str}", event_type=EventType.MARK,
                                ts_event=ts_str, ts_received=ts_str,
                                payload=MarkPayload(instrument=product, price=mark)))
        return events


def borrow_delta_event(cumulative_borrow_usdt: float, previous_cumulative_borrow_usdt: float,
                       cycle_ts: str, cycle_index: int) -> Event | None:
    """Portfolio-level borrow, delta since the last cycle -- see the
    module docstring for why this can't be attributed per-leg. Sign
    convention: pnl_by_type["borrow"] is NEGATIVE-accumulating in the
    legacy model (`pnl_acc["borrow"] -= c`), so a more negative value is
    a POSITIVE borrow cost paid -- BorrowCostPayload.amount must be
    `>= 0` (a cost), hence the sign flip below."""
    now = to_decimal(cumulative_borrow_usdt)
    prev = to_decimal(previous_cumulative_borrow_usdt)
    delta_cost = prev - now   # borrow accumulates negatively -> cost is the negated delta
    if delta_cost == 0:
        return None
    _require(delta_cost > 0, f"borrow bucket moved the wrong way since last cycle "
             f"(now={now!r} prev={prev!r}) -- unmappable, borrow cost must be monotonic")
    event_id = f"cycle-{cycle_index}-borrow-{cycle_ts}"
    return Event(event_id=event_id, event_type=EventType.BORROW_COST,
                ts_event=cycle_ts, ts_received=cycle_ts,
                payload=BorrowCostPayload(amount=delta_cost, currency=QUOTE_CCY))
