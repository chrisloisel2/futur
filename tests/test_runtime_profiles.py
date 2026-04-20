import unittest

from core.runtime.profiles import format_train_help, get_training_profile, resolve_profile_and_args


class RuntimeProfilesTest(unittest.TestCase):
    def test_aliases_resolve_to_canonical_profiles(self) -> None:
        self.assertEqual(get_training_profile("prod").key, "pipeline")
        self.assertEqual(get_training_profile("minute").key, "1m")
        self.assertEqual(get_training_profile("legacy").key, "local")

    def test_help_mentions_canonical_run_roots(self) -> None:
        help_text = format_train_help()
        self.assertIn("runs/pipeline", help_text)
        self.assertIn("runs/research/pipeline_minute", help_text)
        self.assertIn("runs/legacy/local", help_text)

    def test_default_resolution_is_pipeline(self) -> None:
        self.assertEqual(resolve_profile_and_args([]), ("pipeline", []))
        self.assertEqual(resolve_profile_and_args(["--mode", "combined"]), ("pipeline", ["--mode", "combined"]))


if __name__ == "__main__":
    unittest.main()
