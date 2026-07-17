from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from data_pipeline.derivatives_positioning import (
    POSITIONING_ENDPOINTS,
    archive_symbol_positioning,
    fetch_positioning_wide,
    latest_stored_timestamp,
)


class FakeClient:
    """Renvoie 3 barres 5m par endpoint, quel que soit le chunk demandé."""

    def __init__(self):
        self.calls = []
        base = int(datetime(2026, 7, 16, tzinfo=timezone.utc).timestamp() * 1000)
        self._timestamps = [base, base + 300_000, base + 600_000]

    def get_json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        endpoint = url.rsplit("/", 1)[-1]
        rename = POSITIONING_ENDPOINTS.get(endpoint)
        if rename is None:
            return []
        rows = []
        for ts in self._timestamps:
            row = {"timestamp": ts, "symbol": params["symbol"]}
            for api_col in rename:
                row[api_col] = "1.5"
            rows.append(row)
        return rows


def test_fetch_positioning_wide_merges_all_endpoints():
    client = FakeClient()
    end = datetime(2026, 7, 16, 1, tzinfo=timezone.utc)
    frame = fetch_positioning_wide(client, "BTCUSDT", period="5m",
                                   start=end - timedelta(hours=2), end=end)

    assert len(frame) == 3
    assert frame["symbol"].unique().tolist() == ["BTCUSDT"]
    for rename in POSITIONING_ENDPOINTS.values():
        for col in rename.values():
            assert col in frame.columns, col
            assert frame[col].notna().all()
    endpoints_hit = {url.rsplit("/", 1)[-1] for url, _ in client.calls}
    assert endpoints_hit == set(POSITIONING_ENDPOINTS)


def test_archive_symbol_positioning_writes_and_resumes(tmp_path: Path, monkeypatch):
    import data_pipeline.derivatives_positioning as mod

    client = FakeClient()
    now = datetime(2026, 7, 16, 1, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)

    written = archive_symbol_positioning(client, tmp_path, "BTCUSDT", period="5m")
    assert written == 3

    last = latest_stored_timestamp(tmp_path, "BTCUSDT", interval="5m")
    assert last == pd.Timestamp("2026-07-16 00:10:00", tz="UTC")

    # Deuxième run : reprend depuis last - 1 barre, dédupe → même nombre de lignes stockées.
    archive_symbol_positioning(client, tmp_path, "BTCUSDT", period="5m")
    stored = pd.read_parquet(
        tmp_path / "binance_futures_positioning" / "futures_um" / "BTCUSDT" / "5m"
        / "year=2026" / "month=07" / "data.parquet"
    )
    assert len(stored) == 3
