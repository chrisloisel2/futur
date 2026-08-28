import json

from market_physics_v3.collectors.qualification import qualify_venue


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_old_dead_letter_does_not_poison_new_clean_smoke(tmp_path):
    root = tmp_path / "data"
    health_dir = tmp_path / "health"
    health_dir.mkdir(parents=True)
    start = 2_000_000_000
    stop = 3_000_000_000

    for kind in ("book_events", "trades", "derivatives"):
        _write(
            root / "raw" / kind / "venue=okx" / "symbol=BTCUSDT" /
            "date=2026-08-15" / "events.jsonl",
            [{"receive_ts_ns": start + 10}],
        )
    _write(
        root / "raw_wire" / "venue=okx" / "date=2026-08-15" / "messages.jsonl",
        [{"receive_ts_ns": start + 20}],
    )
    # Historical failed smoke: retained forever, but outside the current run.
    _write(
        root / "dead_letters" / "venue=okx" / "date=2026-08-15" / "errors.jsonl",
        [{"receive_ts_ns": start - 1000, "error": "old gap"}],
    )
    health = {
        "venue": "okx",
        "connected": False,
        "clean_shutdown": True,
        "started_ns": start,
        "stopped_ns": stop,
        "messages": 500,
        "events": 1000,
        "book_events": 800,
        "trade_events": 100,
        "derivative_events": 100,
        "parse_errors": 0,
        "sequence_gaps": 0,
        "subscription_acks": 20,
        "subscription_errors": 0,
        "reconnects": 0,
        "last_exception": None,
        "last_receive_ns": stop - 100,
        "last_event_ns": stop - 200,
        "idle_ms": 100.0,
    }
    (health_dir / "okx.json").write_text(json.dumps(health))

    report = qualify_venue("okx", str(root), str(health_dir))
    assert report["qualified"]
    assert report["dead_letter_files"] == []

    # A current-run dead letter must fail closed.
    dl = root / "dead_letters" / "venue=okx" / "date=2026-08-15" / "errors.jsonl"
    with dl.open("a") as fh:
        fh.write(json.dumps({"receive_ts_ns": start + 50, "error": "current gap"}) + "\n")
    report = qualify_venue("okx", str(root), str(health_dir))
    assert not report["qualified"]
    assert "nonempty_dead_letters" in report["reasons"]
