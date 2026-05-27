import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.artifacts import validate_pipeline_label_stats
from production.dashboard.data import load_pipeline_dashboard_snapshot


class ProductionSurfacesTest(unittest.TestCase):
    def test_dashboard_snapshot_reads_canonical_files(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "pipeline" / "20260419-120000"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text('{"run_id": "20260419-120000"}', encoding="utf-8")
            (run_dir / "config.json").write_text('{"pipeline_config": {"direction_threshold_long": 0.55}}', encoding="utf-8")
            (run_dir / "pipeline_summary.json").write_text('{"elapsed_sec": 12.3}', encoding="utf-8")

            snapshot = load_pipeline_dashboard_snapshot(run_dir)

            self.assertEqual(snapshot["manifest"]["run_id"], "20260419-120000")
            self.assertEqual(snapshot["config"]["direction_threshold_long"], 0.55)
            self.assertEqual(snapshot["summary"]["elapsed_sec"], 12.3)

    def test_pipeline_label_stats_validator_accepts_canonical_stats(self) -> None:
        validate_pipeline_label_stats(
            {
                "n_total": 100,
                "n_tradeable": 25,
                "n_long": 10,
                "n_short": 8,
                "thr_long": 0.01,
                "thr_short_with_cost": 0.02,
            }
        )


if __name__ == "__main__":
    unittest.main()
