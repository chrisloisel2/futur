import numpy as np

from pipeline.monitoring.drift.data_drift import _psi


def test_psi_nonnegative():
    psi = _psi(np.array([1,2,3]), np.array([1,2,3]))
    assert psi >= 0
