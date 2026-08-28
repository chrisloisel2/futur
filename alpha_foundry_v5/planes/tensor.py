from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Iterator, Mapping, Optional, Sequence, Tuple

import pandas as pd

from .derivatives import DerivativesPlane
from .event_trade import EventTradePlane
from .replay import iter_merged_records, merge_modal_streams


DEFAULT_VENUES = ("binance", "bybit", "okx", "hyperliquid")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def load_base_summary(base_tape: str) -> Dict[str, object]:
    path = Path(base_tape) / "SUMMARY.json"
    if not path.is_file():
        raise ValueError("base state tape must contain SUMMARY.json: %s" % path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    window = payload.get("window") or {}
    if int(window.get("started_ns", 0) or 0) <= 0 or int(window.get("stopped_ns", 0) or 0) <= 0:
        raise ValueError("base SUMMARY.json does not contain a valid window")
    return payload


def iter_base_rows(base_tape: str) -> Iterator[Dict[str, object]]:
    root = Path(base_tape)
    parts = sorted(root.glob("part-*.parquet"))
    if not parts:
        raise ValueError("no part-*.parquet under %s" % root)
    for part in parts:
        frame = pd.read_parquet(part)
        if "asof_ns" not in frame or "symbol" not in frame:
            raise ValueError("base tape part misses asof_ns/symbol: %s" % part)
        for row in frame.to_dict(orient="records"):
            yield row


def _reconstruct_available(asof_ns: int, age_ms) -> Optional[int]:
    if not _finite(age_ms):
        return None
    age = float(age_ms)
    if age < -1e-6:
        raise ValueError("negative receive age in base tape")
    available = int(round(int(asof_ns) - max(0.0, age) * 1e6))
    return min(int(asof_ns), available)


def add_book_availability_clocks(row: Dict[str, object], venues: Sequence[str]) -> None:
    asof_ns = int(row["asof_ns"])
    clocks = []
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        prefix = venue + "__"
        mappings = (
            ("price_receive_age_ms", "price_available_ts_ns"),
            ("depth_receive_age_ms", "depth_available_ts_ns"),
        )
        for age_name, clock_name in mappings:
            source = prefix + age_name
            if source not in row:
                continue
            available = _reconstruct_available(asof_ns, row.get(source))
            if available is None:
                continue
            row[prefix + clock_name] = int(available)
            clocks.append(int(available))
    if clocks:
        row["book__available_ts_ns"] = int(max(clocks))


def _merge_feature_dict(row: Dict[str, object], features: Mapping[str, object]) -> None:
    overlap = set(row).intersection(features)
    if overlap:
        raise ValueError("feature namespace collision: %s" % sorted(overlap)[:5])
    row.update(features)


def _add_cross_plane_features(
    row: Dict[str, object],
    previous_fair_value: Dict[str, float],
    venues: Sequence[str],
) -> None:
    symbol = str(row["symbol"]).upper()
    current_fv = row.get("price_fair_value")
    price_ret_bps = float("nan")
    if _finite(current_fv) and float(current_fv) > 0:
        previous = previous_fair_value.get(symbol)
        if previous is not None and previous > 0:
            price_ret_bps = 1e4 * math.log(float(current_fv) / float(previous))
        previous_fair_value[symbol] = float(current_fv)
    row["lev__price_ret_1grid_bps"] = price_ret_bps

    oi_change = row.get("deriv__median_oi_change_pct")
    if _finite(price_ret_bps) and _finite(oi_change) and abs(float(oi_change)) > 0:
        p = float(price_ret_bps)
        oi = float(oi_change)
        row["lev__new_long_leverage"] = float(p > 0 and oi > 0)
        row["lev__short_deleveraging"] = float(p > 0 and oi < 0)
        row["lev__new_short_leverage"] = float(p < 0 and oi > 0)
        row["lev__long_deleveraging"] = float(p < 0 and oi < 0)

    # Liquidation/depth is dimensionless and valid because both numerator and
    # denominator are quote notional. We deliberately do NOT compute liq/OI:
    # OI units differ by venue unless an instrument-specific normalizer proves
    # quote-notional equivalence.
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        prefix = venue + "__"
        liq = row.get(prefix + "liquidation_notional_30000ms")
        buy_depth = row.get(prefix + "buy_notional_10bps")
        sell_depth = row.get(prefix + "sell_notional_10bps")
        if _finite(liq) and _finite(buy_depth) and _finite(sell_depth):
            depth = max(0.0, float(buy_depth)) + max(0.0, float(sell_depth))
            if depth > 0:
                row[prefix + "liquidation_to_depth_30000ms"] = float(liq) / depth


def _validate_row_clocks(row: Mapping[str, object]) -> int:
    asof_ns = int(row["asof_ns"])
    checked = 0
    for name, value in row.items():
        if not str(name).endswith("_available_ts_ns"):
            continue
        if not _finite(value):
            continue
        checked += 1
        if int(value) > asof_ns:
            raise ValueError("PIT violation %s=%s > asof_ns=%s" % (name, value, asof_ns))
    return checked


def _write_chunk(root: Path, part: int, rows) -> None:
    if not rows:
        return
    frame = pd.DataFrame(rows)
    frame.to_parquet(root / ("part-%05d.parquet" % int(part)), index=False)


def build_multimodal_market_tensor(
    base_tape: str,
    market_root: str,
    out_dir: str,
    venues: Sequence[str] = DEFAULT_VENUES,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    chunk_rows: int = 50000,
) -> Dict[str, object]:
    summary = load_base_summary(base_tape)
    window = summary["window"]
    start_ns = int(window["started_ns"])
    stop_ns = int(window["stopped_ns"])
    cadence_ms = int((summary.get("streaming") or {}).get("cadence_ms", 0) or 0)
    if cadence_ms <= 0:
        # Fallback for legacy summaries; current tape directory is cadence=100ms.
        name = Path(base_tape).name
        if name.startswith("cadence=") and name.endswith("ms"):
            cadence_ms = int(name[len("cadence="):-2])
    if cadence_ms <= 0:
        raise ValueError("cannot determine base cadence")

    venues = tuple(str(v).lower() for v in venues)
    symbols = tuple(str(s).upper() for s in symbols)
    raw_stream = merge_modal_streams(
        iter_merged_records(market_root, "book_events", start_ns, stop_ns, venues, symbols),
        iter_merged_records(market_root, "trades", start_ns, stop_ns, venues, symbols),
        iter_merged_records(market_root, "derivatives", start_ns, stop_ns, venues, symbols),
    )
    next_record = next(raw_stream, None)
    event_trade = EventTradePlane(cadence_ms=cadence_ms, venues=venues, symbols=symbols)
    derivatives = DerivativesPlane(venues=venues, symbols=symbols)

    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.glob("part-*.parquet")) or (root / "_SUCCESS").exists():
        raise FileExistsError("output directory already contains a tensor: %s" % root)

    expected_step_ns = int(cadence_ms) * 1_000_000
    previous_grid_asof = None  # type: Optional[int]
    current_symbols = set()
    previous_fair_value = {}  # type: Dict[str, float]
    buffer = []
    part = 0
    total_rows = 0
    availability_checks = 0
    consumed = {"book_events": 0, "trades": 0, "derivatives": 0}
    columns = set()

    def flush() -> None:
        nonlocal buffer, part
        if not buffer:
            return
        _write_chunk(root, part, buffer)
        part += 1
        buffer = []

    for base_row in iter_base_rows(base_tape):
        row = dict(base_row)
        asof_ns = int(row["asof_ns"])
        symbol = str(row["symbol"]).upper()
        if symbol not in symbols:
            continue

        if previous_grid_asof is None or asof_ns != previous_grid_asof:
            if previous_grid_asof is not None:
                if current_symbols != set(symbols):
                    raise ValueError(
                        "base tape grid %s missing/extra symbols: %s"
                        % (previous_grid_asof, sorted(current_symbols))
                    )
                if asof_ns - int(previous_grid_asof) != expected_step_ns:
                    raise ValueError("base tape cadence gap at %s -> %s" % (previous_grid_asof, asof_ns))
            current_symbols = set()
            while next_record is not None and int(next_record["receive_ts_ns"]) <= asof_ns:
                kind = str(next_record.get("_source_kind", ""))
                if kind in consumed:
                    consumed[kind] += 1
                event_trade.ingest(next_record)
                derivatives.ingest(next_record)
                next_record = next(raw_stream, None)
            event_trade.advance(asof_ns)
            derivatives.advance(asof_ns)
            previous_grid_asof = asof_ns

        if symbol in current_symbols:
            raise ValueError("duplicate (asof_ns, symbol) in base tape")
        current_symbols.add(symbol)

        add_book_availability_clocks(row, venues)
        _merge_feature_dict(row, event_trade.state(asof_ns, symbol))
        _merge_feature_dict(row, derivatives.state(asof_ns, symbol))
        _add_cross_plane_features(row, previous_fair_value, venues)
        availability_checks += _validate_row_clocks(row)
        buffer.append(row)
        total_rows += 1
        columns.update(row.keys())
        if len(buffer) >= int(chunk_rows):
            flush()
            print(
                "[afv5-tensor] rows=%d parts=%d raw=%s"
                % (total_rows, part, json.dumps(consumed, sort_keys=True)),
                flush=True,
            )

    if previous_grid_asof is not None and current_symbols != set(symbols):
        raise ValueError("final base grid missing/extra symbols: %s" % sorted(current_symbols))
    flush()

    availability_columns = sorted(c for c in columns if str(c).endswith("_available_ts_ns"))
    report = {
        "base_tape": str(Path(base_tape)),
        "market_root": str(Path(market_root)),
        "window": {"started_ns": start_ns, "stopped_ns": stop_ns},
        "cadence_ms": int(cadence_ms),
        "venues": list(venues),
        "symbols": list(symbols),
        "rows": int(total_rows),
        "parts": int(part),
        "columns": int(len(columns)),
        "raw_records_consumed": consumed,
        "availability_clock_columns": availability_columns,
        "availability_checks": int(availability_checks),
        "proof_contract": {
            "book": "availability reconstructed from causal receive-age fields in base state tape",
            "event_trade": "features built only from records with receive_ts_ns <= asof_ns",
            "derivatives": "last-known state and rolling events use receive_ts_ns <= asof_ns",
            "oi_cross_venue": "changes/dispersion only; raw OI is never summed across venues",
            "liquidation_zero_policy": "rolling zeros only after first liquidation observation for venue/symbol",
        },
    }
    (root / "SUMMARY.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (root / "AVAILABILITY_CONTRACT.json").write_text(
        json.dumps(
            {
                "availability_suffix": "_available_ts_ns",
                "asof_column": "asof_ns",
                "rule": "every finite availability clock must be <= asof_ns",
                "clock_columns": availability_columns,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (root / "_SUCCESS").write_text("ok\n", encoding="utf-8")
    return report
