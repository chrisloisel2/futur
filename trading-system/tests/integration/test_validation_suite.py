import pandas as pd

from pipeline.research.validation import ValidationConfig, ValidationSuite


def test_validation_suite_outputs_metrics(tmp_path):
    trades = pd.DataFrame(
        {
            "t_entry": pd.date_range("2024-01-01", periods=3, freq="1H"),
            "t_exit": pd.date_range("2024-01-01", periods=3, freq="1H"),
            "qty": [1.0, 1.0, 1.0],
            "entry_px": [100.0, 101.0, 99.0],
            "gross_pnl": [1.0, -1.0, 2.0],
            "net_pnl": [0.8, -1.2, 1.5],
            "slippage": [0.1, 0.1, 0.1],
            "symbol": "BTCUSDT",
        }
    )
    equity = pd.DataFrame({"event_time": trades["t_exit"], "equity": trades["net_pnl"].cumsum(), "drawdown": 0})
    labels = pd.DataFrame(
        {
            "t0": trades["t_entry"],
            "symbol": "BTCUSDT",
            "horizon_s": 60,
            "return_fwd": [0.01, -0.01, 0.02],
        }
    )
    features = pd.DataFrame({"event_time": trades["t_entry"], "symbol": "BTCUSDT", "prob_edge": [0.6, 0.4, 0.7]})
    suite = ValidationSuite(ValidationConfig(report_path=str(tmp_path)))
    metrics = suite.run(features, labels, trades, equity, run_id="test", output_dir=tmp_path)
    assert "leakage_rate" in metrics
    assert (tmp_path / "validation_report.md").exists()
