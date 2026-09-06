from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from data_pipeline.derivatives_positioning import (
    POSITIONING_ENDPOINTS,
    archive_symbol_positioning,
    fetch_positioning_wide,
    latest_stored_timestamp,
)


# ⚠ Ces tests étaient figés sur des dates ABSOLUES (2026-07-16) alors que
# `fetch_positioning_wide` borne son `start` à un plancher de rétention
# RELATIF à `now` (`now - RETENTION_DAYS`). Passé ce délai, `start` était
# repoussé au-delà de `end` codé en dur et la fonction retournait un frame vide
# -- le test échouait donc à partir du 2026-08-15 environ, pour une raison de
# calendrier et non de code. Un test qui pourrit avec la date échoue ensuite
# pour toujours et cesse d'être lu ; c'est ainsi qu'une suite passe au rouge
# permanent. Les repères sont désormais relatifs à `now`.
_NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
_WINDOW_END = _NOW
_WINDOW_START = _NOW - timedelta(hours=2)


class FakeClient:
    """Renvoie 3 barres 5m par endpoint, quel que soit le chunk demandé."""

    def __init__(self):
        self.calls = []
        base = int((_WINDOW_START + timedelta(minutes=30)).timestamp() * 1000)
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
    frame = fetch_positioning_wide(client, "BTCUSDT", period="5m",
                                   start=_WINDOW_START, end=_WINDOW_END)

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
    # L'horloge gelée doit rester COHÉRENTE avec les barres du FakeClient, qui
    # sont désormais relatives à `now` (voir la note en tête de fichier) --
    # sinon le plancher de rétention repousse `start` au-delà de `end` et rien
    # n'est archivé.
    now = _WINDOW_END

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(mod, "datetime", FrozenDatetime)

    written = archive_symbol_positioning(client, tmp_path, "BTCUSDT", period="5m")
    assert written == 3

    expected_last = pd.Timestamp(_WINDOW_START + timedelta(minutes=40))
    last = latest_stored_timestamp(tmp_path, "BTCUSDT", interval="5m")
    assert last == expected_last

    # Deuxième run : reprend depuis last - 1 barre, dédupe → même nombre de lignes stockées.
    archive_symbol_positioning(client, tmp_path, "BTCUSDT", period="5m")
    part = (tmp_path / "binance_futures_positioning" / "futures_um" / "BTCUSDT" / "5m"
            / f"year={expected_last:%Y}" / f"month={expected_last:%m}" / "data.parquet")
    stored = pd.read_parquet(part)
    assert len(stored) == 3
