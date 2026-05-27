import sys
import unittest

from core.settings import configure_project_imports, get_settings
from core.runtime.profiles import get_training_profile


class SettingsTest(unittest.TestCase):
    def test_default_paths_are_canonical(self) -> None:
        settings = get_settings()

        self.assertTrue(settings.paths.project_root.name == "futur")
        self.assertEqual(settings.paths.pipeline_runs_dir, settings.paths.project_root / "runs" / "pipeline")
        self.assertEqual(settings.paths.research_runs_dir, settings.paths.project_root / "runs" / "research")
        self.assertEqual(
            settings.paths.pipeline_1m_runs_dir,
            settings.paths.project_root / "runs" / "research" / "pipeline_minute",
        )
        self.assertEqual(settings.paths.legacy_runs_dir, settings.paths.project_root / "runs" / "legacy")
        self.assertEqual(settings.paths.local_runs_dir, settings.paths.project_root / "runs" / "legacy" / "local")

    def test_configure_project_imports_is_idempotent(self) -> None:
        root = configure_project_imports()
        root_again = configure_project_imports()

        self.assertEqual(root, root_again)
        self.assertIn(str(root), sys.path)
        self.assertIn(str(root / "legacy"), sys.path)
        self.assertIn(str(root / "legacy" / "ai" / "models"), sys.path)

    def test_profile_registry_matches_canonical_zones(self) -> None:
        self.assertEqual(get_training_profile("pipeline").default_run_root, "runs/pipeline")
        self.assertEqual(get_training_profile("1m").default_run_root, "runs/research/pipeline_minute")
        self.assertEqual(get_training_profile("local").default_run_root, "runs/legacy/local")



if __name__ == "__main__":
    unittest.main()
