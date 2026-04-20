from pipeline.execution.slippage import SlippageModel


def test_expected_slippage_positive():
    model = SlippageModel()
    sl = model.expected_slippage_bps(depth_usd=100000, order_notional_usd=1000, rv=0.001)
    assert sl > 0
