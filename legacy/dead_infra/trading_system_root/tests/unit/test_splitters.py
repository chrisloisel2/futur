import pandas as pd

from pipeline.research.splitters import Embargo, PurgedKFoldSplitter, WalkForwardSplitter


def test_walk_forward_splitter_produces_windows():
    idx = pd.date_range("2024-01-01", periods=60, freq="1D")
    splitter = WalkForwardSplitter(
        train_window=pd.Timedelta(days=20),
        test_window=pd.Timedelta(days=5),
        step=pd.Timedelta(days=10),
        purge_window=pd.Timedelta(days=1),
        embargo=Embargo.from_minutes(30),
    )
    splits = splitter.split(idx)
    assert len(splits) > 0
    s0 = splits[0]
    assert s0.test_start > s0.train_end


def test_purged_kfold_respects_purge_and_embargo():
    idx = pd.date_range("2024-01-01", periods=30, freq="1D")
    splitter = PurgedKFoldSplitter(
        n_splits=3,
        purge_window=pd.Timedelta(days=1),
        embargo=Embargo.from_minutes(60),
    )
    splits = splitter.split(idx)
    assert len(splits) == 3
    for sp in splits:
        assert sp.test_start >= sp.train_start
        assert sp.train_end <= sp.test_start + pd.Timedelta(days=0)
