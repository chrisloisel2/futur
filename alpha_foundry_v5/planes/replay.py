from __future__ import annotations

import heapq
import json
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple


EXTERNAL_SORT_CHUNK_ROWS = 200_000
VALID_RECORD_KINDS = {"book_events", "trades", "derivatives"}


def _record_paths(root: Path, record_kind: str, venue: str, symbol: str) -> Iterable[Path]:
    if record_kind not in VALID_RECORD_KINDS:
        raise ValueError("unsupported record kind: %s" % record_kind)
    return (
        root
        / "raw"
        / record_kind
        / ("venue=" + str(venue).lower())
        / ("symbol=" + str(symbol).upper())
    ).glob("date=*/events.jsonl")


def _receive_ns_from_line(line: str) -> Optional[int]:
    marker = '"receive_ts_ns"'
    pos = line.find(marker)
    if pos >= 0:
        colon = line.find(":", pos + len(marker))
        if colon >= 0:
            i = colon + 1
            n = len(line)
            while i < n and line[i] in " \t":
                i += 1
            j = i
            if j < n and line[j] == "-":
                j += 1
            while j < n and line[j].isdigit():
                j += 1
            if j > i:
                try:
                    return int(line[i:j])
                except ValueError:
                    pass
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    try:
        return int(row.get("receive_ts_ns", 0) or 0)
    except (TypeError, ValueError):
        return None


def _iter_filtered_lines(path: Path, start_ns: int, stop_ns: int) -> Iterator[Tuple[int, str]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            receive_ns = _receive_ns_from_line(line)
            if receive_ns is None:
                continue
            if int(receive_ns) < int(start_ns) or int(receive_ns) > int(stop_ns):
                continue
            yield int(receive_ns), line


def _receive_ordered(path: Path, start_ns: int, stop_ns: int) -> bool:
    previous = None
    for receive_ns, _line in _iter_filtered_lines(path, start_ns, stop_ns):
        if previous is not None and int(receive_ns) < int(previous):
            return False
        previous = int(receive_ns)
    return True


def _decode(line: str, record_kind: str) -> Optional[Dict[str, object]]:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict):
        return None
    try:
        row["receive_ts_ns"] = int(row["receive_ts_ns"])
    except (KeyError, TypeError, ValueError):
        return None
    row["_source_kind"] = str(record_kind)
    return row


def _direct_records(path: Path, record_kind: str, start_ns: int, stop_ns: int) -> Iterator[Dict[str, object]]:
    for _receive_ns, line in _iter_filtered_lines(path, start_ns, stop_ns):
        row = _decode(line, record_kind)
        if row is not None:
            yield row


def _run_records(path: Path, record_kind: str) -> Iterator[Dict[str, object]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = _decode(line, record_kind)
            if row is not None:
                yield row


def _external_sorted_records(
    path: Path,
    record_kind: str,
    start_ns: int,
    stop_ns: int,
    chunk_rows: int = EXTERNAL_SORT_CHUNK_ROWS,
) -> Iterator[Dict[str, object]]:
    chunk_rows = max(1, int(chunk_rows))
    with tempfile.TemporaryDirectory(prefix="afv5-replay-sort-") as tmp_name:
        tmp = Path(tmp_name)
        runs = []
        chunk = []
        run_id = 0

        def flush() -> None:
            nonlocal chunk, run_id
            if not chunk:
                return
            chunk.sort(key=lambda item: int(item[0]))
            run_path = tmp / ("run-%05d.jsonl" % run_id)
            with run_path.open("w", encoding="utf-8") as out:
                for _receive_ns, line in chunk:
                    out.write(line)
                    out.write("\n")
            runs.append(run_path)
            run_id += 1
            chunk = []

        for receive_ns, line in _iter_filtered_lines(path, start_ns, stop_ns):
            chunk.append((int(receive_ns), line))
            if len(chunk) >= chunk_rows:
                flush()
        flush()

        streams = [_run_records(run, record_kind) for run in runs]
        for row in heapq.merge(*streams, key=lambda x: int(x["receive_ts_ns"])):
            yield row


def _file_records(path: Path, record_kind: str, start_ns: int, stop_ns: int) -> Iterator[Dict[str, object]]:
    if _receive_ordered(path, start_ns, stop_ns):
        for row in _direct_records(path, record_kind, start_ns, stop_ns):
            yield row
        return
    for row in _external_sorted_records(path, record_kind, start_ns, stop_ns):
        yield row


def _partition_records(
    paths: Iterable[Path], record_kind: str, start_ns: int, stop_ns: int
) -> Iterator[Dict[str, object]]:
    physical = tuple(sorted(path for path in paths if path.is_file()))
    streams = [_file_records(path, record_kind, start_ns, stop_ns) for path in physical]
    for row in heapq.merge(*streams, key=lambda x: int(x["receive_ts_ns"])):
        yield row


def iter_merged_records(
    root: str,
    record_kind: str,
    start_ns: int,
    stop_ns: int,
    venues: Sequence[str],
    symbols: Sequence[str],
) -> Iterator[Dict[str, object]]:
    """Replay event-date partitions in strict local receive-time order."""
    base = Path(root)
    streams = []
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        for symbol_raw in symbols:
            symbol = str(symbol_raw).upper()
            streams.append(
                _partition_records(
                    _record_paths(base, record_kind, venue, symbol),
                    record_kind,
                    int(start_ns),
                    int(stop_ns),
                )
            )
    return heapq.merge(*streams, key=lambda x: int(x["receive_ts_ns"]))


def merge_modal_streams(*streams: Iterator[Mapping[str, object]]) -> Iterator[Mapping[str, object]]:
    return heapq.merge(*streams, key=lambda x: int(x["receive_ts_ns"]))
