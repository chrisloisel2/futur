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
    counters, non-empty raw-wire capture, normalized book/trade/derivative
    partitions, causal clocks and zero non-empty dead-letter files.
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

    # New health files record clean shutdown explicitly. Older smoke files are
    # accepted if storage-level evidence below is complete and error-free.
    if "clean_shutdown" in health and not bool(health.get("clean_shutdown")):
        reasons.append("unclean_shutdown")

    type_counts = {
        "book_events": int(health.get("book_events", 0) or 0),
        "trades": int(health.get("trade_events", 0) or 0),
        "derivatives": int(health.get("derivative_events", 0) or 0),
    }
    normalized_files = {}
    for kind in ("book_events", "trades", "derivatives"):
        files = _nonempty_files(
            (root_path / kind / ("venue=" + venue)).glob("**/events.jsonl")
        )
        normalized_files[kind] = files
        if type_counts[kind] <= 0 and not files:
            reasons.append("missing_%s" % kind)

    raw_files = _nonempty_files(
        (root_path / "raw_wire" / ("venue=" + venue)).glob("**/messages.jsonl")
    )
    if not raw_files:
        reasons.append("missing_raw_wire")

    dead_files = _nonempty_files(
        (root_path / "dead_letters" / ("venue=" + venue)).glob("**/errors.jsonl")
    )
    if dead_files:
        reasons.append("nonempty_dead_letters")

    qualified = len(reasons) == 0
    return {
        "venue": venue,
        "qualified": qualified,
        "status": "EVENT_LEVEL" if qualified else "UNKNOWN",
        "reasons": sorted(set(reasons)),
        "health": health,
        "type_counts": type_counts,
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
        df.loc[mask, "notes"] = (
            "qualified from live smoke messages=%s events=%s "
            "parse_errors=0 sequence_gaps=0 dead_letters=0"
            % (health.get("messages", "?"), health.get("events", "?"))
        )
    df.to_csv(path, index=False)
    return True
