import numpy as np

from pipeline.monitoring.drift.data_drift import _js


def test_js_nonnegative():
    js = _js(np.array([1,2,3]), np.array([1,2,3]))
    assert js >= 0
