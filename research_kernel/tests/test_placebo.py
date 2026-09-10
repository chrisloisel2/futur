from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_kernel.placebo import PlaceboError, PlaceboSuite, placebo_gate


def decisions(gross, symbol="BTCUSDT", episodes=None):
    n = len(gross)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="1D", tz="UTC"),
            "symbol": symbol,
            "gross_bps": np.asarray(gross, dtype=float),
            "episode_id": episodes or [str(i) for i in range(n)],
            "side": 1,
        }
    )


def panel(n=500, symbol="BTCUSDT", loc=0.0, scale=50.0, seed=9):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="1D", tz="UTC"),
            "symbol": symbol,
            "fwd_bps": rng.normal(loc, scale, n),
        }
    )


def test_noise_does_not_beat_its_own_null():
    rng = np.random.default_rng(1)
    d = decisions(rng.normal(0, 50, 400))
    results = PlaceboSuite(d, n_draws=400).run(["direction_flip"])
    assert results[0].percentile < 95
    assert placebo_gate(results)


def test_a_real_edge_beats_the_flip_null():
    rng = np.random.default_rng(2)
    d = decisions(rng.normal(40, 20, 400))
    results = PlaceboSuite(d, n_draws=400).run(["direction_flip", "same_event_random_side"])
    assert all(r.percentile >= 95 for r in results)
    assert placebo_gate(results) == []


def test_the_null_interval_is_reported_and_is_not_zero():
    rng = np.random.default_rng(3)
    d = decisions(rng.normal(0, 120, 49))
    r = PlaceboSuite(d, n_draws=500).run(["direction_flip"])[0]
    assert r.null_p2_5_bps < 0 < r.null_p97_5_bps
    assert r.null_p97_5_bps - r.null_p2_5_bps > 10


def test_a_missing_panel_fails_closed():
    d = decisions([10.0] * 100)
    results = PlaceboSuite(d, n_draws=50).run(["time_shuffle", "symbol_shuffle"])
    assert all(r.status == "SKIPPED_NO_PANEL" for r in results)
    failures = placebo_gate(results)
    assert len(failures) == 2
    assert all("did not run" in f for f in failures)


def test_the_shuffles_run_when_a_panel_is_supplied():
    rng = np.random.default_rng(4)
    d = decisions(rng.normal(40, 20, 200))
    results = PlaceboSuite(d, panel=panel(), n_draws=200).run(
        ["time_shuffle", "entry_time_randomization"]
    )
    assert all(r.status == "RUN" for r in results)
    assert all(r.n_draws == 200 for r in results)


def test_clustered_decisions_are_flipped_as_one_block():
    rng = np.random.default_rng(5)
    gross = rng.normal(30, 10, 200)
    eps = [str(i // 20) for i in range(200)]
    per_decision = PlaceboSuite(decisions(gross, episodes=eps), n_draws=400).run(
        ["direction_flip"]
    )[0]
    per_episode = PlaceboSuite(decisions(gross, episodes=eps), n_draws=400).run(
        ["same_event_random_side"]
    )[0]
    spread_decision = per_decision.null_p97_5_bps - per_decision.null_p2_5_bps
    spread_episode = per_episode.null_p97_5_bps - per_episode.null_p2_5_bps
    assert spread_episode > spread_decision


def test_an_unknown_test_is_refused():
    with pytest.raises(PlaceboError):
        PlaceboSuite(decisions([1.0] * 10), n_draws=10).run(["hope"])


def test_a_missing_column_is_refused():
    bad = decisions([1.0] * 10).drop(columns=["episode_id"])
    with pytest.raises(PlaceboError):
        PlaceboSuite(bad)
