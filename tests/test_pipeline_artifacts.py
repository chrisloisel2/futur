import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.artifacts.pipeline import (
    component_enabled,
    find_latest_pipeline_run,
    load_pipeline_config,
    resolve_edge_component,
    resolve_edge_threshold,
    resolve_filter_component,
    resolve_filter_thresholds,
)


class PipelineArtifactsTest(unittest.TestCase):
    def test_resolve_canonical_component_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "runs" / "pipeline" / "20260419-120000"
            edge_long = run_dir / "edge_long"
            edge_long.mkdir(parents=True)
            (run_dir / "filter").mkdir(parents=True)

            (edge_long / "model.pkl").write_bytes(b"model")
            (edge_long / "scaler.pkl").write_bytes(b"scaler")
            (edge_long / "metadata.json").write_text(
                '{"threshold": 0.61, "features": ["a", "b"], "enabled_for_inference": true}',
                encoding="utf-8",
            )
            (run_dir / "filter" / "model.pkl").write_bytes(b"model")
            (run_dir / "filter" / "scaler.pkl").write_bytes(b"scaler")
            (run_dir / "filter" / "metadata.json").write_text(
                '{"threshold_long": 0.42, "threshold_short": 0.47, "features": ["f1"]}',
                encoding="utf-8",
            )
            (run_dir / "config.json").write_text(
                '{"pipeline_config": {"direction_threshold_long": 0.55, "filter_threshold_long": 0.4, "filter_threshold_short": 0.45}}',
                encoding="utf-8",
            )

            filter_component = resolve_filter_component(run_dir)
            long_component = resolve_edge_component(run_dir, "long")

            self.assertEqual(filter_component.model, run_dir / "filter" / "model.pkl")
            self.assertEqual(long_component.model, edge_long / "model.pkl")
            self.assertEqual(
                resolve_filter_thresholds(run_dir, {"threshold_long": 0.42, "threshold_short": 0.47}),
                (0.42, 0.47),
            )
            self.assertEqual(resolve_edge_threshold(run_dir, "long", {"threshold": 0.61}, 0.55), 0.61)
            self.assertTrue(component_enabled({"enabled_for_inference": True}))

    def test_resolve_legacy_component_layout_and_find_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_root = tmp_path / "runs" / "pipeline"
            older = runs_root / "20260418-120000"
            latest = runs_root / "20260419-120000"

            for run_dir in (older, latest):
                (run_dir / "filter").mkdir(parents=True)
                (run_dir / "long").mkdir(parents=True)
                (run_dir / "filter" / "filter_model.pkl").write_bytes(b"model")
                (run_dir / "filter" / "filter_scaler.pkl").write_bytes(b"scaler")
                (run_dir / "long" / "best_model.pkl").write_bytes(b"model")
                (run_dir / "long" / "scaler.pkl").write_bytes(b"scaler")

            (latest / "pipeline_summary.json").write_text(
                '{"config": {"direction_threshold_long": 0.58}}',
                encoding="utf-8",
            )

            latest_run = find_latest_pipeline_run(tmp_path)
            self.assertEqual(latest_run, latest)

            long_component = resolve_edge_component(latest, "long")
            self.assertEqual(long_component.model, latest / "long" / "best_model.pkl")
            self.assertEqual(load_pipeline_config(latest)["direction_threshold_long"], 0.58)
            self.assertFalse(component_enabled({"enabled_for_inference": False}))


if __name__ == "__main__":
    unittest.main()
