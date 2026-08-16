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


def _iter_filtered_rows(paths: Sequence[Path], start_ns: int, stop_ns: int):
    """Yield in-window JSON rows in physical file order.

    Never break merely because one row exceeds stop_ns. Append-only files can
    contain bounded physical disorder after buffered flushes or interrupted /
    restarted collectors, so a later row may still belong to the requested
    receive-time window.
    """
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                receive_ns = int(row.get("receive_ts_ns", 0) or 0)
                if receive_ns < int(start_ns) or receive_ns > int(stop_ns):
                    continue
                yield receive_ns, line, row


def _partition_receive_ordered(paths: Sequence[Path], start_ns: int, stop_ns: int) -> bool:
    """Check the only ordering invariant required by the causal replay engine."""
    previous = None
    for receive_ns, _line, _row in _iter_filtered_rows(paths, start_ns, stop_ns):
        if previous is not None and int(receive_ns) < int(previous):
            return False
        previous = int(receive_ns)
    return True


def _direct_record_iter(paths: Sequence[Path], start_ns: int, stop_ns: int) -> Iterator[BookEvent]:
    for _receive_ns, _line, row in _iter_filtered_rows(paths, start_ns, stop_ns):
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


def _external_sorted_record_iter(
    paths: Sequence[Path],
    start_ns: int,
    stop_ns: int,
    chunk_rows: int = EXTERNAL_SORT_CHUNK_ROWS,
) -> Iterator[BookEvent]:
    """External stable receive-time sort for a physically disordered partition.

    The long-run collector is append-only. Multiple buffered flushes, interrupted
    sessions, or process restarts can make physical JSONL row order differ from
    receive-time order even though every row carries a valid receive_ts_ns. The
    replay must restore receive order before events reach SynchronizedBookEngine;
    relaxing the engine's monotonicity check would corrupt point-in-time causality.

    Sorting is bounded-memory: only `chunk_rows` raw lines are retained at once,
    sorted stably by receive_ts_ns, spilled to temporary runs, then merged.
    """
    chunk_rows = max(1, int(chunk_rows))
    with tempfile.TemporaryDirectory(prefix="mpv3-book-sort-") as tmp_name:
        tmp = Path(tmp_name)
        runs = []
        chunk = []
        run_id = 0

        def flush_chunk() -> None:
            nonlocal chunk, run_id
            if not chunk:
                return
            # Python sort is stable; equal receive timestamps retain source order.
            chunk.sort(key=lambda x: int(x[0]))
            run_path = tmp / ("run-%05d.jsonl" % run_id)
            with run_path.open("w", encoding="utf-8") as out:
                for _receive_ns, line in chunk:
                    out.write(line)
                    out.write("\n")
            runs.append(run_path)
            run_id += 1
            chunk = []

        for receive_ns, line, _row in _iter_filtered_rows(paths, start_ns, stop_ns):
            chunk.append((int(receive_ns), line))
            if len(chunk) >= chunk_rows:
                flush_chunk()
        flush_chunk()

        if not runs:
            return

        streams = [_run_record_iter(path) for path in runs]
        for event in heapq.merge(*streams, key=lambda x: int(x.receive_ts_ns)):
            yield event


def _record_iter(paths: Iterable[Path], start_ns: int, stop_ns: int) -> Iterator[BookEvent]:
    """Yield one venue/symbol partition in causal receive-time order.

    Fast path: physically ordered partitions are read directly after a validation
    pass. Slow path: only partitions with a receive-time inversion are externally
    sorted with bounded memory. This keeps the scientific invariant strict while
    avoiding unnecessary spill files for healthy partitions.
    """
    ordered_paths = tuple(sorted(path for path in paths if path.is_file()))
    if not ordered_paths:
        return
    if _partition_receive_ordered(ordered_paths, start_ns, stop_ns):
        for event in _direct_record_iter(ordered_paths, start_ns, stop_ns):
            yield event
        return
    for event in _external_sorted_record_iter(ordered_paths, start_ns, stop_ns):
        yield event


def iter_merged_book_events(
    root: str,
    start_ns: int,
    stop_ns: int,
    venues: Sequence[str] = DEFAULT_VENUES,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
) -> Iterator[BookEvent]:
    """K-way merge venue/symbol partitions by local receive time.

    Each input stream is first proven receive-ordered (or externally reordered),
    so the merged iterator can safely feed the fail-closed causal engine.
    """
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
        frame.to_parquet(target / ("part-%05d.parquet" % part), index=False)
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
