from market_physics_v3.collectors.normalize import BookDeltaState, parse_hyperliquid

R = 2_000_000_000_000_000_000


def test_hyperliquid_preserves_level_order_count():
    state = BookDeltaState()
    out = parse_hyperliquid(
        {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": 1002,
                "levels": [
                    [{"px": "100", "sz": "2", "n": 7}],
                    [{"px": "101", "sz": "3", "n": 4}],
                ],
            },
        },
        R,
        state,
    )
    assert out[0].order_count == 7
    assert out[1].order_count == 4


def test_hyperliquid_bbo_preserves_order_count_without_wiping_deep_state():
    state = BookDeltaState()
    parse_hyperliquid(
        {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": 1002,
                "levels": [
                    [{"px": "99", "sz": "5", "n": 3}],
                    [{"px": "102", "sz": "4", "n": 2}],
                ],
            },
        },
        R,
        state,
    )
    out = parse_hyperliquid(
        {
            "channel": "bbo",
            "data": {
                "coin": "BTC",
                "time": 1003,
                "bbo": [
                    {"px": "100", "sz": "2", "n": 6},
                    {"px": "101", "sz": "3", "n": 5},
                ],
            },
        },
        R,
        state,
    )
    assert out[0].order_count == 6
    assert out[1].order_count == 5
    assert ("hyperliquid", "BTCUSDT", "bid", 99.0) in state.levels


def test_hyperliquid_trade_uses_global_identity_and_wallets():
    state = BookDeltaState()
    msg = {
        "channel": "trades",
        "data": [
            {
                "coin": "BTC",
                "time": 1003,
                "side": "B",
                "px": "100",
                "sz": "1",
                "tid": 7,
                "hash": "0xabc",
                "users": ["0xbuyer", "0xseller"],
            }
        ],
    }
    event = parse_hyperliquid(msg, R, state)[0]
    assert event.trade_id == "1003:BTCUSDT:7"
    assert event.buyer == "0xbuyer"
    assert event.seller == "0xseller"
    assert event.tx_hash == "0xabc"
    assert event.aggressor == "buy"


def test_hyperliquid_same_tid_different_block_time_is_not_duplicate():
    state = BookDeltaState()
    base = {
        "coin": "BTC",
        "side": "A",
        "px": "100",
        "sz": "1",
        "tid": 7,
        "hash": "0xabc",
        "users": ["0xbuyer", "0xseller"],
    }
    first = dict(base, time=1003)
    second = dict(base, time=1004, hash="0xdef")
    a = parse_hyperliquid({"channel": "trades", "data": [first]}, R, state)[0]
    b = parse_hyperliquid({"channel": "trades", "data": [second]}, R, state)[0]
    assert a.trade_id != b.trade_id
    assert a.aggressor == "sell"
