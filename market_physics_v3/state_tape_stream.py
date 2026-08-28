from __future__ import annotations

import heapq
import json
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

import pandas as pd

from .microstructure import book_feature_vector, top_of_book_ofi
from .schema import BookEvent
from .state_tape import DEFAULT_SYMBOLS, DEFAULT_VENUES, book_event_from_record
from .synchronized import SynchronizedBookEngine


EXTERNAL_SORT_CHUNK_ROWS = 200_000


def _paths(root: Path, venue: str, symbol: str):
    return (root / "raw" / "book_events" / ("venue=" + venue) / ("symbol=" + symbol)).glob(
        "date=*/events.jsonl"
    )


def _receive_ns_from_line(line: str) -> Optional[int]:
    """Extract receive_ts_ns without decoding the full JSON object."""
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
            if j > i and not (j == i + 1 and line[i] == "-"):
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


def _iter_filtered_lines(paths: Sequence[Path], start_ns: int, stop_ns: int):
    """Yield in-window JSON lines in physical file order.

    Never break merely because one row exceeds stop_ns. A file may contain a
    bounded physical inversion after buffered flushes or interrupted sessions.
    """
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                receive_ns = _receive_ns_from_line(line)
                if receive_ns is None:
                    continue
                if receive_ns < int(start_ns) or receive_ns > int(stop_ns):
                    continue
                yield int(receive_ns), line


def _receive_ordered(paths: Sequence[Path], start_ns: int, stop_ns: int) -> bool:
    previous = None
    for receive_ns, _line in _iter_filtered_lines(paths, start_ns, stop_ns):
        if previous is not None and int(receive_ns) < int(previous):
            return False
        previous = int(receive_ns)
    return True


def _direct_record_iter(paths: Sequence[Path], start_ns: int, stop_ns: int) -> Iterator[BookEvent]:
    for _receive_ns, line in _iter_filtered_lines(paths, start_ns, stop_ns):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield book_event_from_record(row)


def _run_record_iter(path: Path) -> Iterator[BookEvent]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield book_event_from_record(row)


def _file_label(path: Path) -> str:
    venue = "?"
    symbol = "?"
    date = "?"
    for part in path.parts:
        if part.startswith("venue="):
            venue = part.split("=", 1)[1]
        elif part.startswith("symbol="):
            symbol = part.split("=", 1)[1]
        elif part.startswith("date="):
            date = part.split("=", 1)[1]
    return "%s/%s/%s" % (venue, symbol, date)


def _partition_label(paths: Sequence[Path]) -> str:
    if not paths:
        return "unknown"
    label = _file_label(paths[0]).split("/")
    return "/".join(label[:2])


def _external_sorted_record_iter(
    paths: Sequence[Path],
    start_ns: int,
    stop_ns: int,
    chunk_rows: int = EXTERNAL_SORT_CHUNK_ROWS,
) -> Iterator[BookEvent]:
    """External stable receive-time sort for physically disordered file(s)."""
    chunk_rows = max(1, int(chunk_rows))
    label = _file_label(paths[0]) if len(paths) == 1 else _partition_label(paths)
    with tempfile.TemporaryDirectory(prefix="mpv3-book-sort-") as tmp_name:
        tmp = Path(tmp_name)
        runs = []
        chunk = []
        run_id = 0
        selected_rows = 0

        def flush_chunk() -> None:
            nonlocal chunk, run_id
            if not chunk:
                return
            chunk.sort(key=lambda x: int(x[0]))
            run_path = tmp / ("run-%05d.jsonl" % run_id)
            with run_path.open("w", encoding="utf-8") as out:
                for _receive_ns, line in chunk:
                    out.write(line)
                    out.write("\n")
            runs.append(run_path)
            run_id += 1
            if run_id == 1 or run_id % 10 == 0:
                print(
                    "[state-tape] %s reorder spill runs=%d rows=%d"
                    % (label, run_id, selected_rows),
                    flush=True,
                )
            chunk = []

        for receive_ns, line in _iter_filtered_lines(paths, start_ns, stop_ns):
            selected_rows += 1
            chunk.append((int(receive_ns), line))
            if len(chunk) >= chunk_rows:
                flush_chunk()
        flush_chunk()

        if not runs:
            return
        print(
            "[state-tape] %s external reorder ready runs=%d rows=%d"
            % (label, len(runs), selected_rows),
            flush=True,
        )
        streams = [_run_record_iter(path) for path in runs]
        for event in heapq.merge(*streams, key=lambda x: int(x.receive_ts_ns)):
            yield event


def _ordered_file_iter(path: Path, start_ns: int, stop_ns: int) -> Iterator[BookEvent]:
    """Prove one physical JSONL file ordered, or repair only that file."""
    label = _file_label(path)
    print("[state-tape] validate receive-order %s" % label, flush=True)
    if _receive_ordered((path,), start_ns, stop_ns):
        print("[state-tape] %s ordered -> direct replay" % label, flush=True)
        for event in _direct_record_iter((path,), start_ns, stop_ns):
            yield event
        return
    print("[state-tape] %s inversion detected -> external reorder" % label, flush=True)
    for event in _external_sorted_record_iter((path,), start_ns, stop_ns):
        yield event


def _record_iter(paths: Iterable[Path], start_ns: int, stop_ns: int) -> Iterator[BookEvent]:
    """Yield one venue/symbol partition in causal receive-time order.

    Normalized storage is partitioned by *event date*, while causality is defined
    by *receive time*. A long run crossing UTC midnight can therefore have valid
    receive-time overlap across adjacent date files. Each physical date file is
    validated/repaired independently, then date files are merged by receive time
    instead of being concatenated lexicographically.
    """
    ordered_paths = tuple(sorted(path for path in paths if path.is_file()))
    if not ordered_paths:
        return
    streams = [_ordered_file_iter(path, start_ns, stop_ns) for path in ordered_paths]
    for event in heapq.merge(*streams, key=lambda x: int(x.receive_ts_ns)):
        yield event


def iter_merged_book_events(
    root: str,
    start_ns: int,
    stop_ns: int,
    venues: Sequence[str] = DEFAULT_VENUES,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
) -> Iterator[BookEvent]:
    """K-way merge venue/symbol partitions by local receive time."""
    base = Path(root)
    streams = []
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        for symbol_raw in symbols:
            symbol = str(symbol_raw).upper()
            streams.append(_record_iter(_paths(base, venue, symbol), start_ns, stop_ns))
    return heapq.merge(*streams, key=lambda x: int(x.receive_ts_ns))


def _grid(start_ns: int, stop_ns: int, cadence_ms: int):
    step_ns = int(cadence_ms) * 1_000_000
    if step_ns <= 0:
        raise ValueError("cadence_ms must be positive")
    t = int(start_ns) + step_ns
    while t <= int(stop_ns):
        yield t
        t += step_ns


def _state_row(
    engine: SynchronizedBookEngine,
    symbol: str,
    asof_ns: int,
    cadence_ms: int,
    venues: Sequence[str],
    max_receive_age_ms: float,
    max_transport_lag_ms: float,
    max_sync_span_ms: float,
    previous_deep_snapshots: Dict[Tuple[str, str], object],
    previous_price_snapshots: Dict[Tuple[str, str], object],
) -> Dict[str, object]:
    strict_state = engine.state(
        symbol=symbol,
        asof_ns=asof_ns,
        required_venues=venues,
        require_deep=True,
        max_receive_age_ms=max_receive_age_ms,
        max_transport_lag_ms=max_transport_lag_ms,
        max_sync_span_ms=max_sync_span_ms,
        min_venues=len(venues),
    )
    price_state = engine.state(
        symbol=symbol,
        asof_ns=asof_ns,
        required_venues=venues,
        require_deep=False,
        max_receive_age_ms=None,
        max_transport_lag_ms=max_transport_lag_ms,
        max_sync_span_ms=None,
        min_venues=len(venues),
    )
    row: Dict[str, object] = {
        "asof_ns": int(asof_ns),
        "symbol": symbol,
        "cadence_ms": int(cadence_ms),
        "ready": bool(strict_state.ready),
        "strict_ready": bool(strict_state.ready),
        "sync_span_ms": float(strict_state.sync_span_ms),
        "fair_value": float(strict_state.fair_value),
        "dispersion_bps": float(strict_state.dispersion_bps),
        "venues_used": ",".join(strict_state.venues_used),
        "venues_missing": ",".join(strict_state.venues_missing),
        "reasons": ",".join(strict_state.reasons),
        "price_ready": bool(price_state.ready),
        "price_sync_span_ms": float(price_state.sync_span_ms),
        "price_fair_value": float(price_state.fair_value),
        "price_dispersion_bps": float(price_state.dispersion_bps),
        "price_venues_used": ",".join(price_state.venues_used),
        "price_venues_missing": ",".join(price_state.venues_missing),
        "price_reasons": ",".join(price_state.reasons),
    }
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        prefix = venue + "__"
        book = engine.books.get((venue, symbol))
        if book is None:
            continue

        deep = book.deep_snapshot()
        if deep is not None and deep.available_ts_ns <= asof_ns:
            deep_age_ms = float((asof_ns - deep.available_ts_ns) / 1e6)
            deep_fresh = (
                deep_age_ms <= float(max_receive_age_ms)
                and deep.transport_lag_ms <= float(max_transport_lag_ms)
            )
            for name, value in book_feature_vector(deep).items():
                row[prefix + name] = float(value)
            for name, value in book.fragmentation_features().items():
                row[prefix + name] = float(value)
            row[prefix + "receive_age_ms"] = deep_age_ms
            row[prefix + "depth_receive_age_ms"] = deep_age_ms
            row[prefix + "transport_lag_ms"] = float(deep.transport_lag_ms)
            row[prefix + "depth_transport_lag_ms"] = float(deep.transport_lag_ms)
            row[prefix + "depth_fresh"] = bool(deep_fresh)
            row[prefix + "dislocation_bps"] = float(
                strict_state.dislocation_bps.get(venue, float("nan"))
            )
            row[prefix + "weight"] = float(strict_state.weights.get(venue, 0.0))
            prev_key = (venue, symbol)
            previous = previous_deep_snapshots.get(prev_key)
            row[prefix + "ofi_l1_grid"] = (
                float(top_of_book_ofi(previous, deep)) if previous is not None else float("nan")
            )
            previous_deep_snapshots[prev_key] = deep

        price = book.price_snapshot()
        if price is not None and price.available_ts_ns <= asof_ns:
            pf = book_feature_vector(price)
            for name in (
                "best_bid", "best_ask", "mid", "spread_bps", "microprice",
                "microprice_offset_bps", "queue_imbalance_l1",
            ):
                row[prefix + "price_" + name] = float(pf[name])
            row[prefix + "price_receive_age_ms"] = float(
                (asof_ns - price.available_ts_ns) / 1e6
            )
            row[prefix + "price_transport_lag_ms"] = float(price.transport_lag_ms)
            row[prefix + "price_dislocation_bps"] = float(
                price_state.dislocation_bps.get(venue, float("nan"))
            )
            row[prefix + "price_weight"] = float(price_state.weights.get(venue, 0.0))
            prev_key = (venue, symbol)
            previous_price = previous_price_snapshots.get(prev_key)
            row[prefix + "price_ofi_l1_grid"] = (
                float(top_of_book_ofi(previous_price, price))
                if previous_price is not None else float("nan")
            )
            previous_price_snapshots[prev_key] = price
    return row


def build_streaming_state_tape(
    events: Iterator[BookEvent],
    start_ns: int,
    stop_ns: int,
    cadence_ms: int,
    out_dir: str,
    venues: Sequence[str] = DEFAULT_VENUES,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    max_receive_age_ms: float = 1500.0,
    max_transport_lag_ms: float = 5000.0,
    max_sync_span_ms: float = 1000.0,
    chunk_rows: int = 50000,
) -> Dict[str, object]:
    """Build a causal tape with bounded memory and chunked Parquet output."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("part-*.parquet"):
        old.unlink()

    engine = SynchronizedBookEngine()
    previous_deep: Dict[Tuple[str, str], object] = {}
    previous_price: Dict[Tuple[str, str], object] = {}
    current: Optional[BookEvent] = next(events, None)
    event_count = 0
    rows: list = []
    part = 0
    total_rows = 0
    strict_ready_rows = 0
    price_ready_rows = 0
    depth_fresh_counts = {str(v).lower(): 0 for v in venues}
    depth_seen_counts = {str(v).lower(): 0 for v in venues}

    def flush() -> None:
        nonlocal rows, part
        if not rows:
            return
        frame = pd.DataFrame(rows)
        path = target / ("part-%05d.parquet" % part)
        frame.to_parquet(path, index=False)
        print(
            "[state-tape] wrote %s rows_total=%d book_events=%d"
            % (path.name, total_rows, event_count),
            flush=True,
        )
        part += 1
        rows = []

    for asof_ns in _grid(start_ns, stop_ns, cadence_ms):
        while current is not None and int(current.receive_ts_ns) <= int(asof_ns):
            engine.ingest(current)
            event_count += 1
            current = next(events, None)
        for symbol_raw in symbols:
            symbol = str(symbol_raw).upper()
            row = _state_row(
                engine, symbol, asof_ns, cadence_ms, venues,
                max_receive_age_ms, max_transport_lag_ms, max_sync_span_ms,
                previous_deep, previous_price,
            )
            total_rows += 1
            strict_ready_rows += int(bool(row.get("strict_ready", False)))
            price_ready_rows += int(bool(row.get("price_ready", False)))
            for venue_raw in venues:
                venue = str(venue_raw).lower()
                key = venue + "__depth_fresh"
                if key in row:
                    depth_seen_counts[venue] += 1
                    depth_fresh_counts[venue] += int(bool(row[key]))
            rows.append(row)
            if len(rows) >= int(chunk_rows):
                flush()
    flush()
    (target / "_SUCCESS").write_text("ok\n")
    return {
        "cadence_ms": int(cadence_ms),
        "rows": int(total_rows),
        "parts": int(part),
        "book_events_consumed": int(event_count),
        "strict_ready_fraction": float(strict_ready_rows / total_rows) if total_rows else 0.0,
        "price_ready_fraction": float(price_ready_rows / total_rows) if total_rows else 0.0,
        "depth_fresh_fraction": {
            venue: (
                float(depth_fresh_counts[venue] / depth_seen_counts[venue])
                if depth_seen_counts[venue] else 0.0
            )
            for venue in sorted(depth_fresh_counts)
        },
    }
