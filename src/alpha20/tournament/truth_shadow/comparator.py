"""src/alpha20/tournament/truth_shadow/comparator.py -- differential
comparator between CarryBasisAdapter's legacy MultiLegResult and
TruthEngine's account state, logged as an append-only JSONL
classification trail.

WHAT IS COMPARED, AND AGAINST WHAT (no field is compared against a
fabricated or guessed legacy value -- every "legacy_value" below traces to
a real column in MultiLegResult.leg_ledger or .portfolio_ledger):

  Per-instrument fields (spot_qty, perp_qty, entry_price, terminal_state):
  compared AFTER EVERY state-changing event, on BOTH sides as of that
  event's own position in the applied-event stream (its APPLICATION-ORDER
  INDEX, not its `ts_event` -- see `_event_index_map`/`_legs_open_asof`):
    - legacy side: the SUM of leg_ledger rows sharing that Truth
      instrument key whose own entry-fill event has already been applied,
      and whose own exit-fill event (if any) has NOT yet been applied, by
      that index -- not a real-time `ts_event` inequality (a leg's own
      final daily MARK and its exit FILL can share the IDENTICAL
      `ts_event` whenever the exit lands exactly at midnight UTC, which
      the mapper's own daily sampling does routinely; only the
      application-order index can tell which of the two happened "first"),
      and not whether the row happens to still be open in the FINAL
      leg_ledger snapshot.
    - truth side: `Account.snapshot()` taken immediately after THAT event
      was applied (`ShadowCycleResult.account_snapshots[i]`), not the
      live/current `engine.account`.
  Both sides need this "as of" treatment for the same reason: one real
  decide() call applies EVERY event for the whole replay window in a
  single batch before compare_cycle ever runs, so by the time this method
  reads anything, both the leg_ledger and engine.account already reflect
  the TERMINAL state -- "open now" and "open as of event i" only coincide
  for the very last event (Phase 4D commit 9 fix; the pre-fix version
  compared FINAL leg_ledger against FINAL engine.account for every event
  and produced 1238 spurious UNEXPLAINED_DIVERGENCE rows on the first
  real 60-day replay; a first attempt at the fix used a `ts_event`
  inequality instead of an application-order index and still produced 19
  spurious terminal_state mismatches from exactly the midnight-exit tie
  described above).
  Both `spot_qty`/`perp_qty`/`entry_price` also quantize the legacy side
  through the SAME real `ProductSpec.quantize_quantity`/`quantize_price`
  Truth's own `FillPayload.__post_init__` already applies at construction
  (a pre-existing, unrelated-to-this-shadow Truth engine invariant) --
  comparing Truth's exchange-grid-quantized position against a raw,
  unquantized legacy float would flag a real ~half-a-lot-size divergence
  on nearly every leg, even though both sides transcribe the identical
  row correctly; the ProductSpec grid itself was frozen in Commit 6, long
  before this replay ever ran, so applying it here is not a tolerance
  raised after observing a result. A mismatch that survives this
  quantization means Truth's own reducer (or this shadow's own mapper)
  disagrees with the exact source data used to build its events --
  classified SHADOW_MAPPING_ERROR, never UNEXPLAINED_DIVERGENCE, because
  the "legacy value" and the events that produced the "truth value" come
  from the identical row.

  Portfolio-level fields (cash, nav, fees, funding, borrow, gross/net
  exposure): compared exactly ONCE, at the TERMINAL state (the last
  state-changing event), against the LAST row of portfolio_ledger --
  never per-event. This is a deliberate, documented scope decision, not
  an oversight: MultiLegBacktester.run() is a single batch replay over
  the whole window, and Truth's own event stream is not strictly
  chronologically interleaved across legs (mapping.py's own docstring:
  each leg's full event block is emitted contiguously in leg_id order,
  not globally time-sorted) -- so "Truth's cumulative cash as of event
  N's own timestamp" is not a well-defined quantity to compare against a
  point-in-time legacy row for any N before the last one. The terminal
  comparison IS well-defined for both sides (both are the complete,
  final state) and is what commit 9's own PASS bar (zero
  UNEXPLAINED_DIVERGENCE) is actually about: does the two engines' FINAL
  accounting agree. A mismatch here is a genuine legacy-vs-Truth economic
  divergence -- classified UNEXPLAINED_DIVERGENCE unless the field is in
  `_NO_LEGACY_ANALOG_FIELDS` (see below).

  margin_used: legacy's MultiLegBacktester has no initial/maintenance
  margin RATE model at all (see check_portfolio_invariants -- it checks
  hedge/exposure CAPS as a fraction of equity, never a margin
  requirement). There is no legacy figure to compare Truth's
  margin.compute_margin_state() against -- always classified
  EXPECTED_LEGACY_DIVERGENCE, with a clear cause, never silently skipped
  and never miscounted as a MATCH.

  gross_exposure / net_exposure: legacy's check_portfolio_invariants()
  returns these as a FRACTION of equity, not an absolute dollar amount;
  Truth's margin.compute_exposures() returns absolute dollars. Both sides
  are normalized to "fraction of that engine's own NAV" before comparing
  -- a documented choice (relative exposure, not absolute dollars, since
  the two engines' cash bases can differ slightly even when structurally
  equivalent), not a fabricated number.

Only FILL, MARK, FUNDING, FEE, and BORROW_COST events trigger a
comparison -- ORDER_SUBMITTED/ORDER_ACKNOWLEDGED never change any
compared field (Truth's Order state machine has no cash/position
effect), so comparing before/after them would always be a trivial,
content-free MATCH row. This is "after each [state-changing] event," the
literal wording applied to what can actually differ.

Classifications (exactly these four, never a fifth):
  MATCH                      -- |truth - legacy| <= the ToleranceConfig
                                 tolerance for this (venue, field).
  EXPECTED_LEGACY_DIVERGENCE -- a documented, structural reason the two
                                 models cannot agree (margin_used today;
                                 extend _NO_LEGACY_ANALOG_FIELDS, never
                                 silently, if another such field is found).
  SHADOW_MAPPING_ERROR       -- a per-instrument field diverges from the
                                 SAME leg_ledger snapshot used to build the
                                 Truth events being compared -- the bug is
                                 provably in this shadow package, not in
                                 either accounting engine.
  UNEXPLAINED_DIVERGENCE     -- a portfolio-level field diverges beyond
                                 tolerance with no documented explanation.
                                 The mission's PASS bar requires zero of
                                 these.

No global tolerance anywhere in this module -- every tolerance comparison
routes through a caller-supplied `ToleranceConfig` (src.futur.truth.
reconciliation), attached to an explicit (venue, field).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

from src.futur.truth.engine import TruthEngine
from src.futur.truth.events import Event, EventType
from src.futur.truth.margin import compute_exposures, compute_margin_state
from src.futur.truth.numeric import to_decimal
from src.futur.truth.reconciliation import ToleranceConfig

CLASSIFICATIONS = ("MATCH", "EXPECTED_LEGACY_DIVERGENCE", "SHADOW_MAPPING_ERROR",
                  "UNEXPLAINED_DIVERGENCE")

_NO_LEGACY_ANALOG_FIELDS = frozenset({"margin_used"})
_STATE_CHANGING_EVENT_TYPES = frozenset({
    EventType.FILL, EventType.MARK, EventType.FUNDING, EventType.FEE, EventType.BORROW_COST,
})
_ZERO = Decimal(0)


@dataclass(frozen=True)
class ComparisonRow:
    run_id: str
    sequence: int
    event_id: str
    timestamp: str
    field: str
    legacy_value: str
    truth_value: str
    difference: str
    tolerance_applied: str
    classification: str
    cause: str

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(f"classification {self.classification!r} is not one of "
                             f"{CLASSIFICATIONS}")

    def to_json_line(self) -> str:
        return json.dumps({
            "run_id": self.run_id, "sequence": self.sequence, "event_id": self.event_id,
            "timestamp": self.timestamp, "field": self.field,
            "legacy_value": self.legacy_value, "truth_value": self.truth_value,
            "difference": self.difference, "tolerance_applied": self.tolerance_applied,
            "classification": self.classification, "cause": self.cause,
        }, sort_keys=True)


class DifferentialLog:
    """Append-only JSONL writer, one line per (event, field) comparison --
    no rewrite/delete method exposed, matching the ledger's own
    append-only discipline."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a", encoding="utf-8")   # noqa: SIM115

    def write(self, row: ComparisonRow) -> None:
        self._file.write(row.to_json_line() + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def _classify(field: str, legacy: Decimal | None, truth: Decimal, tolerance: Decimal
             ) -> tuple[str, Decimal, str]:
    if field in _NO_LEGACY_ANALOG_FIELDS:
        return ("EXPECTED_LEGACY_DIVERGENCE", truth,
                f"{field}: legacy has no comparable figure (documented scope gap)")
    if legacy is None:
        return ("EXPECTED_LEGACY_DIVERGENCE", truth,
                f"{field}: portfolio_ledger is empty -- no legacy row for the terminal comparison")
    diff = abs(truth - legacy)
    if diff <= tolerance:
        return ("MATCH", diff, "")
    return ("UNEXPLAINED_DIVERGENCE", diff,
           f"{field}: |truth - legacy| = {diff} exceeds tolerance {tolerance}")


def _terminal_portfolio_row(portfolio_ledger: pd.DataFrame) -> pd.Series | None:
    if portfolio_ledger.empty:
        return None
    return portfolio_ledger.iloc[-1]


def _event_index_map(applied_events: list[Event]) -> dict[str, int]:
    """event_id -> its own position in `applied_events`, i.e. Truth's real
    application order. Needed because two events can share the identical
    `ts_event` (a leg's own final daily MARK and its exit FILL both land
    exactly on the leg's exit day when that exit is midnight-aligned) --
    only the application-order index disambiguates "before this leg's own
    exit was applied" from "at or after it," a real-time inequality on
    `ts_event` alone cannot (Phase 4D commit 9 fix)."""
    return {e.event_id: i for i, e in enumerate(applied_events)}


def _legs_open_asof(leg_ledger: pd.DataFrame, asset: str, leg_types: frozenset,
                    as_of_idx: int, event_index: dict[str, int]) -> pd.DataFrame:
    """Legs of `asset`/`leg_types` open AT applied-event index `as_of_idx`
    (the SAME index `account_snapshots[as_of_idx]` was captured at),
    determined from whether each leg's OWN entry-fill/exit-fill events --
    looked up by event_id in `event_index`, i.e. by their real position in
    the applied event stream -- have themselves been applied by that
    index. NOT from a real-time `ts_event` comparison (see
    `_event_index_map`), and NOT from whether the row is still open in the
    FINAL leg_ledger snapshot: a single MultiLegBacktester.run() call
    returns one ledger for the whole replay window, so "open as of event
    X" must be derived per event, not read off a single exit_time.isna()
    flag (Phase 4D commit 9 fix)."""
    if leg_ledger.empty:
        return leg_ledger
    mask = []
    for r in leg_ledger.itertuples():
        if r.asset != asset or r.leg_type not in leg_types:
            mask.append(False)
            continue
        entry_idx = event_index.get(f"{r.leg_id}-fill-entry")
        if entry_idx is None or entry_idx > as_of_idx:
            mask.append(False)
            continue
        exit_idx = event_index.get(f"{r.leg_id}-fill-exit")
        mask.append(exit_idx is None or exit_idx > as_of_idx)
    return leg_ledger[mask]


def _quantized_leg_qty(row, instrument) -> Decimal:
    """The REAL exchange-grid-quantized quantity this leg's own fill
    actually produced in Truth -- `FillPayload.__post_init__` always
    quantizes to `instrument.lot_size` at construction (a pre-existing,
    unrelated-to-this-shadow Truth engine invariant, see
    src/futur/truth/events.py), so a raw, unquantized leg_ledger qty is
    NOT the right "expected" figure for a self-consistency check once
    real ProductSpecs are in play (Phase 4D commit 9 fix): comparing
    Truth's quantized position against legacy's un-quantized float would
    flag a real ~lot_size/2 divergence on almost every leg, even though
    both sides are transcribing the identical row correctly."""
    return instrument.quantize_quantity(row.qty)


_SPOT_LEG_TYPES = frozenset({"LONG_SPOT", "CARRY_LONG_SPOT"})
_PERP_LEG_TYPES = frozenset({"SHORT_HEDGE", "CARRY_SHORT_PERP"})
_DELTA_SIGN = {"LONG_SPOT": 1, "CARRY_LONG_SPOT": 1, "SHORT_HEDGE": -1, "CARRY_SHORT_PERP": -1}


class DifferentialComparator:
    def __init__(self, run_id: str, venue: str, tolerance_config: ToleranceConfig | None = None):
        self.run_id = run_id
        self.venue = venue
        self.tolerance_config = tolerance_config or ToleranceConfig()

    def compare_cycle(self, engine: TruthEngine, applied_events: list[Event],
                      leg_ledger: pd.DataFrame, portfolio_ledger: pd.DataFrame,
                      log: DifferentialLog, account_snapshots: list[dict] | None = None
                      ) -> list[ComparisonRow]:
        """`account_snapshots[i]` must be `Account.snapshot()` taken
        immediately after `applied_events[i]` was applied (same index) --
        see ShadowRunner._observe / ShadowCycleResult.account_snapshots.
        Required for correct per-instrument (timestamp-aware) comparisons:
        a single decide() call applies every event for the whole replay
        window in one batch BEFORE compare_cycle ever runs, so by the time
        this method executes, `engine.account` is already the TERMINAL
        state for every event -- without the per-event snapshot, an
        "as of event i" instrument comparison would silently degrade back
        into "as of the end of the whole window" for every i, exactly the
        bug this method exists to fix. If omitted (e.g. a legacy caller
        with a single-event cycle, where terminal == as-of for that one
        event), falls back to `engine.account` for every event."""
        rows: list[ComparisonRow] = []
        terminal_event: Event | None = None
        terminal_ts: pd.Timestamp | None = None
        event_index = _event_index_map(applied_events)
        for idx, event in enumerate(applied_events):
            if event.event_type not in _STATE_CHANGING_EVENT_TYPES:
                continue
            event_ts = pd.Timestamp(event.ts_event, tz="UTC") if pd.Timestamp(event.ts_event).tz is None \
                else pd.Timestamp(event.ts_event)
            instrument = getattr(event.payload, "instrument", None)
            if instrument is not None:
                asof_snapshot = (account_snapshots[idx] if account_snapshots is not None
                                 else engine.account.__dict__)
                rows.extend(self._compare_instrument_fields(
                    event, instrument, leg_ledger, idx, event_index, asof_snapshot))
            if terminal_ts is None or event_ts >= terminal_ts:
                terminal_ts = event_ts
                terminal_event = event
        if terminal_event is not None:
            rows.extend(self._compare_portfolio_fields(engine, terminal_event, portfolio_ledger))
        for row in rows:
            log.write(row)
        return rows

    # ── per-instrument (mapping self-consistency, application-order-aware) ──

    def _compare_instrument_fields(self, event: Event, instrument, leg_ledger: pd.DataFrame,
                                   as_of_idx: int, event_index: dict[str, int],
                                   asof_account: dict) -> list[ComparisonRow]:
        rows = []
        is_spot = instrument.type.value == "SPOT"
        leg_types = _SPOT_LEG_TYPES if is_spot else _PERP_LEG_TYPES
        open_legs = _legs_open_asof(leg_ledger, instrument.symbol, leg_types, as_of_idx, event_index)
        expected_qty = sum(
            (_DELTA_SIGN[str(r.leg_type)] * _quantized_leg_qty(r, instrument)
             for r in open_legs.itertuples()), start=_ZERO
        ) if len(open_legs) else _ZERO

        if is_spot:
            pos = asof_account["spot_positions"].get(instrument.key)
            actual_qty = pos.quantity if pos is not None else _ZERO
            field, tol = "spot_qty", self.tolerance_config.for_field(self.venue, "spot_qty")
        else:
            pos = asof_account["perp_positions"].get(instrument.key)
            actual_qty = pos.quantity if pos is not None else _ZERO
            field, tol = "perp_qty", self.tolerance_config.for_field(self.venue, "perp_qty")

        rows.append(self._mapping_row(event, field, expected_qty, actual_qty, tol))

        terminal_expected = len(open_legs) == 0
        terminal_actual = (actual_qty == 0)
        rows.append(self._mapping_row(
            event, "terminal_state",
            Decimal(1) if terminal_expected else Decimal(0),
            Decimal(1) if terminal_actual else Decimal(0),
            Decimal(0)))

        if not is_spot and pos is not None and len(open_legs):
            # weighted by the SAME quantized qty Truth's own fill used, and
            # each leg's own price quantized to the real tick grid too --
            # both sides of a self-consistency check must go through the
            # SAME quantization Truth's FillPayload applies at construction.
            quantized = [(abs(_quantized_leg_qty(r, instrument)), instrument.quantize_price(r.entry_price))
                        for r in open_legs.itertuples()]
            total_qty = sum((q for q, _ in quantized), start=_ZERO)
            if total_qty > 0:
                weighted_avg = sum((q * p for q, p in quantized), start=_ZERO) / total_qty
                rows.append(self._mapping_row(
                    event, "entry_price", weighted_avg, pos.avg_entry_price,
                    self.tolerance_config.for_field(self.venue, "entry_price")))
        return rows

    def _mapping_row(self, event: Event, field: str, expected: Decimal, actual: Decimal,
                     tolerance: Decimal) -> ComparisonRow:
        diff = abs(actual - expected)
        classification = "MATCH" if diff <= tolerance else "SHADOW_MAPPING_ERROR"
        cause = "" if classification == "MATCH" else (
            f"{field}: truth ({actual}) disagrees with the source leg_ledger snapshot "
            f"({expected}) used to build this event's own history")
        return ComparisonRow(
            run_id=self.run_id, sequence=event.sequence, event_id=event.event_id,
            timestamp=event.ts_event, field=field, legacy_value=str(expected),
            truth_value=str(actual), difference=str(diff), tolerance_applied=str(tolerance),
            classification=classification, cause=cause)

    # ── portfolio-level (legacy economic comparison) ────────────────────

    def _compare_portfolio_fields(self, engine: TruthEngine, event: Event,
                                  portfolio_ledger: pd.DataFrame) -> list[ComparisonRow]:
        row = _terminal_portfolio_row(portfolio_ledger)
        account = engine.account
        margin_state = compute_margin_state(account, engine.margin_config)
        exposures = compute_exposures(account)
        nav = account.nav()
        nav_for_norm = nav if nav != 0 else Decimal(1)

        fields: list[tuple[str, Decimal | None, Decimal]] = [
            ("cash", to_decimal(row["cash"]) if row is not None else None, account.cash),
            ("nav", to_decimal(row["equity"]) if row is not None else None, nav),
            # fees_total/borrow_total both accumulate NEGATIVELY in legacy's
            # pnl_acc (multileg_backtester.py: `pnl_acc["fees"] -= ...`,
            # same pattern borrow_delta_event's own docstring already
            # documents for borrow) -- Truth's cumulative_fees_paid/
            # cumulative_borrow_paid are both always-positive magnitudes
            # (Account._apply_fee/_apply_borrow_cost only ever ADD the
            # amount), so both legacy columns must be negated to compare
            # like-for-like. Missing this negation for fees (borrow was
            # already correct) produced an exact sign-flip false
            # UNEXPLAINED_DIVERGENCE on the first real replay.
            ("fees", to_decimal(-row["fees_total"]) if row is not None else None,
             account.cumulative_fees_paid),
            ("funding", to_decimal(row["funding_pnl_total"]) if row is not None else None,
             account.cumulative_funding),
            ("borrow", to_decimal(-row["borrow_total"]) if row is not None else None,
             account.cumulative_borrow_paid),
            ("gross_exposure",
             to_decimal(row["gross_exposure"]) if row is not None and "gross_exposure" in row else None,
             to_decimal(exposures.total_gross / nav_for_norm)),
            ("net_exposure",
             to_decimal(row["net_exposure"]) if row is not None and "net_exposure" in row else None,
             to_decimal(exposures.net_exposure / nav_for_norm)),
            ("margin_used", None, margin_state.initial_margin_required),
        ]

        rows = []
        for field, legacy_value, truth_value in fields:
            tol = self.tolerance_config.for_field(self.venue, field)
            classification, diff, cause = _classify(field, legacy_value, truth_value, tol)
            rows.append(ComparisonRow(
                run_id=self.run_id, sequence=event.sequence, event_id=event.event_id,
                timestamp=event.ts_event, field=field,
                legacy_value=str(legacy_value) if legacy_value is not None else "N/A",
                truth_value=str(truth_value), difference=str(diff),
                tolerance_applied=str(tol), classification=classification, cause=cause))
        return rows
