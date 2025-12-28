from pipeline.execution.order_events import OrderEvent, OrderEvents


def test_order_events_container():
    ev = OrderEvent(event_time=None, event_type="SEND", symbol="BTC", client_order_id="id", exchange_order_id=None, details={})
    events = OrderEvents(event_time=None, events=[ev])
    assert events.events[0].event_type == "SEND"
