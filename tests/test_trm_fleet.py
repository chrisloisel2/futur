import unittest
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
root_str = str(ROOT)
if root_str in sys.path:
    sys.path.remove(root_str)
sys.path.insert(0, root_str)
for module_name, module in list(sys.modules.items()):
    if module_name == "ai" or module_name.startswith("ai."):
        del sys.modules[module_name]

from ai.level_2.tiny_specialists import (
    CONTEXT_NAMES,
    TRM_FLEET_SIZE,
    TRMFleet,
    build_specialist_scores,
    classify_context,
)


FEATURES = [
    "mom_logret_4",
    "mom_logret_12",
    "mom_logret_24",
    "mom_logret_72",
    "rv_ratio_24_72",
    "rv_ratio_12_48",
    "boll_width_20",
    "boll_pos_20",
    "dist_ema_20",
    "dist_ema_50",
    "dist_ema_200",
    "ema_spread_20_50",
    "ema_spread_50_200",
    "above_vwap_4h",
    "dist_vwap_pct",
    "taker_buy_ratio_base",
    "delta_taker_pressure",
    "vol_imbalance",
    "liq_short_spike_12",
    "liq_imbalance",
    "rsi_14",
]


def _make_market_df(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    t = np.arange(n, dtype=np.float64)
    hourly_ret = 0.0004 * np.sin(t / 9.0) + 0.0002 * (t > n // 2) + rng.normal(0, 0.004, n)
    close = 100.0 * np.exp(np.cumsum(hourly_ret))
    volume = 1_000 + 250 * np.sin(t / 13.0) + rng.normal(0, 80, n)
    volume = np.maximum(volume, 50)

    df = pd.DataFrame({"close": close, "volume": volume})
    log_close = np.log(df["close"])
    for w in (4, 12, 24, 72):
        df[f"mom_logret_{w}"] = log_close.diff(w).fillna(0.0)

    ret_1 = log_close.diff().fillna(0.0)
    rv_12 = ret_1.rolling(12, min_periods=2).std().bfill().fillna(0.001)
    rv_24 = ret_1.rolling(24, min_periods=2).std().bfill().fillna(0.001)
    rv_48 = ret_1.rolling(48, min_periods=2).std().bfill().fillna(0.001)
    rv_72 = ret_1.rolling(72, min_periods=2).std().bfill().fillna(0.001)
    df["rv_ratio_24_72"] = rv_24 / rv_72.replace(0, np.nan)
    df["rv_ratio_12_48"] = rv_12 / rv_48.replace(0, np.nan)

    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    ema50 = df["close"].ewm(span=50, adjust=False).mean()
    ema200 = df["close"].ewm(span=120, adjust=False).mean()
    df["dist_ema_20"] = df["close"] / ema20 - 1.0
    df["dist_ema_50"] = df["close"] / ema50 - 1.0
    df["dist_ema_200"] = df["close"] / ema200 - 1.0
    df["ema_spread_20_50"] = ema20 / ema50 - 1.0
    df["ema_spread_50_200"] = ema50 / ema200 - 1.0

    roll_mean = df["close"].rolling(20, min_periods=2).mean().bfill()
    roll_std = df["close"].rolling(20, min_periods=2).std().bfill().replace(0, np.nan)
    df["boll_width_20"] = (4.0 * roll_std / roll_mean).fillna(0.02)
    df["boll_pos_20"] = ((df["close"] - (roll_mean - 2 * roll_std)) / (4 * roll_std)).fillna(0.5)
    df["above_vwap_4h"] = (df["close"] > ema20).rolling(4, min_periods=1).mean()
    df["dist_vwap_pct"] = df["close"] / ema20 - 1.0
    df["taker_buy_ratio_base"] = 0.5 + 0.08 * np.sin(t / 7.0)
    df["delta_taker_pressure"] = df["taker_buy_ratio_base"].diff().fillna(0.0)
    df["vol_imbalance"] = df["taker_buy_ratio_base"] - 0.5
    df["liq_short_spike_12"] = np.maximum(df["mom_logret_4"], 0.0) * 25.0
    df["liq_imbalance"] = -df["liq_short_spike_12"]
    df["rsi_14"] = 50.0 + np.clip(df["mom_logret_24"] * 900.0, -25.0, 25.0)

    fwd = log_close.shift(-4) - log_close
    df["future_ret_4h"] = fwd.fillna(0.0)
    df["y_long"] = (df["future_ret_4h"] > df["future_ret_4h"].quantile(0.62)).astype(int)
    return df.replace([np.inf, -np.inf], 0.0).fillna(0.0)


class TRMFleetTest(unittest.TestCase):
    def test_default_fleet_has_real_50_to_100_specialists(self) -> None:
        self.assertGreaterEqual(TRM_FLEET_SIZE, 50)
        self.assertLessEqual(TRM_FLEET_SIZE, 100)
        self.assertEqual(TRM_FLEET_SIZE, len(CONTEXT_NAMES))
        self.assertEqual(TRM_FLEET_SIZE, len(set(CONTEXT_NAMES)))
        self.assertIn("y01_breakout_escape", CONTEXT_NAMES)
        self.assertIn("mo01_vwap_accum", CONTEXT_NAMES)

    def test_specialist_scores_are_multi_horizon_and_contextual(self) -> None:
        df = _make_market_df()
        scores = build_specialist_scores(df)
        self.assertEqual(scores.shape, (len(df), TRM_FLEET_SIZE - 1))
        self.assertTrue(np.isfinite(scores.to_numpy()).all())
        self.assertTrue(((scores.to_numpy() >= 0.0) & (scores.to_numpy() <= 1.0)).all())

        ctx = classify_context(df)
        self.assertEqual(len(ctx), len(df))
        self.assertTrue(set(ctx).issubset(set(CONTEXT_NAMES)))
        self.assertGreater(int((ctx != "general").sum()), 0)

    def test_small_fleet_trains_and_routes_predictions(self) -> None:
        df = _make_market_df()
        fleet = TRMFleet(
            features=FEATURES,
            n_recursive_rounds=1,
            max_specialists=6,
            routing_top_k=2,
            min_specialist_rows=30,
        )
        mask = np.ones(len(df), dtype=bool)
        fleet.train(df, mask, df_val_btc=df, val_mask_in_btc=mask)

        pred = fleet.predict(df, mask)
        self.assertEqual(pred.shape, (len(df),))
        self.assertTrue(np.isfinite(pred).all())
        self.assertTrue(((pred >= 0.0) & (pred <= 1.0)).all())
        self.assertIsNotNone(fleet.specialists["general"].clf_)


if __name__ == "__main__":
    unittest.main()
