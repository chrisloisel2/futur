import unittest

import pandas as pd

from strategies.pipeline_hourly.profile import _ensure_pipeline_macro_features


class PipelineMacroFallbackTest(unittest.TestCase):
    def test_missing_macro_features_are_synthesized(self) -> None:
        df = pd.DataFrame(
            {
                "taker_buy_ratio_base": [0.40, 0.55, 0.60],
                "delta_taker_pressure": [-0.10, 0.05, 0.10],
            },
            index=pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC"),
        )

        enriched = _ensure_pipeline_macro_features(df)

        for column in [
            "funding_rate_z_24",
            "oihist_sumOpenInterest_z_24",
            "fear_greed_value_z_24",
            "global_ls_longShortRatio_z_24",
            "taker_ls_imbalance",
            "taker_ls_buySellRatio_z_24",
            "funding_x_global_ls",
            "oi_x_fng",
        ]:
            self.assertIn(column, enriched.columns)
            self.assertFalse(enriched[column].isna().any())

        self.assertAlmostEqual(float(enriched["taker_ls_imbalance"].iloc[0]), -0.10, places=6)
        self.assertEqual(float(enriched["oi_x_fng"].abs().sum()), 0.0)


if __name__ == "__main__":
    unittest.main()
