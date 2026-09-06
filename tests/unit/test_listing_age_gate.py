"""tests/test_listing_age_gate.py — point-in-time + conservatisme du gate d'âge de listing."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.institutional.portfolio.listing_age_gate import ListingAgeGate


def _calendar(tmp_path: Path) -> Path:
    cal = pd.DataFrame({
        "symbol": ["OLDUSDT", "NEWUSDT", "NODATEUSDT"],
        "onboard_ts": [pd.Timestamp("2024-01-01", tz="UTC"),
                       pd.Timestamp("2026-07-01", tz="UTC"),
                       pd.NaT],
        "status": ["TRADING", "TRADING", "DELISTED_NO_DATA"],
    })
    p = tmp_path / "cal.parquet"
    cal.to_parquet(p)
    return p


def test_young_listing_blocked_then_allowed(tmp_path):
    g = ListingAgeGate(min_age_days=30, calendar_path=_calendar(tmp_path))
    assert not g.allows("NEWUSDT", pd.Timestamp("2026-07-15", tz="UTC"))   # J+14
    assert not g.allows("NEWUSDT", pd.Timestamp("2026-07-30 23:00", tz="UTC"))  # J+29
    assert g.allows("NEWUSDT", pd.Timestamp("2026-07-31", tz="UTC"))       # J+30 exact
    assert g.allows("NEWUSDT", pd.Timestamp("2026-09-01", tz="UTC"))


def test_old_listing_always_allowed(tmp_path):
    g = ListingAgeGate(min_age_days=30, calendar_path=_calendar(tmp_path))
    assert g.allows("OLDUSDT", pd.Timestamp("2026-07-18", tz="UTC"))


def test_unknown_or_undated_symbol_blocked(tmp_path):
    # absent du calendrier = probablement listé après la dernière régénération → bloqué
    g = ListingAgeGate(min_age_days=30, calendar_path=_calendar(tmp_path))
    assert not g.allows("GHOSTUSDT", pd.Timestamp("2026-07-18", tz="UTC"))
    assert not g.allows("NODATEUSDT", pd.Timestamp("2026-07-18", tz="UTC"))


def test_missing_calendar_blocks_everything(tmp_path):
    g = ListingAgeGate(min_age_days=30, calendar_path=tmp_path / "absent.parquet")
    assert g.n_known == 0
    assert not g.allows("BTCUSDT", pd.Timestamp("2026-07-18", tz="UTC"))
