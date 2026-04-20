import pandas as pd

from pipeline.quality.checks import OutlierCheck
from domain.state.quality import QualityFlag


def test_outlier_price_flagged():
    df = pd.DataFrame({"price": [100, 101, 102, 5000]})
    check = OutlierCheck(zscore_threshold=3.5, window=3)
    out = check.apply(df)
    assert (out.loc[3, "quality_flags"] & int(QualityFlag.OUTLIER_PRICE)) > 0
