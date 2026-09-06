from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


def _nonempty_files(paths):
    out = []
    for path in paths:
        try:
            if path.is_file() and path.stat().st_size > 0:
                out.append(str(path))
        except FileNotFoundError:
            pass
    return out


def _files_with_rows_in_window(paths, start_ns, stop_ns):
    """Return files containing at least one record inside [start_ns, stop_ns].

    Used for new-format health reports so append-only evidence from a previous
    failed smoke remains auditable without poisoning all future qualifications.
    """
    if not start_ns or not stop_ns:
        return _nonempty_files(paths)
    out = []
    lo, hi = int(start_ns), int(stop_ns)
    for path in paths:
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            found = False
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = int(row.get("receive_ts_ns", 0) or 0)
                    if lo <= ts <= hi:
                        found = True
                        break
            if found:
                out.append(str(path))
        except FileNotFoundError:
            pass
    return out


def qualify_venue(
    venue: str,
    root: str = "data/market_physics_v3",
    health_dir: str = "reports/market_physics_v3/health",
    min_messages: int = 100,
    min_events: int = 100,
    max_idle_ms: float = 5000.0,
) -> Dict[str, object]:
    """Qualify a live venue as EVENT_LEVEL only from observed evidence.

    The gate is intentionally strict. It requires healthy subscription/runtime
    counters, raw-wire capture, normalized book/trade/derivative partitions,
    causal clocks and zero dead letters from the smoke being qualified.

    New health reports additionally distinguish events that were already stale
    when received. Those events remain persisted for replay/audit, but a venue
    is considered live only when each required event family has fresh evidence.
    Hyperliquid requires this telemetry explicitly because its reconnect/startup
    behavior can deliver older trades before the current streaming flow.
    """
    venue = str(venue).lower().strip()
    root_path = Path(root)
    health_path = Path(health_dir) / (venue + ".json")
    reasons: List[str] = []

    if not health_path.exists():
        return {
            "venue": venue,
            "qualified": False,
            "status": "UNKNOWN",
            "reasons": ["missing_health_file"],
        }

    health = json.loads(health_path.read_text())
    if int(health.get("messages", 0)) < int(min_messages):
        reasons.append("insufficient_messages")
    if int(health.get("events", 0)) < int(min_events):
        reasons.append("insufficient_events")
    if int(health.get("parse_errors", 0)) != 0:
        reasons.append("parse_errors")
    if int(health.get("sequence_gaps", 0)) != 0:
        reasons.append("sequence_gaps")
    if int(health.get("subscription_errors", 0)) != 0:
        reasons.append("subscription_errors")
    if int(health.get("subscription_acks", 0)) < 1:
        reasons.append("missing_subscription_ack")
    if int(health.get("reconnects", 0)) != 0:
        reasons.append("reconnects")
    if health.get("last_exception") not in (None, ""):
        reasons.append("last_exception")

    idle = health.get("idle_ms")
    if idle is None or float(idle) > float(max_idle_ms):
        reasons.append("stale_or_missing_last_receive")

    last_receive = int(health.get("last_receive_ns", 0) or 0)
    last_event = int(health.get("last_event_ns", 0) or 0)
    if last_receive <= 0 or last_event <= 0:
        reasons.append("missing_event_clocks")
    elif last_event > last_receive:
        reasons.append("event_clock_after_receive_clock")

    if "clean_shutdown" in health and not bool(health.get("clean_shutdown")):
        reasons.append("unclean_shutdown")

    type_counts = {
        "book_events": int(health.get("book_events", 0) or 0),
        "trades": int(health.get("trade_events", 0) or 0),
        "derivatives": int(health.get("derivative_events", 0) or 0),
    }
    has_typed_health = any(
        key in health for key in ("book_events", "trade_events", "derivative_events")
    )

    fresh_keys = {
        "book_events": "fresh_book_events",
        "trades": "fresh_trade_events",
        "derivatives": "fresh_derivative_events",
    }
    stale_keys = {
        "book_events": "stale_book_events",
        "trades": "stale_trade_events",
        "derivatives": "stale_derivative_events",
    }
    has_freshness_health = "fresh_event_max_lag_ms" in health and all(
        key in health for key in fresh_keys.values()
    )
    if venue == "hyperliquid" and not has_freshness_health:
        reasons.append("missing_freshness_telemetry")
    if has_freshness_health:
        if int(health.get("fresh_events", 0) or 0) < int(min_events):
            reasons.append("insufficient_fresh_events")
        for kind, key in fresh_keys.items():
            if int(health.get(key, 0) or 0) <= 0:
                reasons.append("missing_fresh_%s" % kind)

    start_ns = int(health.get("started_ns", 0) or 0)
    stop_ns = int(health.get("stopped_ns", 0) or 0)
    has_run_window = start_ns > 0 and stop_ns >= start_ns

    normalized_files = {}
    for kind in ("book_events", "trades", "derivatives"):
        candidates = list(
            (root_path / "raw" / kind / ("venue=" + venue)).glob("**/events.jsonl")
        )
        files = (
            _files_with_rows_in_window(candidates, start_ns, stop_ns)
            if has_run_window else _nonempty_files(candidates)
        )
        normalized_files[kind] = files
        if has_typed_health:
            if type_counts[kind] <= 0:
                reasons.append("missing_%s" % kind)
            if has_run_window and type_counts[kind] > 0 and not files:
                reasons.append("missing_current_%s_storage" % kind)
        elif not files:
            # Legacy Bybit smoke: no typed counters/start-stop window existed yet.
            reasons.append("missing_%s" % kind)

    raw_candidates = list(
        (root_path / "raw_wire" / ("venue=" + venue)).glob("**/messages.jsonl")
    )
    raw_files = (
        _files_with_rows_in_window(raw_candidates, start_ns, stop_ns)
        if has_run_window else _nonempty_files(raw_candidates)
    )
    if not raw_files:
        reasons.append("missing_raw_wire")

    dead_candidates = list(
        (root_path / "dead_letters" / ("venue=" + venue)).glob("**/errors.jsonl")
    )
    dead_files = (
        _files_with_rows_in_window(dead_candidates, start_ns, stop_ns)
        if has_run_window else _nonempty_files(dead_candidates)
    )
    if dead_files:
        reasons.append("nonempty_dead_letters")

    qualified = len(reasons) == 0
    freshness = {
        "threshold_ms": health.get("fresh_event_max_lag_ms"),
        "fresh_events": int(health.get("fresh_events", 0) or 0),
        "stale_events": int(health.get("stale_events", 0) or 0),
        "fresh_by_type": {
            kind: int(health.get(key, 0) or 0) for kind, key in fresh_keys.items()
        },
        "stale_by_type": {
            kind: int(health.get(key, 0) or 0) for kind, key in stale_keys.items()
        },
        "max_lag_ms": {
            "book_events": health.get("max_book_lag_ms"),
            "trades": health.get("max_trade_lag_ms"),
            "derivatives": health.get("max_derivative_lag_ms"),
        },
    }
    return {
        "venue": venue,
        "qualified": qualified,
        "status": "EVENT_LEVEL" if qualified else "UNKNOWN",
        "reasons": sorted(set(reasons)),
        "health": health,
        "type_counts": type_counts,
        "freshness": freshness,
        "run_window": {"started_ns": start_ns, "stopped_ns": stop_ns},
        "raw_files": raw_files,
        "normalized_files": normalized_files,
        "dead_letter_files": dead_files,
    }


def promote_manifest(report: Dict[str, object], manifest_path: str) -> bool:
    """Promote only the venue row and only when qualification passed."""
    if not report.get("qualified"):
        return False

    import pandas as pd

    path = Path(manifest_path)
    df = pd.read_csv(path).fillna("")
    if "feed" not in df.columns or "status" not in df.columns:
        raise ValueError("manifest must contain feed,status")

    venue = str(report["venue"]).lower()
    mask = df["feed"].astype(str).str.lower() == venue
    if not mask.any():
        raise ValueError("manifest has no venue row: %s" % venue)

    df.loc[mask, "status"] = "EVENT_LEVEL"
    if "notes" in df.columns:
        health = report.get("health", {})
        note = (
            "qualified from live smoke messages=%s events=%s "
            "parse_errors=0 sequence_gaps=0 dead_letters=0"
            % (health.get("messages", "?"), health.get("events", "?"))
        )
        if "fresh_event_max_lag_ms" in health:
            note += " fresh_events=%s stale_events=%s fresh_lag_ms<=%s" % (
                health.get("fresh_events", "?"),
                health.get("stale_events", "?"),
                health.get("fresh_event_max_lag_ms", "?"),
            )
        df.loc[mask, "notes"] = note
    df.to_csv(path, index=False)
    return True
