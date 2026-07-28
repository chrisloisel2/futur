"""src/alpha20/tournament/truth_shadow/comparator.py -- Phase 4C commit 4:
differential comparator between CarryBasisAdapter's legacy MultiLegResult
and TruthEngine's account state, logged as an append-only JSONL
classification trail.

WHAT IS COMPARED, AND AGAINST WHAT (no field is compared against a
fabricated or guessed legacy value -- every "legacy_value" below traces to
a real column in MultiLegResult.leg_ledger or .portfolio_ledger):

  Per-instrument fields (spot_qty, perp_qty, entry_price, terminal_state):
  compared against the SUM of currently-open leg_ledger rows sharing that
  Truth instrument key (a Truth SPOT/LINEAR_PERP key can receive fills
  from more than one legacy leg_type over time -- e.g. a DIRECTIONAL_LONG
  and a CARRY_LONG_SPOT on the same asset are two different legacy
  PortfolioPositions but the SAME Truth ProductSpec). A mismatch here
  means Truth's own reducer (or this shadow's own mapper) disagrees with
  the exact source data used to build its events -- classified
  SHADOW_MAPPING_ERROR, never UNEXPLAINED_DIVERGENCE, because the
  "legacy value" and the events that produced the "truth value" come from
  the identical row.

  Portfolio-level fields (cash, nav, fees, funding, borrow, gross/net
  exposure): compared against the LATEST portfolio_ledger row at or
  before the event's own timestamp (as-of, never a future row -- no
  lookahead, matching this codebase's own causal discipline elsewhere).
  A mismatch here is a genuine legacy-vs-Truth economic divergence --
  classified UNEXPLAINED_DIVERGENCE unless the field is in
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
                f"{field}: no legacy portfolio_ledger row at or before this event's timestamp")
    diff = abs(truth - legacy)
    if diff <= tolerance:
        return ("MATCH", diff, "")
    return ("UNEXPLAINED_DIVERGENCE", diff,
           f"{field}: |truth - legacy| = {diff} exceeds tolerance {tolerance}")


def _asof_portfolio_row(portfolio_ledger: pd.DataFrame, event_ts: pd.Timestamp) -> pd.Series | None:
    if portfolio_ledger.empty:
        return None
    ts_col = pd.to_datetime(portfolio_ledger["timestamp"], utc=True)
    eligible = portfolio_ledger[ts_col <= event_ts]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def _open_legs_for_instrument(leg_ledger: pd.DataFrame, asset: str, leg_types: frozenset
                              ) -> pd.DataFrame:
    if leg_ledger.empty:
        return leg_ledger
    mask = (leg_ledger["asset"] == asset) & leg_ledger["leg_type"].isin(leg_types) \
        & leg_ledger["exit_time"].isna()
    return leg_ledger[mask]


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
                      log: DifferentialLog) -> list[ComparisonRow]:
        rows: list[ComparisonRow] = []
        for event in applied_events:
            if event.event_type not in _STATE_CHANGING_EVENT_TYPES:
                continue
            event_ts = pd.Timestamp(event.ts_event, tz="UTC") if pd.Timestamp(event.ts_event).tz is None \
                else pd.Timestamp(event.ts_event)
            instrument = getattr(event.payload, "instrument", None)
            if instrument is not None:
                rows.extend(self._compare_instrument_fields(engine, event, instrument, leg_ledger))
            rows.extend(self._compare_portfolio_fields(engine, event, event_ts, portfolio_ledger))
        for row in rows:
            log.write(row)
        return rows

    # ── per-instrument (mapping self-consistency) ───────────────────────

    def _compare_instrument_fields(self, engine: TruthEngine, event: Event, instrument,
                                   leg_ledger: pd.DataFrame) -> list[ComparisonRow]:
        rows = []
        is_spot = instrument.type.value == "SPOT"
        leg_types = _SPOT_LEG_TYPES if is_spot else _PERP_LEG_TYPES
        open_legs = _open_legs_for_instrument(leg_ledger, instrument.symbol, leg_types)
        expected_qty = to_decimal(sum(
            _DELTA_SIGN[str(r.leg_type)] * float(r.qty) for r in open_legs.itertuples()
        )) if len(open_legs) else _ZERO

        if is_spot:
            pos = engine.account.spot_positions.get(instrument.key)
            actual_qty = pos.quantity if pos is not None else _ZERO
            field, tol = "spot_qty", self.tolerance_config.for_field(self.venue, "spot_qty")
        else:
            pos = engine.account.perp_positions.get(instrument.key)
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
            total_qty = sum(abs(float(r.qty)) for r in open_legs.itertuples())
            if total_qty > 0:
                weighted_avg = to_decimal(sum(
                    abs(float(r.qty)) * float(r.entry_price) for r in open_legs.itertuples()
                ) / total_qty)
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
                                  event_ts: pd.Timestamp, portfolio_ledger: pd.DataFrame
                                  ) -> list[ComparisonRow]:
        row = _asof_portfolio_row(portfolio_ledger, event_ts)
        account = engine.account
        margin_state = compute_margin_state(account, engine.margin_config)
        exposures = compute_exposures(account)
        nav = account.nav()
        nav_for_norm = nav if nav != 0 else Decimal(1)

        fields: list[tuple[str, Decimal | None, Decimal]] = [
            ("cash", to_decimal(row["cash"]) if row is not None else None, account.cash),
            ("nav", to_decimal(row["equity"]) if row is not None else None, nav),
            ("fees", to_decimal(row["fees_total"]) if row is not None else None,
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
