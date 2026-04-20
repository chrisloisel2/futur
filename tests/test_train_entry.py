import unittest

from train import resolve_profile_and_args


class TrainEntryTest(unittest.TestCase):
    def test_defaults_to_pipeline(self) -> None:
        profile, args = resolve_profile_and_args(["--data", "foo.parquet", "--mode", "combined"])
        self.assertEqual(profile, "pipeline")
        self.assertEqual(args, ["--data", "foo.parquet", "--mode", "combined"])

    def test_explicit_profiles_are_supported(self) -> None:
        self.assertEqual(resolve_profile_and_args(["pipeline", "--mode", "long"]), ("pipeline", ["--mode", "long"]))
        self.assertEqual(resolve_profile_and_args(["1m", "--wf"]), ("1m", ["--wf"]))
        self.assertEqual(resolve_profile_and_args(["minute", "--wf"]), ("1m", ["--wf"]))
        self.assertEqual(resolve_profile_and_args(["local", "--data", "x.csv"]), ("local", ["--data", "x.csv"]))

    def test_help_is_detected(self) -> None:
        self.assertEqual(resolve_profile_and_args(["--help"]), ("__help__", []))
        self.assertEqual(resolve_profile_and_args(["help"]), ("__help__", []))


if __name__ == "__main__":
    unittest.main()
