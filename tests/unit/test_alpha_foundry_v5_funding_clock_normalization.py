import pytest
from market_physics_v3.collectors.normalize import BookDeltaState, parse_bybit, parse_okx
from market_physics_v3.schema import DerivativeEvent


def test_bybit_ticker_persists_next_funding_time_ns():
    msg = {
        'topic': 'tickers.BTCUSDT',
        'type': 'snapshot',
        'ts': 1760325052630,
        'data': {
            'symbol': 'BTCUSDT',
            'fundingRate': '-0.005',
            'nextFundingTime': '1760342400000',
            'openInterest': '492373.72',
            'markPrice': '66666.60',
            'indexPrice': '66660.00',
        },
    }
    rows = parse_bybit(msg, 1760325052640000000, BookDeltaState())
    funding = [row for row in rows if row.kind == 'funding'][0]
    assert funding.next_funding_ts_ns == 1760342400000000000


def test_okx_funding_uses_nearest_future_boundary_and_persists_premium():
    msg = {
        'arg': {'channel': 'funding-rate', 'instId': 'BTC-USDT-SWAP'},
        'data': [{
            'instId': 'BTC-USDT-SWAP',
            'ts': '1700724675402',
            'fundingRate': '0.0001',
            'fundingTime': '1700726400000',
            'nextFundingTime': '1700755200000',
            'premium': '0.0002',
        }],
    }
    rows = parse_okx(msg, 1700724675500000000, BookDeltaState())
    funding = [row for row in rows if row.kind == 'funding'][0]
    premium = [row for row in rows if row.kind == 'premium'][0]
    assert funding.next_funding_ts_ns == 1700726400000000000
    assert premium.value == 0.0002


def test_next_funding_metadata_is_funding_only_and_causal():
    with pytest.raises(ValueError):
        DerivativeEvent('okx', 'BTCUSDT', 100, 110, 'mark', 1.0, next_funding_ts_ns=120)
    with pytest.raises(ValueError):
        DerivativeEvent('okx', 'BTCUSDT', 100, 110, 'funding', 0.001, next_funding_ts_ns=99)
