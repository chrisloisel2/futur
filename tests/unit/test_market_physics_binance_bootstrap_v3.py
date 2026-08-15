import pytest

from market_physics_v3.collectors.binance_bootstrap import (
    BinanceBootstrapError,
    BinanceDepthSnapshot,
    BufferedDepthMessage,
    align_buffer,
    normalized_bootstrap_events,
    snapshot_events,
)
from market_physics_v3.collectors.normalize import BookDeltaState

MS = 1_000_000


def _snapshot(last_update_id=100, receive_ns=10_000 * MS):
    return BinanceDepthSnapshot(
        symbol="BTCUSDT",
        last_update_id=last_update_id,
        event_ts_ns=9_999 * MS,
        receive_ts_ns=receive_ns,
        bids=((100.0, 5.0), (99.0, 4.0)),
        asks=((101.0, 6.0), (102.0, 7.0)),
        raw={"lastUpdateId": last_update_id, "bids": [["100","5"]], "asks": [["101","6"]]},
    )


def _buf(receive_ms, U, u, pu, bid="100", qty="5"):
    return BufferedDepthMessage(
        receive_ts_ns=receive_ms * MS,
        payload={
            "e":"depthUpdate","E":receive_ms-1,"T":receive_ms-1,"s":"BTCUSDT",
            "U":U,"u":u,"pu":pu,"b":[[bid,qty]],"a":[],
        },
    )


def test_align_buffer_drops_old_events_and_bridges_snapshot():
    snap = _snapshot(100)
    rows = [
        _buf(9990, 90, 95, 89),
        _buf(9991, 96, 100, 95),
        _buf(9992, 101, 105, 100),
    ]
    aligned = align_buffer(snap, rows)
    assert [x.payload["u"] for x in aligned] == [100, 105]


def test_align_buffer_rejects_missing_snapshot_bridge():
    snap = _snapshot(100)
    rows = [_buf(9992, 101, 105, 100), _buf(9993, 106, 110, 105)]
    with pytest.raises(BinanceBootstrapError, match="bridge"):
        align_buffer(snap, rows)


def test_align_buffer_rejects_broken_pu_chain():
    snap = _snapshot(100)
    rows = [_buf(9992, 99, 102, 98), _buf(9993, 103, 110, 777)]
    with pytest.raises(BinanceBootstrapError, match="sequence gap"):
        align_buffer(snap, rows)


def test_snapshot_events_have_explicit_rest_deep_provenance():
    snap = _snapshot(100)
    rows = snapshot_events(snap)
    assert len(rows) == 4
    assert {x.event_type for x in rows} == {"snapshot"}
    assert {x.source_stream for x in rows} == {"depth_snapshot_rest"}
    assert {x.sequence_id for x in rows} == {100}
    assert all(x.receive_ts_ns == snap.receive_ts_ns for x in rows)


def test_normalized_bootstrap_clamps_buffered_delta_availability_to_snapshot_receive():
    snap = _snapshot(100, receive_ns=10_000 * MS)
    # Both websocket messages arrived before the REST snapshot, so they cannot
    # be used by a replay before 10_000ms even though raw wire saw them earlier.
    rows = [_buf(9_990, 99, 102, 98, qty="7"), _buf(9_995, 103, 105, 102, qty="8")]
    state = BookDeltaState()
    out = normalized_bootstrap_events(snap, rows, state)
    deltas = [x for x in out if x.source_stream == "depth"]
    assert deltas
    assert all(x.receive_ts_ns == snap.receive_ts_ns for x in deltas)
    assert deltas[-1].sequence_id == 105
    assert state.sequence[("binance", "BTCUSDT", "depth")] == 105
