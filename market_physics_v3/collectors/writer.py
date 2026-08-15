from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, TextIO

from market_physics_v3.schema import BookEvent, DerivativeEvent, TradeEvent, canonical_partition


def _kind(event):
    if isinstance(event, BookEvent):
        return "book_events"
    if isinstance(event, TradeEvent):
        return "trades"
    if isinstance(event, DerivativeEvent):
        return "derivatives"
    raise TypeError(type(event))


class BufferedJsonlSink:
    """Append-only JSONL sink with bounded durability lag.

    Per-message fsync is catastrophically slow for event-level books. We keep
    one append handle per partition and fdatasync at most once per interval or
    after a bounded row count. On normal shutdown close() forces every buffer.
    A hard power loss can lose at most the unflushed tail; raw wire remains the
    canonical replay source and every normalized record is deterministic.
    """

    def __init__(self, flush_every: int = 512, flush_interval_s: float = 1.0):
        self.flush_every = max(1, int(flush_every))
        self.flush_interval_s = max(0.05, float(flush_interval_s))
        self._handles: Dict[Path, TextIO] = {}
        self._counts: Dict[Path, int] = {}
        self._last_flush: Dict[Path, float] = {}

    def append(self, path: Path, row: dict) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = self._handles.get(path)
        if fh is None or fh.closed:
            fh = open(path, "a", encoding="utf-8", buffering=256 * 1024)
            self._handles[path] = fh
            self._counts[path] = 0
            self._last_flush[path] = time.monotonic()
        fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True, default=str) + "\n")
        self._counts[path] += 1
        now = time.monotonic()
        if self._counts[path] >= self.flush_every or now - self._last_flush[path] >= self.flush_interval_s:
            self.flush_path(path)
        return path

    def flush_path(self, path: Path) -> None:
        fh = self._handles.get(path)
        if fh is None or fh.closed:
            return
        fh.flush()
        try:
            os.fdatasync(fh.fileno())
        except AttributeError:  # pragma: no cover - non-POSIX fallback
            os.fsync(fh.fileno())
        self._counts[path] = 0
        self._last_flush[path] = time.monotonic()

    def close(self) -> None:
        for path in list(self._handles):
            try:
                self.flush_path(path)
            finally:
                self._handles[path].close()
        self._handles.clear()
        self._counts.clear()
        self._last_flush.clear()


class AppendOnlyEventWriter:
    def __init__(self, root, flush_every: int = 512, flush_interval_s: float = 1.0):
        self.root = Path(root)
        self.sink = BufferedJsonlSink(flush_every, flush_interval_s)

    def append(self, event):
        date = datetime.fromtimestamp(event.event_ts_ns / 1e9, tz=timezone.utc).date().isoformat()
        rel = canonical_partition(_kind(event), event.venue, event.symbol, date)
        if self.root.name == "market_physics_v3" and rel.startswith("market_physics_v3/"):
            rel = rel[len("market_physics_v3/") :]
        path = self.root / rel / "events.jsonl"
        row = asdict(event)
        row["_record_type"] = event.__class__.__name__
        return self.sink.append(path, row)

    def close(self) -> None:
        self.sink.close()


class RawMessageWriter:
    def __init__(self, root, flush_every: int = 256, flush_interval_s: float = 1.0):
        self.root = Path(root)
        self.sink = BufferedJsonlSink(flush_every, flush_interval_s)

    def append(self, venue, receive_ns, payload, connection_id):
        date = datetime.fromtimestamp(receive_ns / 1e9, tz=timezone.utc).date().isoformat()
        path = self.root / "raw_wire" / ("venue=" + venue) / ("date=" + date) / "messages.jsonl"
        return self.sink.append(
            path,
            {"receive_ts_ns": int(receive_ns), "connection_id": connection_id, "payload": payload},
        )

    def dead_letter(self, venue, receive_ns, payload, error):
        date = datetime.fromtimestamp(receive_ns / 1e9, tz=timezone.utc).date().isoformat()
        path = self.root / "dead_letters" / ("venue=" + venue) / ("date=" + date) / "errors.jsonl"
        return self.sink.append(
            path,
            {"receive_ts_ns": int(receive_ns), "error": str(error), "payload": payload},
        )

    def close(self) -> None:
        self.sink.close()
