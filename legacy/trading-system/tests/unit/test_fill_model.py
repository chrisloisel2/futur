from pipeline.execution.fill_model import FillModel


def test_fill_prob_bounds():
    model = FillModel()
    p = model.estimate_fill_prob(distance_bps=1, spread_bps=10, imbalance=0.1, rv=0.01)
    assert 0.0 <= p <= 1.0
