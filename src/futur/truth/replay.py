"""src/futur/truth/replay.py -- deterministic replay from a JSONL fixture.

A fixture is one JSON object per line, each the canonical serialization of
an Event (see event_to_dict/event_from_dict). Replaying means constructing
a fresh TruthEngine and calling engine.apply(event) for every line, in
file order -- the exact same method live processing would use (engine.py's
own docstring: there is no second code path). Determinism therefore isn't
a separate property replay.py has to implement; it falls out of
Ledger/Account/invariants already being deterministic (commits 2-6) plus
this module doing nothing but drive them in a fixed order.

Every Decimal field serializes to a JSON STRING (str(decimal_value)),
never a bare JSON number -- JSON numbers parse back to float by default,
and `Decimal(str(some_float))` is exactly the float round-trip this whole
engine moved to Decimal to avoid. Payload `__post_init__`s already convert
any incoming str/float/int/Decimal safely via numeric.to_decimal(), so
passing the JSON string straight through on the way back in is sufficient.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from src.futur.truth.engine import TruthEngine
from src.futur.truth.events import (
    BorrowCostPayload,
    CashDepositPayload,
    CashWithdrawalPayload,
    Event,
    EventType,
    FeePayload,
    FillPayload,
    FundingPayload,
    LiquidationPayload,
    MarginUpdatePayload,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderCancelledPayload,
    OrderRejectedPayload,
    OrderSubmittedPayload,
    ProductSpec,
    ProductType,
    ReconciliationPayload,
)
from src.futur.truth.margin import MarginConfig

_PAYLOAD_CLASS_FOR_EVENT_TYPE: dict[str, type] = {
    EventType.CASH_DEPOSIT.value: CashDepositPayload,
    EventType.CASH_WITHDRAWAL.value: CashWithdrawalPayload,
    EventType.ORDER_SUBMITTED.value: OrderSubmittedPayload,
    EventType.ORDER_ACKNOWLEDGED.value: OrderAcknowledgedPayload,
    EventType.ORDER_REJECTED.value: OrderRejectedPayload,
    EventType.ORDER_CANCELLED.value: OrderCancelledPayload,
    EventType.FILL.value: FillPayload,
    EventType.MARK.value: MarkPayload,
    EventType.FUNDING.value: FundingPayload,
    EventType.BORROW_COST.value: BorrowCostPayload,
    EventType.FEE.value: FeePayload,
    EventType.MARGIN_UPDATE.value: MarginUpdatePayload,
    EventType.LIQUIDATION.value: LiquidationPayload,
    EventType.RECONCILIATION.value: ReconciliationPayload,
}

_PRODUCT_FIELD_EVENT_TYPES = {
    EventType.ORDER_SUBMITTED.value, EventType.FILL.value, EventType.MARK.value,
    EventType.FUNDING.value, EventType.MARGIN_UPDATE.value, EventType.LIQUIDATION.value,
}


def _json_default(obj: object) -> object:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON-serializable in a fixture: {type(obj)!r}")


def _product_to_dict(product: ProductSpec) -> dict:
    return {
        "venue": product.venue, "symbol": product.symbol,
        "type": product.type.value, "base_ccy": product.base_ccy,
        "quote_ccy": product.quote_ccy, "tick_size": str(product.tick_size),
        "lot_size": str(product.lot_size),
        "multiplier": str(product.multiplier),
    }


def _product_from_dict(d: dict) -> ProductSpec:
    d = dict(d)
    d["type"] = ProductType(d["type"])
    return ProductSpec(**d)


def event_to_dict(event: Event) -> dict:
    payload_dict = dict(vars(event.payload))
    if "instrument" in payload_dict:
        payload_dict["instrument"] = _product_to_dict(payload_dict["instrument"])
    for k, v in list(payload_dict.items()):
        if isinstance(v, Decimal):
            payload_dict[k] = str(v)
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "ts_event": event.ts_event,
        "ts_received": event.ts_received,
        "payload": payload_dict,
    }


def event_from_dict(d: dict) -> Event:
    event_type = EventType(d["event_type"])
    payload_dict = dict(d["payload"])
    if d["event_type"] in _PRODUCT_FIELD_EVENT_TYPES:
        payload_dict["instrument"] = _product_from_dict(payload_dict["instrument"])
    payload_cls = _PAYLOAD_CLASS_FOR_EVENT_TYPE[d["event_type"]]
    return Event(
        event_id=d["event_id"], event_type=event_type,
        ts_event=d["ts_event"], ts_received=d["ts_received"],
        payload=payload_cls(**payload_dict),
    )


def load_events_jsonl(path: Path) -> list[Event]:
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(event_from_dict(json.loads(line)))
    return events


def write_events_jsonl(events: list[Event], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event_to_dict(event), default=_json_default, sort_keys=True))
            f.write("\n")


@dataclass(frozen=True)
class ReplaySummary:
    n_events: int
    final_cash: Decimal
    final_nav: Decimal
    final_ledger_hash: str
    spot_positions: dict
    perp_positions: dict


def summarize(engine: TruthEngine) -> ReplaySummary:
    return ReplaySummary(
        n_events=len(engine.ledger),
        final_cash=engine.account.cash,
        final_nav=engine.account.nav(),
        final_ledger_hash=engine.ledger.head_hash,
        spot_positions={k: v.quantity for k, v in engine.account.spot_positions.items()},
        perp_positions={k: v.quantity for k, v in engine.account.perp_positions.items()},
    )


def replay(events: list[Event], margin_config: MarginConfig | None = None
          ) -> tuple[TruthEngine, ReplaySummary]:
    """Fresh TruthEngine, apply every event in order. Raises whatever
    engine.apply() raises (typically InvariantViolation) on the first
    violation -- replay does not continue past a broken invariant."""
    engine = TruthEngine(margin_config=margin_config or MarginConfig())
    for event in events:
        engine.apply(event)
    return engine, summarize(engine)


def replay_file(path: Path, margin_config: MarginConfig | None = None
               ) -> tuple[TruthEngine, ReplaySummary]:
    return replay(load_events_jsonl(Path(path)), margin_config)
