from __future__ import annotations

import heapq
import json
import re
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

RUN_RE = re.compile(r"run=(\d+)-(\d+)")


def infer_run_window(tape_dir: str) -> Tuple[int, int]:
    path = str(Path(tape_dir))
    match = RUN_RE.search(path)
    if not match:
        raise ValueError("cannot infer run window from path: %s" % path)
    start_ns, stop_ns = int(match.group(1)), int(match.group(2))
    if stop_ns <= start_ns:
        raise ValueError("invalid run window")
    return start_ns, stop_ns


def base_part_paths(tape_dir: str) -> List[Path]:
    parts = sorted(Path(tape_dir).glob("part-*.parquet"))
    if not parts:
        raise ValueError("no part-*.parquet under %s" % tape_dir)
    return parts


def iter_base_key_chunks(tape_dir: str) -> Iterator[pd.DataFrame]:
    for path in base_part_paths(tape_dir):
        frame = pd.read_parquet(path, columns=["asof_ns", "symbol"])
        if not frame.empty:
            yield frame


def _receive_ns_from_line(line: str) -> Optional[int]:
    marker = '"receive_ts_ns"'
    pos = line.find(marker)
    if pos >= 0:
        colon = line.find(":", pos + len(marker))
        if colon >= 0:
            i = colon + 1
            while i < len(line) and line[i] in " \t":
                i += 1
            j = i
            while j < len(line) and (line[j].isdigit() or (j == i and line[j] == "-")):
                j += 1
            if j > i:
                try:
                    return int(line[i:j])
                except ValueError:
                    pass
    try:
        row = json.loads(line)
        return int(row.get("receive_ts_ns", 0) or 0)
    except Exception:
        return None


def _iter_filtered_lines(path: Path, start_ns: int, stop_ns: int) -> Iterator[Tuple[int, str]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            receive_ns = _receive_ns_from_line(line)
            if receive_ns is None or receive_ns < int(start_ns) or receive_ns > int(stop_ns):
                continue
            yield int(receive_ns), line


def _is_receive_ordered(path: Path, start_ns: int, stop_ns: int) -> bool:
    previous = None
    for receive_ns, _line in _iter_filtered_lines(path, start_ns, stop_ns):
        if previous is not None and receive_ns < previous:
            return False
        previous = receive_ns
    return True


def _direct_record_iter(path: Path, start_ns: int, stop_ns: int) -> Iterator[Mapping[str, object]]:
    for _receive_ns, line in _iter_filtered_lines(path, start_ns, stop_ns):
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _run_record_iter(path: Path) -> Iterator[Mapping[str, object]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _external_sorted_record_iter(path: Path, start_ns: int, stop_ns: int, chunk_rows: int = 200000) -> Iterator[Mapping[str, object]]:
    chunk_rows = max(1, int(chunk_rows))
    with tempfile.TemporaryDirectory(prefix="afv5-plane-sort-") as tmp_name:
        tmp = Path(tmp_name)
        runs: List[Path] = []
        chunk: List[Tuple[int, str]] = []
        run_id = 0

        def flush() -> None:
            nonlocal chunk, run_id
            if not chunk:
                return
            chunk.sort(key=lambda item: item[0])
            run_path = tmp / ("run-%05d.jsonl" % run_id)
            with run_path.open("w", encoding="utf-8") as out:
                for _receive_ns, line in chunk:
                    out.write(line + "\n")
            runs.append(run_path)
            run_id += 1
            chunk = []

        for receive_ns, line in _iter_filtered_lines(path, start_ns, stop_ns):
            chunk.append((receive_ns, line))
            if len(chunk) >= chunk_rows:
                flush()
        flush()
        streams = [_run_record_iter(run) for run in runs]
        for row in heapq.merge(*streams, key=lambda x: int(x.get("receive_ts_ns", 0) or 0)):
            yield row


def _ordered_file_iter(path: Path, start_ns: int, stop_ns: int) -> Iterator[Mapping[str, object]]:
    if _is_receive_ordered(path, start_ns, stop_ns):
        for row in _direct_record_iter(path, start_ns, stop_ns):
            yield row
    else:
        print("[afv5-plane] receive-time inversion -> external reorder: %s" % path, flush=True)
        for row in _external_sorted_record_iter(path, start_ns, stop_ns):
            yield row


def event_paths(raw_root: str, event_type: str, venues: Sequence[str], symbols: Sequence[str]) -> Dict[Tuple[str, str], List[Path]]:
    root = Path(raw_root) / "raw" / event_type
    out: Dict[Tuple[str, str], List[Path]] = {}
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        for symbol_raw in symbols:
            symbol = str(symbol_raw).upper()
            paths = sorted((root / ("venue=" + venue) / ("symbol=" + symbol)).glob("date=*/events.jsonl"))
            out[(venue, symbol)] = paths
    return out


def iter_causal_records(raw_root: str, event_type: str, start_ns: int, stop_ns: int, venues: Sequence[str], symbols: Sequence[str]) -> Iterator[Mapping[str, object]]:
    streams: List[Iterator[Mapping[str, object]]] = []
    for _key, paths in event_paths(raw_root, event_type, venues, symbols).items():
        if not paths:
            continue
        file_streams = [_ordered_file_iter(path, start_ns, stop_ns) for path in paths]
        streams.append(heapq.merge(*file_streams, key=lambda x: int(x.get("receive_ts_ns", 0) or 0)))
    return heapq.merge(*streams, key=lambda x: int(x.get("receive_ts_ns", 0) or 0))


class ChunkedPlaneWriter:
    def __init__(self, out_dir: str, chunk_rows: int = 50000):
        self.root = Path(out_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.chunk_rows = max(1, int(chunk_rows))
        self.rows: List[Mapping[str, object]] = []
        self.part = 0
        self.total = 0
        self.columns = set()

    def append(self, row: Mapping[str, object]) -> None:
        self.rows.append(dict(row))
        self.columns.update(row.keys())
        if len(self.rows) >= self.chunk_rows:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        path = self.root / ("part-%05d.parquet" % self.part)
        pd.DataFrame(self.rows).to_parquet(path, index=False)
        self.total += len(self.rows)
        print("[afv5-plane] wrote %s rows_total=%d" % (path.name, self.total), flush=True)
        self.part += 1
        self.rows = []

    def close(self, metadata: Optional[Mapping[str, object]] = None) -> Mapping[str, object]:
        self.flush()
        summary = {"rows": int(self.total), "parts": int(self.part), "columns": sorted(self.columns)}
        if metadata:
            summary.update(dict(metadata))
        (self.root / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
        (self.root / "_SUCCESS").write_text("ok\n", encoding="utf-8")
        return summary
