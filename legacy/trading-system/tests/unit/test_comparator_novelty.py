import pandas as pd

from domain.signal.comparator import compute_novelty


def test_novelty_zero_if_no_ref():
    df = pd.DataFrame({"a": [1.0]})
    score = compute_novelty(df, {})
    assert score == 0.0
