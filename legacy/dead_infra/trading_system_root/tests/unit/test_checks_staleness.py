import pandas as pd

from pipeline.quality.checks import StalenessCheck
from domain.state.quality import QualityFlag


def test_staleness_sets_flag():
    df = pd.DataFrame({"staleness_ms": [10_000, 40_000]})
    check = StalenessCheck(max_staleness_ms=20_000)
    out = check.apply(df)
    assert (out.loc[1, "quality_flags"] & int(QualityFlag.STALE_EVENT)) > 0
    assert (out.loc[0, "quality_flags"] & int(QualityFlag.STALE_EVENT)) == 0
