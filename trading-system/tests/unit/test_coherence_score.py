from pipeline.meta_control.coherence import compute_coherence


def test_coherence_penalizes_stress():
    score = compute_coherence({"a": 0.5, "b": 0.5}, {"q50": 0.1, "q05": -0.1, "q95": 0.2}, {"flash_crash": True})
    assert score < 1.0
